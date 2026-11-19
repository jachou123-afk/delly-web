"""Explicit ZIP intake with bounded sequential NAS repair and per-item receipts."""
from collections import Counter
import hashlib
import json

import streamlit as st

from product_image_ui import clear_library_cache
from batch_read_pacing import paced_nas_store
from quote_image_repair_batch import (prepare_batch_repair, process_next_repair,
                                      repair_table, retry_failed_repair, safe_repair_error,
                                      repair_receipt_payload, inspect_batch_nas_files)
from quote_image_repair_zip import MAX_ZIP_BYTES, read_repair_zip

PREFIX = "quote_image_repair_batch_"
MODES = {"只核對既有 NAS 原圖（不重新上傳）": "existing_only", "保存原圖並核對": "upload"}


def render_batch_image_repair(get_store, *, get_paced_store=None):
    def processing_store(**kwargs):
        # The production factory wraps HTTP before opening the workbook so its
        # first metadata GET shares the same budget as preflight and processing.
        return (get_paced_store(**kwargs) if get_paced_store is not None
                else paced_nas_store(get_store(), **kwargs))

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
                    run = prepare_batch_repair(processing_store(), package, mode=mode)
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
        if run.get("halt"):
            local_wait = run["halt"].get("diagnostic", {}).get("kind") == "read_budget_wait"
            st.warning("本地讀取等待達上限，本次已停止；後續商品仍待處理，沒有重送API請求。"
                       if local_wait else "服務回報 429，本次已停止；後續商品仍待處理，不會自動重試。")
        st.caption(f"共 {len(run['items'])} 款／{len(package['assets'])} 張原圖；已完成 {counts['completed']}、待處理 {counts['ready']}、失敗 {counts['failed']}、查核未通過 {counts['blocked']}。")
        st.dataframe(repair_table(run), hide_index=True, width="stretch")
        confirmed = st.checkbox("我已逐款核對查核表的實際 NO、主圖與原圖組", key=PREFIX + "confirmed_" + run["scope_digest"])
        actor = st.text_input("批次原圖保存人", value="報價補圖操作者", key=PREFIX + "actor")
        limit = st.number_input("本次最多處理款數", min_value=1, max_value=10, value=5, step=1, key=PREFIX + "limit")
        unfinished = counts["ready"] + counts["failed"]
        with st.expander("本包未完成款 NAS 檔案資訊（唯讀）"):
            st.caption("只查待處理及失敗商品，每次最多10款；跳過已完成，不改保存方式、不上傳、不發布圖片配對。存在或大小相同不等於原bytes／SHA驗收完成。")
            start = st.number_input("檔案資訊查核起始款", min_value=1, max_value=max(1, unfinished),
                                    value=1, step=1, key=PREFIX + "stat_start_" + run["scope_digest"] + "_" + str(unfinished))
            if st.button("只查本包未完成款 NAS 檔案資訊", key=PREFIX + "stat", disabled=not unfinished):
                st.session_state.pop(PREFIX + "stat_report", None)
                try:
                    with st.spinner("只查本包未完成原圖的 NAS 檔案資訊…"):
                        store = processing_store()
                        st.session_state[PREFIX + "stat_report"] = inspect_batch_nas_files(
                            store, package, run, offset=int(start) - 1, limit=10)
                except Exception as exc:
                    st.error("本包檔案資訊尚未確認：" + safe_repair_error(exc))
            stat_report = st.session_state.get(PREFIX + "stat_report")
            if stat_report:
                if stat_report.get("halt"):
                    st.warning("唯讀查核已停止：" + stat_report["halt"]["reason"] + "；未改原補存進度或自動接續其他款。")
                rows = []
                for item in stat_report["items"]:
                    if not item["images"]:
                        rows.append({"商品": item["identity"], "查核時間": item["checked_at"], "原因": item["reason"]})
                    for image in item["images"]:
                        rows.append({"商品": item["identity"], "原圖 SHA-256": image["sha256"],
                                     "原檔大小": image["expected_size"], "NAS 大小": image["size"],
                                     "存在": {True: "是", False: "否", None: "未確認"}[image["exists"]],
                                     "大小相同": image["size_matches"], "查核時間": item["checked_at"], "原因": image["reason"]})
                st.dataframe(rows, hide_index=True, width="stretch")
                stat_json = json.dumps(stat_report, ensure_ascii=False, indent=2)
                st.download_button("下載本包唯讀檔案資訊 JSON", data=stat_json,
                                   file_name="nas-original-stat-only.json", mime="application/json", key=PREFIX + "stat_json")
        if st.button("處理下一批", key=PREFIX + "next", disabled=not confirmed or not actor.strip() or not counts["ready"]):
            progress = st.empty()
            waited = [0.0]

            def waiting(seconds):
                waited[0] += seconds
                progress.caption(f"Google Sheets 讀取節奏等待中，本批累積等待約 {waited[0]:.0f} 秒；不會重新送出保存請求。")

            def update(current):
                st.session_state[PREFIX + "run"] = current
                state = Counter(item["status"] for item in current["items"])
                progress.caption(f"本包已讀回完成 {state['completed']} 款；失敗 {state['failed']} 款，其餘依序接續。")

            try:
                with st.spinner("依序保存原圖並以新 NAS 連線讀回核對…"):
                    store = processing_store(on_wait=waiting)
                    updated = process_next_repair(store, package, run, actor=actor, limit=int(limit), on_update=update)
                st.session_state[PREFIX + "run"] = updated
                if any(item["status"] == "completed" for item in updated["items"]):
                    clear_library_cache()
                halted = updated.get("halt")
                st.session_state[PREFIX + "notice"] = (
                    "本地讀取等待達上限，本次已停止；後續商品保持待處理，成功收據保留。"
                    if halted and halted.get("diagnostic", {}).get("kind") == "read_budget_wait" else
                    "服務回報 429，本次已停止；後續商品保持待處理，成功收據保留。" if halted else
                    "本次已結束。成功款已保留收據，不重做；失敗款保留原保存方式，不自動切換或重試。")
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
        receipt_json = json.dumps(repair_receipt_payload(package, run), ensure_ascii=False, indent=2)
        with st.expander("查看本包核對收據 JSON"):
            st.code(receipt_json, language="json")
        st.download_button("下載本包核對收據", data=receipt_json,
                           file_name="nas-original-repair-receipts.json", mime="application/json", key=PREFIX + "receipt")
