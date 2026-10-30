"""Explicit same-image source refresh; never uploads or writes image locations."""
from copy import deepcopy
import json
import re

from dispatch_manager import DispatchError, digest, now
from dispatch_storage import asset_bytes, decode_records, encode_record, validate_image
from image_limits import MAX_PRODUCT_ORIGINALS
from nas_dispatch_storage import NasDispatchStore
from product_images import PRODUCT_IMAGE_SHEET, subject_key
from quote_image_repair import _current_source, _snapshot_digest


MODE = "binding_source_refresh"
INVALID = "來源指紋刷新資料不完整；未讀寫圖庫"
CONFIRM = "請先逐張核對既有原圖仍是本款，並確認保留主圖與完整順序"
CONFLICT = "來源、公式、圖片版本或 NAS 位置已變更；未確認來源指紋刷新完成"
PENDING = "來源指紋寫入結果待確認；只能唯讀核對預備版本，不可重送"
REPLAY = "這份來源指紋刷新已送出；只能唯讀核對預備版本，不可重送"
NO_NAS = "來源指紋刷新需要已就緒的 NAS 原圖與完整位置；不補檔或發布索引"
BAD_ORIGINAL = "既有 NAS 原圖校驗不符；來源指紋刷新未確認完成"
ERRORS = {INVALID, CONFIRM, CONFLICT, PENDING, REPLAY, NO_NAS, BAD_ORIGINAL}
HEX = re.compile(r"[0-9a-f]{64}")
REV = re.compile(r"[0-9a-f]{32}")


def _exact(left, right):
    # JSON payloads distinguish bool/int/float; Python dict equality does not.
    return digest(left) == digest(right)


def _old_binding(plan):
    try:
        source, saved = plan["source"], plan["binding"]
        if (type(plan["schema"]) is not int or plan["schema"] != 1
                or plan["snapshot_digest"] != _snapshot_digest(source, plan["values"], plan["formulas"])
                or not isinstance(saved, dict) or type(saved.get("schema")) is not int or saved.get("schema") != 1
                or saved.get("identity") != source["identity"]
                or saved.get("subject") != subject_key(source)
                or not HEX.fullmatch(saved.get("source_hash", ""))
                or not HEX.fullmatch(source.get("source_hash", ""))
                or saved["source_hash"] == source["source_hash"]
                or not REV.fullmatch(saved.get("_revision", ""))
                or not all(isinstance(saved.get(k), str) and saved[k].strip() for k in ("actor", "origin", "at"))
                or not isinstance(saved.get("assets"), list)
                or not 1 <= len(saved["assets"]) <= MAX_PRODUCT_ORIGINALS
                or any(not isinstance(v, str) or not HEX.fullmatch(v) for v in saved["assets"])
                or len(set(saved["assets"])) != len(saved["assets"])):
            raise ValueError
        json.dumps(saved, allow_nan=False)
        return deepcopy(saved)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DispatchError(INVALID) from None


def can_refresh_binding_source(plan):
    """Local eligibility only; opening the UI never performs storage I/O."""
    try:
        _old_binding(plan)
        return True
    except DispatchError:
        return False


def _attempt_digest(attempt):
    return digest({k: v for k, v in attempt.items() if k not in {"digest", "state"}})


def prepare_source_refresh_attempt(plan, *, actor, confirmed_same_product=False):
    """Freeze one exact parent/target revision before any external I/O."""
    if confirmed_same_product is not True:
        raise DispatchError(CONFIRM)
    if not isinstance(actor, str) or not actor.strip():
        raise DispatchError(INVALID)
    old = _old_binding(plan)
    value = {k: deepcopy(v) for k, v in old.items() if k != "_revision"}
    value["source_hash"] = plan["source"]["source_hash"]
    records = encode_record(old["identity"], value, old["_revision"])
    attempt = {"schema": 1, "mode": MODE, "actor": actor.strip(), "plan": deepcopy(plan),
               "binding": {**value, "_revision": records[0][0]}, "records": records,
               "prepared_at": now(), "state": "prepared"}
    attempt["digest"] = _attempt_digest(attempt)
    return attempt


def _validate_attempt(attempt):
    try:
        if (not isinstance(attempt, dict) or set(attempt) != {
                "schema", "mode", "actor", "plan", "binding", "records", "prepared_at", "state", "digest"}
                or type(attempt["schema"]) is not int or attempt["schema"] != 1
                or attempt["mode"] != MODE or attempt["state"] not in {"prepared", "write_attempted"}
                or not isinstance(attempt["actor"], str) or not attempt["actor"].strip()
                or not isinstance(attempt["prepared_at"], str) or not attempt["prepared_at"]
                or attempt["digest"] != _attempt_digest(attempt)):
            raise ValueError
        old = _old_binding(attempt["plan"])
        expected = {k: deepcopy(v) for k, v in old.items() if k != "_revision"}
        expected["source_hash"] = attempt["plan"]["source"]["source_hash"]
        target = attempt["binding"]
        decoded = decode_records(attempt["records"])
        if (len(decoded) != 1 or not isinstance(target, dict)
                or not REV.fullmatch(target.get("_revision", ""))
                or target["_revision"] == old["_revision"]
                or not _exact({k: v for k, v in target.items() if k != "_revision"}, expected)
                or decoded[0]["id"] != target["_revision"]
                or decoded[0]["entity"] != old["identity"] or decoded[0]["parent"] != old["_revision"]
                or not _exact(decoded[0]["value"], expected)):
            raise ValueError
        return old
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DispatchError(INVALID) from None


def _store_ready(store):
    if not isinstance(store, NasDispatchStore) or store.nas_config is None or store.nas_config_error:
        raise DispatchError(NO_NAS)


def _guard(store, attempt, binding, locations=None):
    source = _current_source(store, attempt["plan"])
    plan = attempt["plan"]
    if _snapshot_digest(source, plan["values"], plan["formulas"]) != plan["snapshot_digest"]:
        raise DispatchError(CONFLICT)
    if not _exact(store.product_image_bindings().get(source["identity"]), binding):
        raise DispatchError(CONFLICT)
    if locations is not None and not _exact(store.image_locations(binding["assets"]), locations):
        raise DispatchError(CONFLICT)
    return source


def _locations(store, binding):
    result = deepcopy(store.image_locations(binding["assets"]))
    if set(result) != set(binding["assets"]):
        raise DispatchError(NO_NAS)
    return result


def _read_originals(store, ordered, locations, expected_bytes=None):
    originals = {}
    with store._nas() as nas:
        nas.check_root()
        for sha in ordered:
            loaded = nas.get_asset(locations[sha])
            raw = asset_bytes(loaded)
            clean = validate_image(raw, loaded["name"])
            if (clean["sha256"] != sha or loaded.get("_cloud_backup")
                    or len(raw) != locations[sha]["size"] or clean["mime"] != locations[sha]["mime"]
                    or (expected_bytes is not None and raw != expected_bytes[sha])):
                raise DispatchError(BAD_ORIGINAL)
            originals[sha] = raw
    return originals


def _receipt(attempt):
    old, binding = attempt["plan"]["binding"], attempt["binding"]
    result = {"schema": 1, "mode": MODE, "identity": old["identity"], "actor": attempt["actor"],
        "previous_binding": deepcopy(old), "binding": deepcopy(binding),
        "previous_binding_revision": old["_revision"], "binding_revision": binding["_revision"],
        "previous_source_hash": old["source_hash"], "source_hash": binding["source_hash"],
        "subject": old["subject"], "sha256": list(old["assets"]), "main_sha256": old["assets"][0],
        "snapshot_digest": attempt["plan"]["snapshot_digest"], "attempt_digest": attempt["digest"],
        "checked_at": now()}
    result["digest"] = digest(result)
    return {"結果": "已刷新", "identity": old["identity"], "receipt": result}


def refresh_binding_source(store, attempt):
    """Send one prepared binding revision once; a pending result is read-only."""
    old = _validate_attempt(attempt)
    if attempt["state"] != "prepared":
        raise DispatchError(REPLAY)
    _store_ready(store)
    _guard(store, attempt, old)
    locations = _locations(store, old)
    first = _read_originals(store, old["assets"], locations)
    _guard(store, attempt, old, locations)
    worksheet = store._sheet(PRODUCT_IMAGE_SHEET)
    if worksheet is None:
        raise DispatchError(CONFLICT)
    # This is the only write site. Persist consumed state BEFORE sending it.
    _guard(store, attempt, old, locations)
    attempt["state"] = "write_attempted"
    try:
        worksheet.append_rows(deepcopy(attempt["records"]), value_input_option="RAW", table_range="A:H")
    except Exception:
        raise DispatchError(PENDING) from None
    _guard(store, attempt, attempt["binding"], locations)
    _read_originals(store, old["assets"], locations, first)
    _guard(store, attempt, attempt["binding"], locations)
    return _receipt(attempt)


def verify_source_refresh_attempt(store, attempt):
    """Explicit recovery lookup; never creates/appends/retries any record."""
    old = _validate_attempt(attempt)
    _store_ready(store)
    source = _current_source(store, attempt["plan"])
    observed = store.product_image_bindings().get(source["identity"])
    if _exact(observed, old):
        _guard(store, attempt, old)
        result = {"結果": "未發布", "identity": source["identity"],
            "expected_binding_revision": attempt["binding"]["_revision"], "checked_at": now()}
        result["digest"] = digest(result)
        return result
    if not _exact(observed, attempt["binding"]):
        raise DispatchError(CONFLICT)
    locations = _locations(store, observed)
    _guard(store, attempt, observed, locations)
    first = _read_originals(store, old["assets"], locations)
    _guard(store, attempt, observed, locations)
    _read_originals(store, old["assets"], locations, first)
    _guard(store, attempt, observed, locations)
    return _receipt(attempt)


def safe_source_refresh_error(exc):
    from quote_image_repair_batch import safe_repair_error
    return str(exc) if isinstance(exc, DispatchError) and str(exc) in ERRORS else safe_repair_error(exc)
