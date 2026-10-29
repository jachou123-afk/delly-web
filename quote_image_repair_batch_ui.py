"""Explicit ZIP intake with bounded sequential NAS repair and per-item receipts."""
from collections import Counter
import hashlib
import json

import streamlit as st

from product_image_ui import clear_library_cache
from quote_image_repair_batch import (prepare_batch_repair, process_next_repair,
                                      repair_table, retry_failed_repair, safe_repair_error)
from quote_image_repair_zip import MAX_ZIP_BYTES, read_repair_zip

PREFIX = "quote_image_repair_batch_"
MODES = {"只核對既有 NAS 原圖（不重新上傳）": "existing_only", "保存原圖並核對": "upload"}


def render_batch_image_repair(get_store):
    if not st.checkbox("開啟批次 NAS 原圖補存", key=PREFIX + "enabled"):
        return
    with st.expander("批次 NAS 原圖補存", expanded=True):
        st.caption("只接受已核對的商品原圖包。依 manifest 的分頁、實際 NO 與 SHA 配對，不新增商品、不改報價；每次最多處理 10 款。")
        uploaded = st.file_uploader("商品原圖 ZIP（含 manifest.json）", type=["zip"],
                                    max_upload_size=MAX_ZIP_BYTES // (1024 * 1024), key=PREFIX + "zip")
        mode = MODES[st.radio("本包 NAS 保存方式", list(MODES), key=PREFIX + "mode")]
        raw = uploaded.getvalue() if uploaded is not None else None
        selection = (hashlib.sha256(raw).hexdigest(), mode) if raw is not None else None
        if st.session_state.get(PREFIX + "selection") != selection:
            for suffix in ("package", "run", "notice", "stat_report"):
                st.session_state.pop(PREFIX + suffix, None)
            st.session_state[PREFIX + "selection"] = selection
        if st.button("查核原圖 ZIP", key=PREFIX + "check", disabled=raw is None):
            st.session_state.pop(PREFIX + "run", None)
            st.session_state.pop(PREFIX + "package", None)
            try:
                with st.spinner("驗證原圖包並核對雲表實際 NO…"):
                    package = read_repair_zip(raw)
                    run = prepare_batch_repair(get_store(), package, mode=mode)
                st.session_state[PREFIX + "package"] = package
                st.session_state[PREFIX + "run"] = run
            except Exception as exc:
                st.error("原圖包尚未通過查核：" + safe_repair_error(exc))
        run = st.session_state.get(PREFIX + "run")
        package = st.session_state.get(PREFIX + "package")
        if run is None or package is None:
            st.caption("原圖包只放 manifest.json 與 images/<SHA>.<副檔名>，不含聊天、價格或其他附件。每款第一張為主圖，最多 10 張；ZIP 最多 50 MB，解壓後最多 64 MB。")
            return
        counts = Counter(item["status"] for item in run["items"])
        st.caption(f"共 {len(run['items'])} 款／{len(package['assets'])} 張原圖；已完成 {counts['completed']}、待處理 {counts['ready']}、失敗 {counts['failed']}、查核未通過 {counts['blocked']}。")
        st.dataframe(repair_table(run), hide_index=True, width="stretch")
        confirmed = st.checkbox("我已逐款核對查核表的實際 NO、主圖與原圖組", key=PREFIX + "confirmed_" + run["scope_digest"])
        actor = st.text_input("批次原圖保存人", value="報價補圖操作者", key=PREFIX + "actor")
        limit = st.number_input("本次最多處理款數", min_value=1, max_value=10, value=5, step=1, key=PREFIX + "limit")
        if st.button("處理下一批", key=PREFIX + "next", disabled=not confirmed or not actor.strip() or not counts["ready"]):
            progress = st.empty()

            def update(current):
                st.session_state[PREFIX + "run"] = current
                state = Counter(item["status"] for item in current["items"])
                progress.caption(f"本包已讀回完成 {state['completed']} 款；失敗 {state['failed']} 款，其餘依序接續。")

            try:
                with st.spinner("依序保存原圖並以新 NAS 連線讀回核對…"):
                    updated = process_next_repair(get_store(), package, run, actor=actor, limit=int(limit), on_update=update)
                st.session_state[PREFIX + "run"] = updated
                if any(item["status"] == "completed" for item in updated["items"]):
                    clear_library_cache()
                st.session_state[PREFIX + "notice"] = "本次已結束。成功款已保留收據，不重做；失敗款保留原保存方式，不自動切換或重試。"
                st.rerun()
            except Exception as exc:
                st.error("本次尚未全部確認：" + safe_repair_error(exc))
        if st.button("只重新排入失敗款（不處理已完成）", key=PREFIX + "retry", disabled=not confirmed or not counts["failed"]):
            try:
                st.session_state[PREFIX + "run"] = retry_failed_repair(package, run)
                st.rerun()
            except Exception as exc:
                st.error(safe_repair_error(exc))
        notice = st.session_state.get(PREFIX + "notice")
        if notice:
            st.info(notice)
        if counts["completed"] == len(run["items"]):
            st.success("本包所有商品 NAS 原圖與商品配對均已完成新連線讀回核對。")
        elif counts["failed"] or counts["blocked"]:
            st.warning("本包仍有未完成商品，請查看查核表；其他待處理款可繼續。")
        receipts = [item["receipt"] for item in run["items"] if item["status"] == "completed"]
        st.download_button("下載本包核對收據", data=json.dumps({"schema": 1, "package_sha256": package["package_sha256"],
                           "mode": run["mode"], "receipts": receipts}, ensure_ascii=False, indent=2),
                           file_name="nas-original-repair-receipts.json", mime="application/json", key=PREFIX + "receipt")
