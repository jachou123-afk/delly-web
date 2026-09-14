"""Independent multi-row scope, single-cell detail navigation, read-only results."""
import streamlit as st

from batch_cost_audit import batch_signature, merge_selection, run_batch_audit
from cost_audit import block
from dispatch_manager import digest


def _focus(batch, identity):
    key = "dispatch_focus_draft_" + batch["id"]
    # Update both the durable focus and the mounted selectbox. Deleting the
    # widget state leaves its old frontend value able to win on the next rerun.
    st.session_state[key + batch.get("_revision", "")] = identity
    st.session_state[key] = identity


def _table_changed(table_key, selection_key, visible_ids, batch, select_rows=True):
    event = st.session_state[table_key].get("selection", {})
    if select_rows:
        st.session_state[selection_key] = merge_selection(
            st.session_state.get(selection_key, set()), visible_ids, event.get("rows", []))
    cells = event.get("cells", [])
    if cells:
        row, column = cells[0]
        if type(row) is int and 0 <= row < len(visible_ids) and column in ("品號", "商品"):
            _focus(batch, visible_ids[row])


def render_batch_cost_tools(store, batch, items, visible, row_state, candidates):
    st.subheader("① 選取驗算範圍")
    selection_key = "dispatch_bulk_selected_" + batch["id"]
    all_ids, visible_ids = [i["id"] for i in items], [i["id"] for i in visible]
    selected = set(st.session_state.get(selection_key, set())) & set(all_ids)
    left, right, _ = st.columns([1, 1, 2])
    if left.button(f"全選本批（{len(items)} 款）", key="bulk_all_" + batch["id"]):
        selected = set(all_ids)
    if right.button("取消全選", key="bulk_none_" + batch["id"]):
        selected = set()
    st.session_state[selection_key] = selected
    hidden = len(selected - set(visible_ids))
    st.write(f"本批共 {len(items)} 款｜搜尋顯示 {len(visible)} 款｜已選 {len(selected)} 款")
    if hidden:
        st.info(f"已選商品中有 {hidden} 款未顯示在目前搜尋結果；仍會納入驗算。取消全選會清除整批選取。")
    st.caption("左側方框可多選；點「品號／商品」只看下方明細。全選涵蓋整批，包含搜尋未顯示及暫緩商品；不代表已核對或發送。")
    table_key = "dispatch_review_table_" + digest([batch_signature(batch), visible_ids])[:24]
    if visible:
        # Streamlit 1.63 supports native programmatic selection. Stable IDs live
        # separately so filtering/sorting/revisions cannot reinterpret row picks.
        st.session_state[table_key] = {"selection": {
            "rows": [n for n, identity in enumerate(visible_ids) if identity in selected], "cells": []}}
        st.dataframe([{"順序": i["order"], "品號": i["source"]["code"] or i["source"]["no"],
                       "商品": i["source"]["name"], "成本": block(i["source"].get("block", []))[1][10] or "缺資料",
                       "售價": i["source"].get("price", "待確認"), "單位": i["source"].get("unit") or "待確認",
                       "圖片": f"{len(i['images'])} 張本批已存" if i["images"] else (
                           f"{len(candidates[i['id']])} 張" + ("圖庫已存" if all(a.get("binding_revision") for a in candidates[i['id']]) else "候選")
                           if candidates.get(i["id"]) else "待配對／補圖"),
                       "核對": row_state(i)} for i in visible],
                     hide_index=True, width="stretch", height=min(520, 48 + 42 * len(visible)), row_height=42,
                     on_select=lambda: _table_changed(table_key, selection_key, visible_ids, batch),
                     selection_mode=["multi-row", "single-cell"], key=table_key,
                     column_config={"順序": st.column_config.NumberColumn(width="small"),
                                    "品號": st.column_config.TextColumn(help="點品號查看單款明細，不改變左側勾選"),
                                    "商品": st.column_config.TextColumn(width="large"),
                                    "售價": st.column_config.TextColumn(width="small"),
                                    "成本": st.column_config.TextColumn(width="small"),
                                    "單位": st.column_config.TextColumn(width="small")})
        st.caption("清單可在表格內往下捲動；上方總數包含未露出的列。")
    else:
        st.info("搜尋沒有符合的商品；已選範圍仍保留，可清除搜尋後查看。")
    result_key = "dispatch_bulk_result_" + batch["id"]
    if st.button(f"驗算所選商品（{len(selected)} 款）", disabled=not selected,
                 type="primary", key="bulk_run_" + batch["id"]):
        progress = st.progress(0, text=f"準備讀取 {len(selected)} 款…")
        try:
            result = run_batch_audit(store, batch, selected,
                                    progress=lambda done, total: progress.progress(done / total, text=f"已處理 {done}／{total} 款"))
            fresh = result.pop("catalog")
            if fresh is not None:
                st.session_state["dispatch_review_catalog"] = fresh
            for item in items:
                if item["id"] not in selected:
                    continue
                cache_key = "dispatch_cost_" + item["id"] + item["source"]["source_hash"]
                st.session_state.pop(cache_key, None)
                check = result["checks"][item["id"]]
                if check.get("report"):
                    st.session_state[cache_key] = (check["report"], check["formulas"])
            st.session_state[result_key] = result
        except Exception as exc:
            st.session_state.pop(result_key, None)
            st.error(f"本次驗算未完成：{exc}；未更改雲表或核對狀態。")
        else:
            st.rerun()
    st.subheader("② 所選商品驗算結果")
    result = st.session_state.get(result_key)
    if not result:
        st.info("尚未執行整批驗算。選好商品後按「驗算所選商品」，每款都會列出結果。")
        return
    if result["signature"] != batch_signature(batch):
        st.warning("草稿內容已更新，前次整批驗算結果已失效，請重新驗算。")
        return
    if set(result["ids"]) != selected:
        st.warning(f"選取範圍已更動：下表仍是上次 {len(result['ids'])} 款的結果，不是目前所選 {len(selected)} 款。")
    counts = result["counts"]
    a, b, c, d = st.columns(4)
    a.metric("本次結果", f"{len(result['rows'])} 款")
    b.metric("計算一致", counts.get("計算一致", 0))
    c.metric("有差異", counts.get("有差異", 0))
    d.metric("無法完成／需處理", sum(v for k, v in counts.items() if k not in {"計算一致", "有差異"}))
    st.caption(f"驗算時間：{result['at']}。這是當次讀取的結果；計算一致不等於原文、圖片與單位已核對。未自動儲存核對、修改價格或發 LINE。")
    result_table_key = "dispatch_bulk_table_" + digest([batch["id"], result["at"]])[:24]
    st.session_state[result_table_key] = {"selection": {"cells": []}}
    st.dataframe(result["rows"], hide_index=True, width="stretch", height=min(560, 48 + 44 * len(result["rows"])), row_height=44,
                 key=result_table_key, selection_mode="single-cell",
                 on_select=lambda: _table_changed(result_table_key, selection_key, result["ids"], batch, select_rows=False),
                 column_config={"順序": st.column_config.NumberColumn(width="small"),
                                "商品": st.column_config.TextColumn(width="medium"),
                                "計算結果": st.column_config.TextColumn(width="medium"),
                                "需處理": st.column_config.TextColumn(width="large")})
    st.caption(f"共 {len(result['rows'])} 列，包含有差異、缺資料與讀取失敗的商品；可捲動或放大表格。點品號看下方詳細算式。")
