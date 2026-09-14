"""Read-only draft preview: batch choices first, then clearly labelled library images."""
import streamlit as st

from dispatch_storage import asset_bytes


def preview_plan(item, references):
    if item["images"]:
        return {"ids": list(item["images"]), "inline": [], "label": "本批已保存", "pending": ""}
    if references and all(r.get("stored_id") and r.get("binding_revision") for r in references):
        return {"ids": [r["stored_id"] for r in references], "inline": [],
                "label": "圖庫自動帶入（未保存本批核對）", "pending": ""}
    if len(references) == 1 and not references[0].get("stored_id"):
        return {"ids": [], "inline": references, "label": "圖片包候選（待確認／未保存）", "pending": ""}
    return {"ids": [], "inline": [], "label": "",
            "pending": "有多張候選，請到單款明細選擇" if references else "尚無可用配對圖片，請補圖或檢查上方圖庫提示"}


def render_batch_image_preview(store, items, source_images, batch_id):
    key = "dispatch_all_images_" + batch_id
    with st.expander("整批廣告預覽", expanded=bool(st.session_state.get(key))):
        plans = {i["id"]: preview_plan(i, source_images.get(i["id"], [])) for i in items if not i["excluded"]}
        available = sum(bool(p["ids"] or p["inline"]) for p in plans.values())
        st.caption(f"本批 {len(plans)} 款：{available} 款有圖片可預覽，{len(plans) - available} 款待補圖／確認配對。")
        st.caption("本批已保存的圖片優先；未保存者顯示圖庫圖片或唯一候選。這是草稿預覽，不代表已核對或可發送；單款尚未儲存的手動修改不包含在這裡。")
        show = st.checkbox("載入整批商品圖片", key=key,
                           help="勾選後顯示本批全部可用圖片；不修改核對、價格或 LINE 狀態。")
        failures = {}
        if show:
            wanted = list(dict.fromkeys(a for p in plans.values() for a in p["ids"]
                                       if "dispatch_asset_" + a not in st.session_state))
            if wanted:
                with st.spinner(f"讀取 {len(wanted)} 張原圖供整批預覽…"):
                    # Small groups avoid one network request per product. A failed
                    # group is reported on its products, never silently replaced.
                    for start in range(0, len(wanted), 10):
                        group = wanted[start:start + 10]
                        try:
                            loaded = store.get_assets(group)
                            for asset_id in group:
                                asset_bytes(loaded[asset_id])
                            for asset_id in group:
                                st.session_state["dispatch_asset_" + asset_id] = loaded[asset_id]
                        except Exception as exc:
                            failures.update({a: str(exc) for a in group})
        for item in items:
            code = item["source"]["code"] or item["id"]
            if item["excluded"]:
                st.caption(f"{code} · 已排除：{item['reason']}")
                continue
            plan = plans[item["id"]]
            st.write(f"{item['order']}. {code}｜{item['source']['name']}")
            left, right = st.columns([1, 2])
            with left:
                if plan["pending"]:
                    st.warning(plan["pending"])
                else:
                    count = len(plan["ids"]) + len(plan["inline"])
                    st.caption(f"{plan['label']} · {count} 張" + ("" if show else "；勾選上方「載入整批商品圖片」查看"))
                    if show:
                        for asset_id in plan["ids"]:
                            try:
                                if asset_id in failures:
                                    raise ValueError(failures[asset_id])
                                asset = st.session_state["dispatch_asset_" + asset_id]
                                st.image(asset_bytes(asset), caption=asset["name"], width="stretch")
                            except Exception as exc:
                                st.error(f"{code} 圖片讀取失敗：{exc}；未當成缺圖，未換成其他圖片。")
                        for asset in plan["inline"]:
                            try:
                                st.image(asset_bytes(asset), caption=asset["name"], width="stretch")
                            except Exception as exc:
                                st.error(f"{code} 圖片候選無法讀取：{exc}")
            with right:
                if item["copy"]:
                    st.code(item["copy"], language=None)
                else:
                    st.warning("文案尚未產生，請查看本款待處理原因。")
            st.divider()
