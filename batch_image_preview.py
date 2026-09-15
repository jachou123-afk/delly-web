"""Six-product pages of display-only thumbnails; original choices never change."""
import math
import streamlit as st

from dispatch_storage import asset_bytes
from image_cache import cached, scope_cache, THUMB_PREFIX
from image_thumbnails import make_thumbnail

PAGE_SIZE = 6


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
    scope_cache(st.session_state, store)
    key = "dispatch_page_thumbs_" + batch_id
    with st.expander("整批廣告預覽", expanded=bool(st.session_state.get(key))):
        plans = {i["id"]: preview_plan(i, source_images.get(i["id"], [])) for i in items if not i["excluded"]}
        available = sum(bool(p["ids"] or p["inline"]) for p in plans.values())
        st.caption(f"本批 {len(plans)} 款：{available} 款有圖片可預覽，{len(plans) - available} 款待補圖／確認配對。")
        st.caption("每頁 6 款，只讀本頁縮圖；文字細節請到上方「逐款核對」選品號看原圖。"
                   "縮圖不代表已核對，不會替換本批發送原圖；未保存的手動文案修改不包含在預覽。")
        pages = max(1, math.ceil(len(items) / PAGE_SIZE))
        page_key = "dispatch_preview_page_" + batch_id
        st.session_state[page_key] = min(pages, max(1, st.session_state.get(page_key, 1)))
        left, middle, right = st.columns([1, 2, 1])
        def move_page(delta):
            st.session_state[page_key] = min(pages, max(1, st.session_state[page_key] + delta))
        left.button("上一頁", key=key + "_prev", disabled=st.session_state[page_key] <= 1,
                    on_click=move_page, args=(-1,))
        right.button("下一頁", key=key + "_next", disabled=st.session_state[page_key] >= pages,
                     on_click=move_page, args=(1,))
        page = middle.selectbox("預覽頁碼", list(range(1, pages + 1)), key=page_key,
                                format_func=lambda n: f"第 {n}／{pages} 頁")
        start = (page - 1) * PAGE_SIZE
        visible = items[start:start + PAGE_SIZE]
        st.write(f"本頁第 {start + 1 if visible else 0}～{start + len(visible)} 款／清單共 {len(items)} 款（含暫緩）")
        show = st.checkbox("載入本頁縮圖", key=key)
        failures, loaded = {}, {}
        if show:
            wanted = list(dict.fromkeys(a for i in visible if not i["excluded"]
                          for a in plans[i["id"]]["ids"] if THUMB_PREFIX + a not in st.session_state))
            if wanted:
                try:
                    with st.spinner(f"讀取本頁 {len(wanted)} 張縮圖…"):
                        loaded = (store.get_thumbnails(wanted) if hasattr(store, "get_thumbnails") else
                                  {k: make_thumbnail(v) for k, v in store.get_assets(wanted).items()})
                except Exception:
                    failures = {a: "縮圖讀取失敗；未改用原圖或其他商品圖" for a in wanted}
        for item in visible:
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
                    st.caption(plan["label"] + " · 預覽縮圖（非發送原檔）")
                    if show:
                        for identity in plan["ids"]:
                            if identity in failures:
                                st.error(f"{code}：{failures[identity]}")
                            elif THUMB_PREFIX + identity not in st.session_state and identity not in loaded:
                                st.info("縮圖尚未建立，請到上方「NAS 縮圖管理」建立；原圖仍可在單款核對查看。")
                            else:
                                try:
                                    asset = cached(st.session_state, THUMB_PREFIX, identity, lambda: loaded[identity])
                                    st.image(asset_bytes(asset), caption=asset["name"], width="stretch")
                                except Exception:
                                    st.error(f"{code}：縮圖校驗失敗，未使用其他圖片。")
                        for asset in plan["inline"]:
                            try:
                                thumb = cached(st.session_state, THUMB_PREFIX, "inline_" + asset["sha256"],
                                               lambda: make_thumbnail(asset))
                                st.image(asset_bytes(thumb), caption=asset["name"], width="stretch")
                            except Exception:
                                st.error(f"{code}：圖片候選縮圖無法讀取")
            with right:
                if item["copy"]:
                    st.code(item["copy"], language=None)
                else:
                    st.warning("文案尚未產生，請查看本款待處理原因。")
            st.divider()
