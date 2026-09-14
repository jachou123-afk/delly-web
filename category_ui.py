import streamlit as st

from category_codes import QUOTE_CATEGORIES


def render_category_settings(store):
    with st.expander("分類代碼設定（全工具共用）"):
        try:
            key = "dispatch_category_settings"
            if key not in st.session_state:
                st.session_state[key] = store.category_settings()
            state = st.session_state[key]
            st.table([{"商品分頁": k, "廣告代碼": v, "範例": f"BGD-{v}-123"} for k, v in state["codes"].items()])
            missing = [k for k in QUOTE_CATEGORIES if k not in state["codes"]]
            if missing:
                st.warning("尚未設定：" + "、".join(missing) + "。不依分頁第一個字自動猜代碼。")
            st.caption("可新增分類對應；不同分頁不可共用代碼。既有代碼鎖定，避免改動歷史廣告品號。")
            category = st.text_input("新增對應的商品分頁", key="category_new_name")
            code = st.text_input("廣告分類代碼", key="category_new_code", help="1～8 個英文字母，例如 W 或 WA；不得與既有代碼重複。")
            actor = st.text_input("分類設定人", key="category_actor")
            if st.button("保存分類代碼", disabled=not category.strip() or not code.strip() or not actor.strip()):
                store.save_category_code(category, code, actor=actor, expected_revision=state["_revision"])
                for cached in list(st.session_state):
                    if cached.startswith(("dispatch_category_", "dispatch_catalog", "dispatch_review_catalog", "dispatch_bulk_result_")):
                        st.session_state.pop(cached, None)
                st.session_state["dispatch_notice"] = "分類代碼已保存；原商品列與已確認批次未改動。"
                st.rerun()
        except Exception as exc:
            st.error(f"分類設定無法使用：{exc}")
