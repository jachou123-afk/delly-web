"""Allowlisted Sheets 429 metadata; never export arbitrary remote error text."""
import re


FIELDS = frozenset({"request_method", "quota_metric", "quota_limit", "quota_reason", "google_message"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
METRICS = frozenset({"sheets.googleapis.com/read_requests", "sheets.googleapis.com/write_requests"})
REASONS = frozenset({"RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED", "RESOURCE_EXHAUSTED", "USER_RATE_LIMIT_EXCEEDED"})
LIMIT = re.compile(r"(?:Read|Write)RequestsPer(?:Minute|Day|Second)Per(?:User|Project)(?:PerProject)?")
REDACTED_MESSAGE = "Google Sheets 429；未辨識的訊息內容已遮罩。"
_MESSAGE = re.compile(
    r"Quota exceeded for quota metric '([^']+)' and limit '([^']+)' "
    r"of service 'sheets\.googleapis\.com' for consumer '[^']*'\.?"
)
_LABELS = {"Read requests", "Write requests"}
_LIMIT_LABELS = {f"{kind} requests per {period} per {scope}"
                 for kind in ("Read", "Write") for period in ("minute", "day", "second")
                 for scope in ("user", "project")}


def sanitized_message(value):
    """Keep only a recognized quota sentence; replace its entire consumer ID.

    Unrecognized messages are replaced, not heuristically scrubbed: they may
    contain URLs, credentials, emails, product data, or provider response text.
    """
    if type(value) is not str or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return REDACTED_MESSAGE
    match = _MESSAGE.fullmatch(value)
    if not match or match[1] not in _LABELS or match[2] not in _LIMIT_LABELS:
        return REDACTED_MESSAGE
    return (f"Quota exceeded for quota metric '{match[1]}' and limit '{match[2]}' "
            "of service 'sheets.googleapis.com' for consumer '[REDACTED]'.")


def checked_fields(value):
    """Validate already-sanitized receipt fields without normalizing bad data."""
    if not isinstance(value, dict) or set(value) - FIELDS:
        return None
    for key, text in value.items():
        if type(text) is not str:
            return None
        if ((key == "request_method" and text not in METHODS)
                or (key == "quota_metric" and text not in METRICS)
                or (key == "quota_limit" and (len(text) > 96 or not LIMIT.fullmatch(text)))
                or (key == "quota_reason" and text not in REASONS)
                or (key == "google_message" and text != REDACTED_MESSAGE
                    and sanitized_message(text) != text)):
            return None
    return dict(value)


def quota_error_fields(exc):
    """Read the native APIError's parsed JSON and prepared method only for 429."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 429 and getattr(exc, "code", None) != 429:
        return {}
    error = getattr(exc, "error", None)
    error = error if isinstance(error, dict) else {}
    result = {}
    message = sanitized_message(error.get("message"))
    method = getattr(getattr(response, "request", None), "method", None)
    if type(method) is str and method.upper() in METHODS:
        result["request_method"] = method.upper()
    details = error.get("details")
    observations = {key: set() for key in ("quota_metric", "quota_limit", "quota_reason")}
    for detail in details[:8] if isinstance(details, list) else []:
        if not isinstance(detail, dict) or detail.get("@type") != "type.googleapis.com/google.rpc.ErrorInfo":
            continue
        metadata = detail.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        candidate = {"quota_metric": metadata.get("quota_metric"),
                     "quota_limit": metadata.get("quota_limit"),
                     "quota_reason": detail.get("reason")}
        for key, text in candidate.items():
            if checked_fields({key: text}) is not None:
                observations[key].add(text)
    # Multiple contradictory ErrorInfo records stay unclassified.
    if all(len(values) <= 1 for values in observations.values()):
        result.update({key: next(iter(values)) for key, values in observations.items() if values})
    # Preserve old minimal diagnostics when neither native method nor safe
    # quota data exists. Never add arbitrary text merely to fill this field.
    if result or message != REDACTED_MESSAGE:
        result["google_message"] = message
    return result
