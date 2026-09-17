"""Actionable, wrapping explanations for existing draft checks."""
import streamlit as st

from batch_cost_ui import _focus
from dispatch_diagnostics import overview_rows
from dispatch_manager import digest


def _choose_problem(table_key, diagnostics, batch):
    rows = st.session_state[table_key].get("selection", {}).get("rows", [])
    if len(rows) == 1 and type(rows[0]) is int and 0 <= rows[0] < len(diagnostics):
        _focus(batch, diagnostics[rows[0]]["id"])
        st.session_state["dispatch_problem_open_" + batch["id"]] = True


def render_diagnostics(batch, diagnostics, ready, blocked, excluded):
    a, b, c = st.columns(3)
    a.metric("待整批確認", ready)
    b.metric("阻擋確認", blocked)
    c.metric("本批已排除", excluded)
    st.caption("紅色／阻擋：需先修正才可確認批次。黃色／提醒：不是算價錯誤，仍需處理或明確知悉限制。沒有程式阻擋也不等於已批准或已發送。")
    st.write(f"逐款問題清單：{len(diagnostics)} 款。點選一列，會展開下方對應商品的「目前資料、修正方式、操作位置」。")
    if excluded:
        st.caption(f"另有 {excluded} 款本批排除；排除且已填原因的商品不混入本次問題清單。")
    rows = overview_rows(diagnostics)
    if rows:
        key = "dispatch_problem_table_" + digest([batch["id"], rows])[:24]
        st.dataframe(rows, hide_index=True, width="stretch", height=min(500, 42 + 78 * len(rows)), row_height=78,
                     key=key, selection_mode="single-row",
                     on_select=lambda: _choose_problem(key, diagnostics, batch),
                     column_config={"品號": st.column_config.TextColumn(width="small"),
                                    "確認狀態": st.column_config.TextColumn(width="small"),
                                    "阻擋原因": st.column_config.TextColumn(width="medium"),
                                    "提醒（非算價錯誤）": st.column_config.TextColumn(width="medium")})


def render_issue_details(diagnostic):
    st.markdown("#### 本款原因與修正方式")
    if not diagnostic["blockers"]:
        st.info("本款目前沒有程式阻擋；仍須核對圖文、完成整批確認及發文前第二次檢查，不會自動發送。")
    for level, issues in (("阻擋確認", diagnostic["blockers"]), ("提醒（非硬性阻擋）", diagnostic["warnings"])):
        for issue in issues:
            message = f"{level}｜{issue['field']}：{issue['reason']}"
            (st.error if level == "阻擋確認" else st.warning)(message)
            st.write("目前資料：" + issue["current"])
            st.write("修正方式：" + issue["action"])
            st.caption("操作位置：" + issue["location"])
