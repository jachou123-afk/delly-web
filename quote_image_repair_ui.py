"""Standalone image repair for saved quotes; never creates a dispatch batch."""
from collections import Counter

import streamlit as st

from dispatch_manager import digest
from product_image_ui import clear_library_cache, render_quote_images
from quote_image_repair import prepare_image_repair, save_image_repair


PREFIX = "quote_image_repair_"


def render_image_repair(get_store):
    st.subheader("商品補圖")
    st.caption("商品已存入雲表，但圖片保存失敗時，在這裡單獨補圖。保存後會讀回核對，不需要重新新增商品。")
    with st.form(PREFIX + "search"):
        query = st.text_input("搜尋已保存商品（NO、貨號或品名）", key=PREFIX + "query")
        search = st.form_submit_button("搜尋商品")
    if search:
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop(PREFIX + "report", None)
        st.session_state.pop(PREFIX + "matches", None)
        st.session_state.pop(PREFIX + "target", None)
        if not query.strip():
            st.warning("請先輸入 NO、貨號或品名。")
        else:
            try:
                with st.spinner("讀取原雲表商品…"):
                    products = get_store().catalog()
                counts = Counter(p["identity"] for p in products)
                term = query.strip().casefold()
                matches = [p for p in products if any(term in str(p.get(field, "")).casefold()
                           for field in ("no", "code", "supplier_code", "name"))]
                if len(matches) > 50:
                    st.warning("符合的商品超過 50 款，請輸入更完整的 NO 或貨號。")
                else:
                    st.session_state[PREFIX + "matches"] = [p for p in matches if counts[p["identity"]] == 1]
                    if any(counts[p["identity"]] > 1 for p in matches):
                        st.error("有商品 NO 重複，已停止提供該商品的補圖選項。")
                    if not matches:
                        st.info("找不到符合的商品，請確認 NO 或貨號。")
            except Exception as exc:
                st.error(f"商品讀取未完成：{exc}")

    products = st.session_state.get(PREFIX + "matches", [])
    if not products:
        return
    by_id = {p["identity"]: p for p in products}
    chosen = st.selectbox("選擇要補圖的商品", list(by_id), key=PREFIX + "target",
                          format_func=lambda identity: " · ".join(str(by_id[identity].get(k, ""))
                          for k in ("identity", "supplier_code", "name", "vendor")))
    plan = st.session_state.get(PREFIX + "plan")
    if plan and plan["source"]["identity"] != chosen:
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop(PREFIX + "report", None)
        plan = None
    if st.button("載入本款補圖資料", key=PREFIX + "load"):
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop(PREFIX + "report", None)
        plan = None
        try:
            with st.spinner("重新核對商品與既有圖片配對…"):
                plan = prepare_image_repair(get_store(), chosen)
            st.session_state[PREFIX + "plan"] = plan
            st.session_state[PREFIX + "generation"] = st.session_state.get(PREFIX + "generation", 0) + 1
        except Exception as exc:
            st.error(f"尚未載入補圖資料：{exc}")
    if not plan:
        return

    source = plan["source"]
    st.markdown(f"**{source['identity']} · {source.get('supplier_code', '')} · {source['name']}**")
    st.caption(f"廠商：{source.get('vendor', '')}；原雲表：{source['category']} 第 {source['row']} 列。")
    binding = plan.get("binding")
    if binding:
        st.info(f"這款已有 {len(binding['assets'])} 張綁定圖片。上傳同一組原圖可讀回核對；此入口不替換既有不同圖片。")
    image_key = PREFIX + "uploads_" + digest([plan["snapshot_digest"], st.session_state.get(PREFIX + "generation", 0)])[:20]
    assets, errors = render_quote_images(image_key, title="本款原圖（只補圖片）",
                                         caption="每款最多 5 張，單張 2 MB。請核對圖片上的型號與上方商品一致。")
    selection = digest([plan["snapshot_digest"], [a["sha256"] for a in assets], errors])
    if st.session_state.get(PREFIX + "report_selection") != selection:
        st.session_state.pop(PREFIX + "report", None)
    for error in errors:
        st.error(error)
    st.caption("只保存本款原圖與商品配對；不修改品名、價格、原文或發送狀態。失敗時保留本次圖片，可再次按保存重試。")
    if st.button("只保存本款圖片", key=PREFIX + "save", disabled=not assets or bool(errors)):
        st.session_state.pop(PREFIX + "report", None)
        try:
            with st.spinner("保存圖片並讀回核對…"):
                report = save_image_repair(get_store(), plan, assets, actor="報價補圖操作者")
            st.session_state[PREFIX + "report"] = report
            st.session_state[PREFIX + "report_selection"] = selection
            if report and all(row["結果"] in {"已綁定", "已存在"} for row in report):
                clear_library_cache()
        except Exception as exc:
            st.error(f"圖片尚未確認完成：{exc}")
    report = st.session_state.get(PREFIX + "report")
    if report:
        st.dataframe(report, hide_index=True, width="stretch")
        if all(row["結果"] in {"已綁定", "已存在"} for row in report):
            st.success(f"{source['no']} 圖片已保存並完成讀回核對；原商品與原文保留。")
        else:
            st.error("圖片尚未全部保存，請查看原因。可重試保存圖片，不要重新新增商品。")
