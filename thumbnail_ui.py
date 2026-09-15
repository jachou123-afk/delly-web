"""Explicit derivative creation, separate from read-only preview and approval."""
import streamlit as st

from image_cache import THUMB_PREFIX


def render_thumbnail_management(store):
    if not hasattr(store, "thumbnail_status"):
        return
    with st.expander("NAS 縮圖管理（原圖保留）"):
        st.caption("原圖另做最長邊 640 px、每張最多 128 KB 的預覽副本；不改原圖、配對或核對。"
                   "日後加入新圖片，也從這裡分批建立縮圖。縮圖不供下載發送。")
        if not st.checkbox("讀取縮圖進度", key="thumbnail_status_show"):
            return
        try:
            status = store.thumbnail_status()
        except Exception:
            st.error("縮圖進度讀取失敗，未當成零張；請確認圖庫設定或索引。")
            return
        st.write(f"圖庫／批次原圖 {status['total']} 張｜NAS 縮圖 {status['ready']} 張｜待建立 {len(status['pending'])} 張")
        st.caption(f"已保存縮圖合計 {status['bytes']:,} bytes；每張均寫後下載校驗才發布索引。")
        if not status["configured"]:
            st.info("未配置 NAS 縮圖儲存；舊部署仍可按頁產生暫時縮圖。")
        def build_next():
            try:
                with st.spinner("建立縮圖副本、讀回校驗並保存索引…"):
                    created = store.prepare_next_thumbnails()
                for identity in created:
                    st.session_state.pop(THUMB_PREFIX + identity, None)
                st.session_state.pop("thumbnail_build_error", None)
            except Exception:
                st.session_state["thumbnail_build_error"] = True
        st.button("建立下一批縮圖（最多 10 張）", key="thumbnail_build_next", on_click=build_next,
                  disabled=not status["pending"] or not status["configured"])
        if st.session_state.get("thumbnail_build_error"):
            st.error("本批縮圖未完成或結果待確認，已停止；原圖與商品核對不變，請重新讀取進度。")
        if status["configured"] and status["total"] and not status["pending"]:
            st.success("已配對圖片的 NAS 縮圖全部建立；整批預覽改為每頁 6 款，單款核對仍用原圖。")
