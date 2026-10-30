"""Separate explicit source-refresh control; opening it performs no I/O."""
import json
import streamlit as st

from batch_read_pacing import paced_nas_store
from dispatch_manager import digest
from product_image_ui import clear_library_cache
from quote_image_source_refresh import (can_refresh_binding_source, prepare_source_refresh_attempt,
    refresh_binding_source, verify_source_refresh_attempt, safe_source_refresh_error)


PREFIX = "quote_image_source_refresh_"


def _confirmed_report(attempt, report):
    if not isinstance(report, dict) or report.get("結果") != "已刷新":
        return False
    receipt = report.get("receipt")
    old, binding = attempt["plan"]["binding"], attempt["binding"]
    fixed = {"schema":1, "mode":"binding_source_refresh", "identity":old["identity"], "actor":attempt["actor"],
        "previous_binding":old, "binding":binding, "previous_binding_revision":old["_revision"],
        "binding_revision":binding["_revision"], "previous_source_hash":old["source_hash"],
        "source_hash":binding["source_hash"], "subject":old["subject"], "sha256":old["assets"],
        "main_sha256":old["assets"][0], "snapshot_digest":attempt["plan"]["snapshot_digest"],
        "attempt_digest":attempt["digest"]}
    return (isinstance(receipt, dict) and set(receipt) == set(fixed)|{"checked_at","digest"}
        and digest({k:receipt[k] for k in fixed}) == digest(fixed)
        and isinstance(receipt.get("checked_at"),str) and bool(receipt["checked_at"])
        and receipt.get("digest") == digest({k:v for k,v in receipt.items() if k != "digest"})
        and report.get("identity") == old["identity"])


def render_binding_source_refresh(get_store, plan):
    """Return true only to gate this product's unresolved refresh from append."""
    identity = plan["source"]["identity"]
    suffix = digest(identity)
    attempt_key, report_key, error_key = (PREFIX+k+suffix for k in ("attempt_", "report_", "error_"))
    attempt = st.session_state.get(attempt_key)
    if not can_refresh_binding_source(plan) and attempt is None:
        return False
    scope = digest([plan["snapshot_digest"], plan["binding"]])
    with st.expander("重新核對既有圖組並更新來源指紋"):
        old = attempt["plan"]["binding"] if attempt is not None else plan["binding"]
        st.caption(f"目前商品 {plan['source']['identity']}；保留既有 {len(old['assets'])} 張、原主圖與完整順序。只更新圖庫來源指紋，不改商品、價格、原文或雲表圖片。")
        st.caption("既有主圖 SHA-256："+old["assets"][0])
        st.caption("舊來源指紋："+old["source_hash"])
        st.caption("本次來源指紋："+plan["source"]["source_hash"])
        confirmed = st.checkbox("我已逐張核對既有原圖仍是本款，僅更新來源指紋並保留主圖與順序", key=PREFIX+"confirm_"+scope)
        clicked = st.button("只刷新本款圖組來源指紋（保留主圖與順序）", key=PREFIX+"save",
                            disabled=not confirmed or attempt is not None)
        progress = st.empty()
        def waiting(seconds):
            progress.caption("雲表讀取節奏等待中；未重送來源指紋保存請求。")
        if clicked and attempt is None:
            st.session_state.pop(report_key, None)
            try:
                attempt = prepare_source_refresh_attempt(plan, actor="報價補圖操作者", confirmed_same_product=confirmed)
                # Keep the exact target revision even if the following I/O fails.
                st.session_state[attempt_key] = attempt
                with st.spinner("新連線核對既有原圖、刷新來源指紋並再次讀回…"):
                    report = refresh_binding_source(paced_nas_store(get_store(), on_wait=waiting), attempt)
                st.session_state[report_key] = report
                st.session_state.pop(error_key, None)
                clear_library_cache()
            except Exception as exc:
                st.session_state[error_key] = safe_source_refresh_error(exc)
        attempt = st.session_state.get(attempt_key)
        if attempt is not None:
            st.caption("此預備版本不會再次送出。遇到失敗或結果不明，只能按下方唯讀核對。")
            if st.button("只唯讀核對這次來源指紋預備版本", key=PREFIX+"verify"):
                st.session_state.pop(report_key, None)
                try:
                    with st.spinner("唯讀核對預備版本與兩次新連線原圖…"):
                        report = verify_source_refresh_attempt(paced_nas_store(get_store(), on_wait=waiting), attempt)
                    st.session_state[report_key] = report
                    st.session_state.pop(error_key, None)
                    if report["結果"] == "已刷新":
                        clear_library_cache()
                except Exception as exc:
                    st.session_state[error_key] = safe_source_refresh_error(exc)
        if st.session_state.get(error_key):
            st.error("來源指紋尚未確認完成："+st.session_state[error_key])
        report = st.session_state.get(report_key)
        if attempt is not None and (plan["snapshot_digest"] != attempt["plan"]["snapshot_digest"]
                or digest(plan["binding"]) not in {digest(attempt["plan"]["binding"]), digest(attempt["binding"])}):
            report = None
            st.warning("目前載入的商品或圖片版本已變動；保留原預備版本，只能唯讀核對。")
        if report:
            if report["結果"] == "已刷新" and _confirmed_report(attempt, report):
                st.success(f"{report['identity']} 既有圖組來源指紋已刷新並讀回；原主圖與順序保留。請重新載入商品後，再使用追加原圖入口。")
                with st.expander("查看來源指紋刷新正式收據 JSON"):
                    st.code(json.dumps(report["receipt"], ensure_ascii=False, indent=2), language="json")
            else:
                st.warning("目前未讀到本次預備版本；未重送保存，也未標完成。")
        pending = attempt is not None and not _confirmed_report(attempt, st.session_state.get(report_key))
        if pending:
            st.warning("這款來源指紋刷新尚未確認；追加入口暫停，請先唯讀核對預備版本。")
        return pending
