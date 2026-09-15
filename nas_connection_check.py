"""One bounded deployment check; no product/Sheet/LINE access or public controls."""
from datetime import datetime, timezone
from io import BytesIO
import re
from threading import Lock

from PIL import Image

from dispatch_manager import DispatchError
from synology_image_store import NasConfig, SynologyImageStore


FIELDS = ("base_url", "root", "library_id", "account", "password")
STEPS = {"settings": "私密設定", "connect": "API／專用帳號登入",
         "root": "指定圖庫讀取", "upload": "測試圖寫入與校驗",
         "reconnect": "重新登入", "readback": "新連線讀回校驗"}


def test_asset():
    """Fixed synthetic fixture, never a user image; no external filename/input."""
    from dispatch_storage import validate_image
    output = BytesIO()
    Image.new("RGB", (8, 8), (34, 139, 34)).save(output, format="PNG")
    return validate_image(output.getvalue(), "NAS-CONNECTION-TEST.png")


def _reason(exc):
    # Return only known labels/numeric codes, never remote text or config values.
    if not isinstance(exc, DispatchError):
        return "非預期錯誤；詳細內容已隱藏，未自動重試"
    message = str(exc)
    code = re.fullmatch(r"NAS API 未完成（錯誤碼 ([0-9]{1,5}|未知)）；未重試登入或寫入", message)
    if code:
        return "NAS API 錯誤碼 " + code[1]
    http = re.fullmatch(r"NAS 讀寫失敗（HTTP ([0-9]{3})）；未視為缺圖", message)
    if http:
        return "NAS HTTP " + http[1]
    known = {
        "NAS 網路／憑證連線失敗；請確認連線，勿關閉憑證驗證": "網路或 TLS 憑證連線失敗",
        "NAS 入口已轉向；請重新確認網址，未跟隨或向轉向網址傳送帳密": "NAS 入口重新導向；已停止跟隨",
        "NAS 未回傳有效 API 資料，可能為登入或轉接頁": "入口未回傳 API 資料，可能是登入或轉接頁",
        "NAS 原圖保存結果待確認；未發布圖片索引，請只重試圖片核對": "測試圖寫入結果待確認；沒有發布商品索引",
        "NAS 原圖大小／校驗碼不符，停止使用": "讀回大小或 SHA-256 不一致",
        "找不到指定 NAS 圖庫資料夾或沒有存取權限": "找不到指定圖庫或沒有存取權",
    }
    return known.get(message, "設定、權限或回應驗證未通過；未自動重試")


def run_probe(read_section, store_factory=SynologyImageStore):
    """read_section reads ONLY nas_images. All exceptions become safe receipts."""
    step = "settings"
    passed = []
    receipt = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        section = read_section()
        if section is None:
            return {**receipt, "status": "unconfigured", "passed": []}
        # Never copy/iterate all Secrets or persist/hash the config in a UI cache.
        config = NasConfig(**{name: section[name] for name in FIELDS})
        if "請在Streamlit" in config.password or "這裡換成" in config.password:
            raise DispatchError("NAS 密碼仍為範本占位文字")
        passed.append(step)
        asset = test_asset()
        step = "connect"
        with store_factory(config) as store:
            passed.append(step)
            step = "root"
            store.check_root()
            passed.append(step)
            step = "upload"
            metadata = store.put_asset(asset)
            passed.append(step)
        step = "reconnect"
        with store_factory(config) as store:
            passed.append(step)
            step = "readback"
            returned = store.get_asset(metadata)
            from dispatch_storage import asset_bytes
            if asset_bytes(returned) != asset_bytes(asset):
                raise DispatchError("NAS 原圖大小／校驗碼不符，停止使用")
            passed.append(step)
        return {**receipt, "status": "passed", "passed": passed,
                "bytes": len(asset_bytes(asset)), "sha256": asset["sha256"]}
    except Exception as exc:
        return {**receipt, "status": "failed", "passed": passed,
                "failed_step": step, "reason": _reason(exc)}


class OncePerProcess:
    """Cache success AND failure; concurrent viewers cannot repeat authentication."""
    def __init__(self):
        self._lock = Lock()
        self._result = None

    def get(self, read_section, store_factory=SynologyImageStore):
        with self._lock:
            if self._result is None:
                self._result = run_probe(read_section, store_factory)
            # No config, password, session, raw exception or product metadata kept.
            return {**self._result, "passed": list(self._result["passed"])}


_deployment_check = OncePerProcess()


def render_nas_connection_check():
    import streamlit as st

    def read_section():
        try:
            return st.secrets.get("nas_images")
        except FileNotFoundError:
            return None

    with st.spinner("檢查 NAS 專用圖庫連線（每次程式啟動最多一次）…"):
        result = _deployment_check.get(read_section)
    with st.expander("NAS 連線檢查（非商品搬移進度）"):
        st.caption("只測試固定 8×8 小圖；不搬商品、不改雲表、價格、人工核對或 LINE。"
                   "同次程式啟動的成功與失敗都保留，不提供公開重試按鈕。")
        if result["status"] == "unconfigured":
            st.info("尚未取得 NAS 私密設定；已有 NAS 索引的圖片不會自動改讀舊圖。")
        elif result["status"] == "passed":
            st.success("NAS 測試通過：專用帳號登入、指定圖庫、寫入、新連線讀回及 SHA-256 一致。")
            st.caption(f"固定合成測試圖 {result['bytes']} bytes，保留於專用圖庫；未綁定任何商品。")
        else:
            st.warning("NAS 測試未通過：" + STEPS[result["failed_step"]] + "。" + result["reason"])
            st.caption("已停止；不反覆登入、不改權限、不關閉 TLS 驗證。請先確認原因，勿連續重啟。")
        st.caption("檢查時間（UTC）：" + result["checked_at"])
        if result["passed"]:
            st.caption("已通過：" + " → ".join(STEPS[step] for step in result["passed"]))
