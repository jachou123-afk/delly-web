"""Shared strict allowlist for affirmative licensing markers.

The classifier intentionally does not try to enumerate every possible Chinese
negation. A clause is affirmative only when its complete shape matches one of
the formats already established by source data or saved Sheet names. All
other text is preserved verbatim and never creates a licensing claim.
"""

import re
import unicodedata


_CLAUSE_SEPARATOR = re.compile(r"([\r\n，,。.;；!！、]+)")

# Exact independent marker. 新品/爆品 is accepted only with the source's #
# separator; this deliberately rejects prose such as「需正版授權」.
_STANDALONE_LICENSE = re.compile(
    r"(?:(?:新品|爆品)\s*[#＃]\s*|[#＃]\s*)?"
    r"(?:正版\s*)?(?:授權|授权)",
    re.IGNORECASE,
)

# Exact source shape used by MN202450. MINISO is product-name content, so it
# remains after the structural marker is removed.
_MINISO_SOURCE_PREFIX = re.compile(
    r"爆品\s*[#＃]\s*正版\s*(?:授權|授权)\s*(?P<rest>MINISO)",
    re.IGNORECASE,
)

# Established saved-name/source prefix. Requiring 新品# makes it structurally
# distinct from an arbitrary sentence that happens to mention 正版授權.
_SHEET_NAME_PREFIX = re.compile(
    r"新品\s*[#＃]\s*正版\s*(?:授權|授权)\s+(?P<rest>\S.*)",
    re.IGNORECASE,
)

# Existing Sheet regression:「正版授權 新品測試商品」. The required 新品 at
# the start of the remainder keeps this legacy allowance narrow.
_LEGACY_TEST_NAME_PREFIX = re.compile(
    r"正版\s*(?:授權|授权)\s+(?P<rest>新品\S.*)",
    re.IGNORECASE,
)


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def _affirmative_remainder(clause):
    """Return ``(matched, remainder)`` for one complete allowlisted clause."""
    stripped = _normalize(clause).strip()
    if not stripped:
        return False, ""
    if _STANDALONE_LICENSE.fullmatch(stripped):
        return True, ""
    for pattern in (
        _MINISO_SOURCE_PREFIX,
        _SHEET_NAME_PREFIX,
        _LEGACY_TEST_NAME_PREFIX,
    ):
        match = pattern.fullmatch(stripped)
        if match:
            return True, match.group("rest")
    return False, ""


def is_standalone_license_marker(value):
    """Return True only for an exact, independent affirmative marker."""
    return bool(_STANDALONE_LICENSE.fullmatch(_normalize(value).strip()))


def has_affirmative_license_marker(value):
    """Find an allowlisted affirmative shape, clause by clause."""
    for part in _CLAUSE_SEPARATOR.split(str(value or "")):
        if not part or _CLAUSE_SEPARATOR.fullmatch(part):
            continue
        matched, _ = _affirmative_remainder(part)
        if matched:
            return True
    return False


def strip_affirmative_license_markers(value):
    """Remove only allowlisted markers; preserve every unrecognized clause."""
    parts = _CLAUSE_SEPARATOR.split(str(value or ""))
    result = []
    for part in parts:
        if not part or _CLAUSE_SEPARATOR.fullmatch(part):
            result.append(part)
            continue
        matched, remainder = _affirmative_remainder(part)
        if not matched:
            result.append(part)
            continue
        leading = part[:len(part) - len(part.lstrip())]
        trailing = part[len(part.rstrip()):]
        result.append(leading + remainder + trailing)
    return "".join(result)
