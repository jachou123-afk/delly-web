"""Explicit fixed-size synthetic NAS transport check, separate from tiny probe."""
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import re
import struct
import zlib

from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, validate_image
from product_image_storage import (checked_image_diagnostic, image_error_diagnostic,
                                   image_error_summary)
import synology_image_store as transport
from synology_image_store import NasConfig, SynologyImageStore

UPLOAD_TIMEOUT = getattr(transport, "UPLOAD_TIMEOUT", None)


WIDTH = HEIGHT = 1460
PNG_BYTES = 6_396_813
PNG_SHA256 = "099292f7b336d40b471612a43be6a685d938e834b6bdf4fc9bd94b07c30dd57d"
FILENAME = "NAS-LARGE-UPLOAD-FIXED-1460-V1.png"
FIELDS = ("base_url", "root", "library_id", "account", "password")
STEPS = frozenset({"settings", "fixture", "connect", "root", "stat", "existing_readback",
                   "upload", "reconnect", "readback_root", "readback"})
_JSON_STATE_KEY = "nas_large_upload_probe_receipt_v1"
_CONFIRM_KEY = "nas_large_upload_probe_confirm_v1"
_BUTTON_KEY = "nas_large_upload_probe_run_v1"


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _client_ready():
    return (type(UPLOAD_TIMEOUT) is tuple and len(UPLOAD_TIMEOUT) == 2
            and all(type(value) is int for value in UPLOAD_TIMEOUT)
            and UPLOAD_TIMEOUT == (60, 60))


@lru_cache(maxsize=1)
def _fixed_png_bytes():
    """A fixed all-black RGB PNG using deterministic stored deflate blocks."""
    raw = bytes(HEIGHT * (1 + WIDTH * 3))
    compressed = [b"\x78\x01"]
    for offset in range(0, len(raw), 65535):
        part = raw[offset:offset + 65535]
        final = offset + len(part) == len(raw)
        compressed.append(bytes([1 if final else 0])
                          + struct.pack("<HH", len(part), len(part) ^ 0xffff) + part)
    compressed.append(struct.pack(">I", zlib.adler32(raw) & 0xffffffff))

    def chunk(kind, value):
        return (struct.pack(">I", len(value)) + kind + value
                + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff))
    result = (b"\x89PNG\r\n\x1a\n"
              + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
              + chunk(b"IDAT", b"".join(compressed)) + chunk(b"IEND", b""))
    if len(result) != PNG_BYTES or hashlib.sha256(result).hexdigest() != PNG_SHA256:
        raise DispatchError("固定大型合成測試圖校驗失敗；未連線 NAS")
    return result


def large_test_asset():
    """No configurable contents, dimensions, filename, destination or user input."""
    return validate_image(_fixed_png_bytes(), FILENAME)


def _verified_bytes(returned, expected):
    clean = validate_image(asset_bytes(returned), FILENAME)
    if clean["sha256"] != PNG_SHA256 or asset_bytes(clean) != asset_bytes(expected):
        raise DispatchError("大型合成測試圖原檔讀回不一致；未確認通過")


def _checked_stat(value):
    fields = {"sha256", "exists", "size", "expected_size", "size_matches"}
    if (type(value) is not dict or set(value) != fields
            or type(value["sha256"]) is not str or value["sha256"] != PNG_SHA256
            or type(value["exists"]) is not bool
            or type(value["expected_size"]) is not int or value["expected_size"] != PNG_BYTES):
        raise DispatchError("大型測試圖檔案資訊不完整；未當成缺檔或開始上傳")
    if value["exists"] is False:
        if value["size"] is not None or value["size_matches"] is not False:
            raise DispatchError("大型測試圖缺檔資訊不完整；未開始上傳")
    elif (type(value["size"]) is not int or value["size"] != PNG_BYTES
          or value["size_matches"] is not True):
        raise DispatchError("大型測試圖既有檔案大小不符；未覆蓋或開始上傳")
    return value["exists"]


def _safe_receipt(value):
    """Reconstruct only fixed fields; no raw errors, metadata, paths or Secrets."""
    value = value if type(value) is dict else {}
    status = value.get("status")
    if type(status) is not str or status not in {"not_confirmed", "unconfigured", "failed", "passed"}:
        status = "failed"
    result = value.get("result")
    if type(result) is not str or result not in {"not_run", "failed", "upload_verified", "upload_not_tested"}:
        result = "failed"
    stamp = value.get("checked_at")
    if (type(stamp) is not str or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00", stamp)):
        stamp = _timestamp()
    output = {"schema": 1, "probe": "fixed_large_upload_v1", "checked_at": stamp,
              "status": status, "result": result,
              "upload_attempted": value.get("upload_attempted") is True,
              "client_ready": _client_ready(),
              "multipart_timeout_s": list(UPLOAD_TIMEOUT) if _client_ready() else None,
              "bytes": PNG_BYTES, "sha256": PNG_SHA256, "width": WIDTH, "height": HEIGHT,
              "passed": [v for v in value.get("passed", [])[:len(STEPS)] if type(v) is str and v in STEPS]
                        if type(value.get("passed")) is list else []}
    step = value.get("failed_step")
    if type(step) is str and step in STEPS:
        output["failed_step"] = step
    try:
        diagnostic = checked_image_diagnostic(value.get("diagnostic"))
    except (TypeError, ValueError):
        diagnostic = None
    if diagnostic is not None:
        output["diagnostic"] = diagnostic
    if status == "failed":
        output["reason"] = (image_error_summary(diagnostic) if diagnostic is not None
                            else "設定、權限或回應驗證未通過；未自動重試")
    return output


def run_large_upload_probe(read_section, store_factory=SynologyImageStore, *, confirmed=False):
    """One explicit call, stat-first, at most one Upload; never retries writes.

    stat_asset's validated missing result is the only authority to attempt the
    fixed object. Existing files are read-only checks, never upload acceptance.
    """
    receipt = {"checked_at": _timestamp(), "status": "not_confirmed", "result": "not_run",
               "upload_attempted": False, "passed": []}
    if confirmed is not True:
        return _safe_receipt(receipt)
    if not _client_ready():
        receipt.update(status="failed", result="failed", failed_step="settings")
        return _safe_receipt(receipt)
    step = "settings"
    try:
        section = read_section()
        if section is None:
            receipt["status"] = "unconfigured"
            return _safe_receipt(receipt)
        config = NasConfig(**{name: section[name] for name in FIELDS})
        receipt["passed"].append(step)
        step = "fixture"
        asset = large_test_asset()
        receipt["passed"].append(step)
        step = "connect"
        with store_factory(config) as store:
            receipt["passed"].append(step)
            step = "root"
            store.check_root()
            receipt["passed"].append(step)
            metadata = store._metadata(asset)
            step = "stat"
            exists = _checked_stat(store.stat_asset(metadata))
            receipt["passed"].append(step)
            if exists:
                step = "existing_readback"
                _verified_bytes(store.get_asset(metadata), asset)
            else:
                step = "upload"
                receipt["upload_attempted"] = True
                returned_metadata = store.put_asset(asset)
                if returned_metadata != metadata:
                    raise DispatchError("大型測試圖上傳回覆不符固定範圍；保存結果待確認")
            receipt["passed"].append(step)
        step = "reconnect"
        with store_factory(config) as store:
            receipt["passed"].append(step)
            step = "readback_root"
            store.check_root()
            receipt["passed"].append(step)
            step = "readback"
            _verified_bytes(store.get_asset(metadata), asset)
            receipt["passed"].append(step)
        receipt.update(status="passed", result="upload_not_tested" if exists else "upload_verified")
    except Exception as exc:
        receipt.update(status="failed", result="failed", failed_step=step,
                       diagnostic=image_error_diagnostic(exc))
    return _safe_receipt(receipt)


def render_large_upload_probe(read_section, store_factory=SynologyImageStore):
    """No settings/NAS IO until both confirmation and an explicit button click."""
    import streamlit as st

    st.caption("大型上傳診斷：固定 1460×1460 合成 PNG（6,396,813 bytes），"
               "只存專用 NAS 圖庫；保留測試檔，不綁商品、不改雲表。")
    if not _client_ready():
        st.warning("大型上傳客戶端政策尚未載入；本功能暫停，未讀設定或連線 NAS。")
        return
    confirmed = st.checkbox("我同意上傳並保留這張固定大型合成測試圖", key=_CONFIRM_KEY)
    stored = st.session_state.get(_JSON_STATE_KEY)
    clicked = st.button("執行一次大型上傳診斷", key=_BUTTON_KEY,
                        disabled=not confirmed or stored is not None)
    if clicked and confirmed and stored is None:
        with st.spinner("先查固定檔案，再驗證單次上傳及新連線讀回…"):
            result = run_large_upload_probe(read_section, store_factory, confirmed=True)
        stored = json.dumps(_safe_receipt(result), ensure_ascii=False, sort_keys=True, allow_nan=False)
        st.session_state[_JSON_STATE_KEY] = stored
        st.rerun()
    if stored is None:
        return
    try:
        result = _safe_receipt(json.loads(stored))
    except (TypeError, ValueError):
        result = _safe_receipt({"status": "failed", "result": "failed"})
    safe_json = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    st.session_state[_JSON_STATE_KEY] = safe_json
    if result["status"] == "passed" and result["result"] == "upload_verified":
        st.success("已執行一次大型上傳呼叫，且新 NAS 連線原檔讀回一致。")
    elif result["status"] == "passed" and result["result"] == "upload_not_tested":
        st.warning("固定測試檔已存在；本次僅讀回核對，未測試上傳，不能作為本次上傳驗收。")
    elif result["status"] == "unconfigured":
        st.warning("NAS 設定尚未就緒，未開始大型上傳。")
    else:
        st.warning("大型診斷未完成；未自動重試，安全診斷如下。")
    st.code(safe_json, language="json")
