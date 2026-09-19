"""Image-library setup, automatic proposals and explicit persistence controls."""
import streamlit as st

from dispatch_images import extract_sheet_images, match_image_pack
from dispatch_manager import digest
from dispatch_storage import asset_bytes, validate_image
from product_images import subject_key, unique_proposals


def clear_library_cache():
    for key in list(st.session_state):
        if key.startswith("dispatch_library_"):
            st.session_state.pop(key, None)


def render_quote_images(key):
    st.markdown("#### 本款原圖（隨報價保存到圖庫）")
    st.caption("選填；每款最多 5 張，單張 2 MB。原圖只需提供一次，之後發送管理自動帶入；不改雲表原有圖片。")
    uploads = st.file_uploader("本款商品原圖", type=["jpg", "jpeg", "png", "webp"],
                               accept_multiple_files=True, max_upload_size=2, key=key)
    assets, errors = {}, []
    for upload in uploads or []:
        try:
            asset = validate_image(upload.getvalue(), upload.name)
            assets[asset["sha256"]] = asset
        except ValueError as exc:
            errors.append(f"{upload.name}：{exc}")
    if len(assets) > 5:
        errors.append("每款最多 5 張不同原圖")
    if assets:
        columns = st.columns(min(5, len(assets)))
        for n, asset in enumerate(assets.values()):
            columns[n % len(columns)].image(asset_bytes(asset), caption=asset["name"], width="stretch")
    return list(assets.values()), errors


def render_source_images(store, batch):
    products = [i["source"] for i in batch["items"]]
    cache_key = "dispatch_source_images_" + batch["id"]
    library_key = "dispatch_library_" + batch["id"] + digest([(p["identity"], subject_key(p)) for p in products])[:16]
    library = None
    try:
        if library_key not in st.session_state:
            st.session_state[library_key] = store.product_image_refs(products)
        library = st.session_state[library_key]
    except Exception as exc:
        st.error(f"商品圖庫讀取失敗：{exc}。未當成沒有圖片，請重新載入雲端。")
    refs = library["images"] if library else {}
    found = sum(bool(v) for v in refs.values())
    if library is not None:
        st.caption(f"本批 {found}／{len(products)} 款已有配對圖片。")
    else:
        st.caption("商品圖庫：狀態未能讀取，暫不顯示自動帶圖數量。")
    with st.expander("補圖或調整圖片配對"):
        st.caption("舊商品整包配對一次，保存到圖庫後各批次共用；不需要每次重新搬圖。")
        if library:
            for warning in library["warnings"]:
                st.warning(warning)
        if st.button("載入原報價表圖片（整批）", key="source_images_load_" + batch["id"]):
            try:
                with st.spinner("讀取原表圖片並比對品號、品名與圖片位置…"):
                    st.session_state[cache_key] = store.source_images(products)
                st.rerun()
            except Exception as exc:
                st.warning("原表圖片暫時無法匯出，並不代表原表沒有圖片；可在下方整包匯入。")
                st.text(str(exc))
        st.caption("ZIP 檔名等於貨號或 BGD 品號，例如 A0081.jpg；貨號會對照整份雲表，重複時不猜。Excel 以原分頁、品號、品名與圖片位置配對。")
        uploaded = st.file_uploader("原圖圖片包或原表 Excel", type=["zip", "xlsx"], max_upload_size=50,
                                    key="source_pack_" + batch["id"])
        if st.button("讀取圖片包並預覽配對", key="source_pack_read_" + batch["id"], disabled=uploaded is None):
            try:
                if uploaded.name.lower().endswith(".xlsx"):
                    proposals = extract_sheet_images(uploaded.getvalue(), products)
                else:
                    proposals = match_image_pack(uploaded.getvalue(), products, all_products=store.catalog())
                st.session_state[cache_key] = proposals
                st.session_state.pop("image_save_report_" + batch["id"], None)
                st.rerun()
            except Exception as exc:
                st.error(f"圖片包未套用：{exc}")
        proposals = st.session_state.get(cache_key, {})
        if proposals:
            count = sum(bool(v) for v in proposals["images"].values())
            st.info(f"找到 {count} 款圖片候選；未配對的商品仍留在清單。")
            for warning in proposals.get("warnings", []):
                st.warning(warning)
            plan, rows = unique_proposals(products, proposals["images"], library["bindings"] if library else {})
            st.dataframe(rows, hide_index=True, width="stretch", height=250)
            actor = st.text_input("圖片保存人", value=batch["actor"], key="image_actor_" + batch["id"])
            st.caption("下方按鈕只保存唯一配對的原圖與商品對應，不保存人工核對、不發 LINE；多張候選或既有不同圖片請到單款明細確認。")
            if st.button(f"保存 {len(plan)} 款唯一配對圖片到圖庫", key="images_bind_" + batch["id"],
                         disabled=not plan or not actor.strip() or library is None):
                try:
                    with st.spinner("保存原圖並核對商品對應…"):
                        result = store.save_product_images(plan, actor=actor, origin="legacy_image_import")
                    st.session_state["image_save_report_" + batch["id"]] = result
                    clear_library_cache()
                    st.rerun()
                except Exception as exc:
                    st.error(f"圖庫未完成：{exc}；原商品與批次核對狀態未更改。")
        report = st.session_state.get("image_save_report_" + batch["id"])
        if report:
            st.dataframe(report, hide_index=True, width="stretch")
            success = sum(r["結果"] in {"已綁定", "已存在"} for r in report)
            st.info(f"本次 {len(report)} 款：{success} 款已綁定，{len(report) - success} 款待處理。")
    # A saved, current binding takes precedence over transient package candidates.
    manual = st.session_state.get(cache_key, {}).get("images", {})
    return {p["identity"]: refs.get(p["identity"]) or manual.get(p["identity"], []) for p in products}


def render_save_current_images(store, source, assets, actor, key, disabled=False):
    with st.expander("把本款採用圖片保存到共用圖庫"):
        st.caption("供未來草稿自動帶入；不更改本批核對、不修改原表圖片。已有不同圖片需明確確認替換，舊圖紀錄保留。")
        try:
            binding_key = "image_binding_revision_" + key
            if binding_key not in st.session_state:
                st.session_state[binding_key] = store.product_image_bindings().get(source["identity"])
            saved = st.session_state[binding_key]
            stale_binding = bool(saved and saved["subject"] != subject_key(source))
            reuse_stale = stale_binding and not assets
            selected_assets = assets
            different = False
            if reuse_stale:
                st.warning("商品型號／名稱變動後，舊圖不會自動帶入。只有逐張確認仍是同一款商品，才可沿用原綁定圖片。")
                previous = store.get_assets(saved["assets"])
                selected_assets = [previous[identity] for identity in saved["assets"]]
                columns = st.columns(min(5, len(selected_assets)))
                for number, asset in enumerate(selected_assets):
                    columns[number % len(columns)].image(
                        asset_bytes(asset), caption=f"變更前已綁定原圖 {number + 1} · {asset['name']}", width="stretch")
                replace = st.checkbox("我已逐張核對：變更前綁定原圖仍是本款同一商品",
                                      key="image_reuse_stale_" + key)
                button_label = "沿用舊圖並更新本商品配對"
            else:
                different = bool(saved and (stale_binding or saved["assets"] != [a["sha256"] for a in assets]))
                replace = (st.checkbox("我確認以目前採用圖片替換本商品的圖庫對應",
                                       key="image_replace_" + key) if different else False)
                button_label = "保存本款圖片到圖庫"
            if st.button(button_label, key="image_bind_one_" + key,
                         disabled=disabled or not selected_assets or len(selected_assets) > 5
                         or not actor.strip() or ((reuse_stale or different) and not replace)):
                result = store.save_product_images([{"source": source, "assets": selected_assets,
                    "expected_revision": saved.get("_revision", "") if saved else ""}],
                    actor=actor, origin="same_item_source_correction" if reuse_stale else "single_item_image_choice",
                    replace=replace)
                st.dataframe(result, hide_index=True, width="stretch")
                clear_library_cache()
                st.session_state.pop(binding_key, None)
                if result[0]["結果"] in {"已綁定", "已存在"}:
                    st.session_state["dispatch_notice"] = "本款圖片已保存到圖庫；未標記商品已核對。"
                    st.rerun()
                else:
                    st.error("圖片保存有待處理項目，請查看上方圖庫結果。")
        except Exception as exc:
            st.error(f"無法保存本款圖片對應：{exc}")
