"""Explicit, ten-image migration batches; no quote/binding/approval writes."""
import streamlit as st


def render_nas_image_management(store):
    if not hasattr(store, "migration_status"):
        return
    with st.expander("NAS 商品原圖搬移與對帳"):
        st.caption("只搬已綁定且留有舊原檔的圖片，每批最多 10 張；寫後校驗才發布位置索引。"
                   "不刪舊圖、不改商品配對、價格、核對或 LINE。新上傳原圖改存 NAS。")
        # Do not query either image backend during unrelated ordinary page views.
        if not st.checkbox("讀取 NAS 搬移進度", key="nas_migration_show"):
            return
        try:
            status = store.migration_status()
        except Exception:
            st.error("搬移進度讀取失敗，停止操作；沒有當成零張或重新搬移。")
            return
        eligible, migrated, pending = status["eligible"], status["migrated"], status["pending"]
        st.write(f"舊原圖 {len(eligible)} 張｜已切換 NAS {len(migrated)} 張｜待搬 {len(pending)} 張")
        st.caption("已切換數量來自已保存的位置索引；不代表本次已重新下載對帳。")
        scope = tuple(eligible)
        if st.session_state.get("nas_verify_scope") != scope:
            st.session_state["nas_verify_scope"] = scope
            st.session_state["nas_verified"] = {}
        verified = st.session_state.setdefault("nas_verified", {})
        # A disappearing pointer must never leave a stale verified count.
        for identity in set(verified) - set(migrated):
            verified.pop(identity, None)
        if not status["nas_ready"]:
            st.warning("NAS 私密設定未就緒；已切換圖片不會暗中退回舊圖。")
        if st.button("搬移下一批（最多 10 張）", key="nas_migrate_next",
                     disabled=not pending or not status["nas_ready"]):
            try:
                with st.spinner("搬移、讀回校驗並保存圖片位置…"):
                    moved = store.migrate_next_images()
                # Force subsequent previews to use the verified storage location.
                for identity in moved:
                    st.session_state.pop("dispatch_asset_" + identity, None)
                st.rerun()
            except Exception:
                st.error("本批搬移未完成或結果待確認，已停止；請重新讀取進度，勿新增商品或覆蓋原圖。")
        remaining = [k for k in migrated if k not in verified]
        if st.button("讀回對帳下一批（最多 10 張）", key="nas_verify_next",
                     disabled=bool(pending) or not remaining or not status["nas_ready"]):
            try:
                with st.spinner("從 NAS 新連線讀回，逐張比對舊原檔…"):
                    rows = store.verify_migrated_images(remaining[:10])
                verified.update({row["sha256"]: row for row in rows})
                st.rerun()
            except Exception:
                st.error("原檔讀回對帳未通過；未標記本批通過，請先確認連線／檔案／索引。")
        st.write(f"本次重新讀回對帳：{len(verified)}／{len(eligible)} 張")
        if verified:
            st.caption(f"已比對 {sum(row['bytes'] for row in verified.values()):,} bytes；每張原檔位元組及 SHA-256 一致。")
            st.dataframe([{"原檔名稱": row["name"], "大小（bytes）": row["bytes"],
                           "對帳結果": "原檔位元組一致", "SHA-256": identity}
                          for identity, row in sorted(verified.items())],
                         hide_index=True, width="stretch")
        if status["nas_ready"] and eligible and not pending and set(verified) == set(eligible):
            st.success("本次已逐張讀回，全部原圖與舊圖庫一致；舊圖備份保留，商品核對狀態未改。")
