"""Explicit selected-product supplement control; no replacement controls."""
import json

import streamlit as st

from batch_read_pacing import paced_nas_store
from dispatch_manager import digest
from product_image_ui import clear_library_cache
from quote_image_append import append_nas_image_repair
from quote_image_repair_batch import safe_repair_error


PREFIX = "quote_image_append_"


def render_append_original(get_store, plan, assets, *, selection_errors=False):
    binding = plan.get("binding")
    if not binding:
        return
    scope = digest([plan["snapshot_digest"], binding, [a["sha256"] for a in assets], selection_errors])
    if st.session_state.get(PREFIX + "selection") != scope:
        st.session_state.pop(PREFIX + "report", None)
        st.session_state[PREFIX + "selection"] = scope
    with st.expander("追加本款補充原圖（保留既有主圖與順序）"):
        st.caption(f"目前商品 {plan['source']['identity']}；既有 {len(binding['assets'])} 張。只追加一張已核對同款的新原圖，不替換、刪除或重傳舊圖。")
        st.caption("既有主圖 SHA-256：" + binding["assets"][0])
        confirmed = st.checkbox("我已核對補充原圖與本款同款，並確認保留既有主圖與順序", key=PREFIX + "confirmed_" + scope)
        clicked = st.button("只追加本款補充原圖（保留主圖）", key=PREFIX + "save",
                            disabled=not confirmed or len(assets) != 1 or selection_errors)
        if clicked:
            st.session_state.pop(PREFIX + "report", None)
            progress = st.empty()

            def waiting(seconds):
                progress.caption("雲表讀取節奏等待中；未重送圖片保存請求。")

            try:
                with st.spinner("核對舊圖，只補新原圖並重新讀回全部原圖…"):
                    store = paced_nas_store(get_store(), on_wait=waiting)
                    report = append_nas_image_repair(store, plan, assets,
                        actor="報價補圖操作者", confirmed_same_product=confirmed)
                st.session_state[PREFIX + "report"] = report
                clear_library_cache()
            except Exception as exc:
                st.error("補充原圖尚未確認完成：" + safe_repair_error(exc))
        report = st.session_state.get(PREFIX + "report")
        if report:
            st.success(f"{report['identity']} 補充原圖已追加並完成新連線讀回；既有主圖與順序保留。")
            with st.expander("查看追加原圖核對收據 JSON"):
                st.code(json.dumps(report["receipt"], ensure_ascii=False, indent=2), language="json")
