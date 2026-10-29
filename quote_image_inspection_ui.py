"""Explicit single-item read controls; opening this UI performs no storage I/O."""
import streamlit as st

from dispatch_manager import digest
from dispatch_storage import asset_bytes
from image_thumbnails import display_bytes
from quote_image_inspection import inspect_nas_files, read_bound_originals, inspection_error

PREFIX = "quote_image_inspection_"
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def render_image_inspection(get_store, plan, assets, *, selection_errors=False):
    selection = digest([plan["snapshot_digest"], plan.get("binding"),
                        [(asset["sha256"], asset["name"]) for asset in assets], bool(selection_errors)])
    if st.session_state.get(PREFIX + "selection") != selection:
        for suffix in ("stat", "bound"):
            st.session_state.pop(PREFIX + suffix, None)
        st.session_state[PREFIX + "selection"] = selection
    with st.expander("本款原圖唯讀檢查"):
        st.caption("只讀取本款選取或已綁定的原圖，不保存、不替換、不追加，也不變更批次處理方式。")
        if st.button("只查本款 NAS 檔案資訊", key=PREFIX + "stat_button",
                     disabled=not assets or bool(selection_errors)):
            st.session_state.pop(PREFIX + "stat", None)
            try:
                with st.spinner("查核本款選取原圖的檔案資訊…"):
                    result = inspect_nas_files(get_store(), plan, assets)
                st.session_state[PREFIX + "stat"] = result
            except Exception as exc:
                st.error("檔案資訊尚未確認：" + inspection_error(exc))
        stats = st.session_state.get(PREFIX + "stat")
        if stats:
            st.dataframe([{"原圖 SHA-256": row["sha256"], "原檔大小": row["expected_size"],
                           "存在": {True: "是", False: "否", None: "未確認"}[row["exists"]],
                           "NAS 大小": row["size"], "大小相同": row["size_matches"],
                           "原因": row["reason"]} for row in stats["images"]], hide_index=True, width="stretch")
            st.caption("檔案資訊查核時間：" + stats["checked_at"])
            st.warning("檔案存在或大小相同只供診斷，不代表 SHA-256 校驗或補圖完成；不會自動上傳。")
        if st.button("讀取這款已綁定原圖", key=PREFIX + "bound_button", disabled=not plan.get("binding")):
            st.session_state.pop(PREFIX + "bound", None)
            try:
                with st.spinner("依既有綁定順序讀取並核對原圖…"):
                    result = read_bound_originals(get_store(), plan)
                st.session_state[PREFIX + "bound"] = result
            except Exception as exc:
                st.error("已綁定原圖尚未確認：" + inspection_error(exc))
        bound = st.session_state.get(PREFIX + "bound")
        if bound:
            st.caption("以下保留既有順序，第一張為原主圖；僅供核對與下載，未修改綁定。讀取時間：" + bound["checked_at"])
            for index, asset in enumerate(bound["assets"], 1):
                raw = asset_bytes(asset)
                label = f"原圖 {index}" + ("（原主圖）" if index == 1 else "")
                st.image(display_bytes(raw), caption=label + " · " + asset["sha256"], width="stretch")
                st.download_button("下載" + label, data=raw,
                                   file_name=f"{index:02d}-{asset['sha256']}.{EXTENSIONS[asset['mime']]}",
                                   mime=asset["mime"], key=PREFIX + "download_" + selection + "_" + str(index))
