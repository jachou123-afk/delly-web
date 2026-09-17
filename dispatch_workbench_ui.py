"""Compact, session-local dispatch cards. No LINE transport or implicit receipts."""
import streamlit as st

from dispatch_manager import item_status
from dispatch_workbench import prepare_workbench, current_item, card_images, complete_current, advance_run

RUN_KEY = "dispatch_workbench_run"


def cache_saved_batch(saved):
    history = st.session_state.get("dispatch_history")
    if history is not None:
        st.session_state["dispatch_history"] = [saved if b["id"] == saved["id"] else b for b in history]
        if not any(b["id"] == saved["id"] for b in history):
            st.session_state["dispatch_history"].insert(0, saved)
    st.session_state["dispatch_active"] = saved["id"]


def render_workbench(store, batch):
    """Return True while cards replace the detailed management controls."""
    if batch["status"] not in ("approved", "in_progress"):
        return False
    st.subheader("快速發送工作台")
    run = st.session_state.get(RUN_KEY)
    if run and run["batch_id"] != batch["id"]:
        st.session_state.pop(RUN_KEY, None)
        run = None
    if run:
        try:
            item = current_item(batch, run)
        except Exception as exc:
            run["blocked"] = True
            st.error(str(exc))
            st.warning("先查看 LINE 與雲端；若已發只補登，不能用重發處理保存錯誤。")
            if st.button("返回核對／單款處理", key="workbench_recover"):
                st.session_state.pop(RUN_KEY, None)
                st.session_state.pop("dispatch_history", None)
                st.rerun()
            return False
        if item is None:
            st.success(f"本輪 {len(run['completed'])} 款的圖文查驗結果均已保存；全批結案仍看下方對帳。")
            if st.button("結束本輪工作台", key="workbench_end"):
                st.session_state.pop(RUN_KEY, None)
                st.rerun()
            return False
        st.info(f"目標：{run['target']}｜第 {len(run['completed']) + 1} / {len(run['items'])} 款｜執行人：{run['actor']}")
        st.caption(f"第二次檢查：{run['checked_at']}。使用此凍結版本；本頁不會自動操作 LINE。")
        if run["missing_source_ids"]:
            st.warning(f"沿用已確認的舊資料限制：{len(run['missing_source_ids'])} 款缺廠商原文；表內驗算一致不代表已核實原文。")
        with st.expander("一次下載全輪原圖與文案／操作規則"):
            st.download_button("下載本輪已檢查圖文包", run["package"], f"已檢查圖文-{run['id'][:8]}.zip",
                               "application/zip", on_click="ignore", key="workbench_zip_" + run["id"])
            st.caption("圖 → 文 → 看到兩者完整相鄰出現 → 完成並下一則。不要把整批圖片與文案分開發。")
            st.caption("檢查是當時快照，不是持續監控雲表。修改來源／價格／圖片／文案、换人換機或中斷不明時，先暫停重查；同批只由一人發送。")
        st.write(f"**{item['source']['code']}｜{item['source']['name']}**")
        left, right = st.columns([1, 1.4])
        try:
            images = card_images(run, item)
            if len(images) != len(item["images"]):
                raise ValueError("圖片張數與凍結版本不符")
            with left:
                for position, (name, data) in enumerate(images, 1):
                    st.image(data, caption=f"原圖 {position} / {len(images)}", width="stretch")
                    st.download_button(f"取得本款原圖 {position}", data, item["source"]["code"] + "-" + name,
                                       on_click="ignore", key=f"workbench_image_{run['id']}_{item['id']}_{position}")
        except Exception as exc:
            run["blocked"] = True
            st.error(f"原圖不能正常顯示，停止本款：{exc}")
            return True
        with right:
            st.caption("右上角複製完整文案；內容已凍結，不必重算。")
            st.code(item["copy"], language=None)
        st.caption("按下完成＝你已實際查驗正確聊天室：本款全部原圖＋完整文案相鄰出現，無傳送中／失敗。不是按了 LINE 傳送就算完成。")
        with st.expander("補充 LINE 訊息時間／查驗依據（選填）"):
            note = st.text_input("本款查驗補充", key=f"workbench_note_{run['id']}_{item['id']}")
        complete, pause = st.columns(2)
        if pause.button("暫停／有問題，返回處理", key="workbench_pause"):
            run["blocked"] = True
            st.rerun()
        # Item-specific key prevents a delayed double click completing the next card.
        if complete.button("完成並下一則", type="primary", key=f"workbench_done_{run['id']}_{item['id']}"):
            try:
                updated = complete_current(batch, run, item["id"], observed=True, note=note)
                saved = store.save_batch(updated, expected_revision=run["revision"],
                                         record_id=run["id"] + "-" + item["id"])
                next_run = advance_run(run, saved, item["id"])
                cache_saved_batch(saved)
                st.session_state[RUN_KEY] = next_run
            except Exception as exc:
                run["blocked"] = True
                st.error(f"保存結果待查，不前進也不重發：{exc}")
                return True
            st.rerun()
        return True
    candidates = [i for i in sorted(batch["items"], key=lambda i: i["order"])
                  if not i["excluded"] and item_status(i) == "待發"]
    if not candidates:
        st.caption("沒有整組待發商品；已完成不重發，部分完成／結果不明請用下方單款處理。")
        return False
    with st.expander("第二次檢查通過後，逐則快速發送", expanded=True):
        st.caption("解析／報價完成後，再整批檢查成本、算式、售價、單位、原圖、文案及已發紀錄。只檢查一次，通過才開放卡片。")
        prefix = "workbench_prepare_" + batch["id"] + batch.get("_revision", "")
        by_id = {i["id"]: i for i in candidates}
        selection = st.multiselect("本輪工作台商品（預設全選）", list(by_id), default=list(by_id), key=prefix + "_items",
                                   format_func=lambda k: by_id[k]["source"]["code"] + "｜" + by_id[k]["source"]["name"])
        actor = st.text_input("本輪執行／查驗人", value=batch["actor"], key=prefix + "_actor")
        checked = st.checkbox(f"已核對 LINE「{batch['target']}」與未發範圍，且本輪只有我執行", key=prefix + "_checked")
        if st.button("第二次檢查並開啟工作台", type="primary", disabled=not selection, key=prefix + "_start"):
            try:
                with st.spinner("整批驗算、核對版本及準備原圖；此步驟不發送、不登記完成…"):
                    st.session_state[RUN_KEY] = prepare_workbench(store, batch, selection, actor, history_checked=checked)
            except Exception as exc:
                st.error(f"第二次檢查未通過：{exc}")
            else:
                st.rerun()
    return False
