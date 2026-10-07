"""Standalone source-only supplementation for existing quotes."""
from collections import Counter

import streamlit as st

from cost_audit_ui import render_cost_review
from quote_evidence import clear_evidence_caches, prepare_evidence_repair, save_evidence_repair


PREFIX = "quote_evidence_repair_"


def render_evidence_repair(get_store):
    st.subheader("原文補存")
    st.caption("商品已保存而原文未保存時，依 NO 或貨號補存到原雲表的「_報價依據」。不新增商品、不改價格，也不需要建立廣告批次。")
    with st.form(PREFIX + "search"):
        query = st.text_input("搜尋已保存商品（NO、貨號或品名）", key=PREFIX + "query")
        search = st.form_submit_button("搜尋商品")
    if search:
        generation = st.session_state.get(PREFIX + "selector_generation", 0)
        st.session_state.pop(PREFIX + "target_" + str(generation), None)
        st.session_state[PREFIX + "selector_generation"] = generation + 1
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop(PREFIX + "matches", None)
        st.session_state.pop("dispatch_notice", None)
        if not query.strip():
            st.warning("請先輸入 NO、貨號或品名。")
        else:
            try:
                products = get_store().catalog()
                counts = Counter(product["identity"] for product in products)
                term = query.strip().casefold()
                matches = [product for product in products if any(
                    term in str(product.get(field, "")).casefold()
                    for field in ("no", "code", "supplier_code", "name"))]
                if len(matches) > 50:
                    st.warning("符合的商品超過 50 款，請輸入更完整的 NO 或貨號。")
                else:
                    st.session_state[PREFIX + "matches"] = [
                        product for product in matches if counts[product["identity"]] == 1]
                    if any(counts[product["identity"]] > 1 for product in matches):
                        st.error("有商品 NO 重複，已停止提供該商品的原文補存選項。")
                    if not matches:
                        st.info("找不到符合的商品，請確認 NO 或貨號。")
            except Exception as exc:
                st.error(f"商品讀取未完成：{exc}")
    products = st.session_state.get(PREFIX + "matches", [])
    if not products:
        return
    by_id = {product["identity"]: product for product in products}
    target_key = PREFIX + "target_" + str(st.session_state.get(PREFIX + "selector_generation", 0))
    chosen = st.selectbox("選擇要補存原文的商品", list(by_id), key=target_key,
                          format_func=lambda identity: " · ".join(str(by_id[identity].get(key, ""))
                          for key in ("identity", "supplier_code", "name", "vendor")))
    plan = st.session_state.get(PREFIX + "plan")
    if plan and plan["source"]["identity"] != chosen:
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop("dispatch_notice", None)
        plan = None
    if st.button("載入本款原文與參數", key=PREFIX + "load"):
        st.session_state.pop(PREFIX + "plan", None)
        st.session_state.pop("dispatch_notice", None)
        plan = None
        clear_evidence_caches(st.session_state)
        try:
            plan = prepare_evidence_repair(get_store(), chosen)
            st.session_state[PREFIX + "plan"] = plan
            st.session_state[PREFIX + "generation"] = st.session_state.get(PREFIX + "generation", 0) + 1
        except Exception as exc:
            st.error(f"尚未載入補存資料：{exc}")
    if not plan:
        return
    source = plan["source"]
    st.markdown(f"**{source['identity']} · {source.get('supplier_code', '')} · {source['name']}**")
    st.caption(f"廠商：{source.get('vendor', '')}；原雲表：{source['category']} 第 {source['row']} 列。")
    st.info("參數只從本款原表及公式的明確數字預填；無法辨識的項目保持空白。請對照完整原文與當時紀錄，不能從售價倒推。")
    if st.session_state.get("dispatch_notice"):
        st.success(st.session_state.pop("dispatch_notice"))
    actor = st.text_input("補存核對人", key=PREFIX + "actor")
    prefix = PREFIX + plan["snapshot_digest"] + "_" + str(st.session_state.get(PREFIX + "generation", 0))
    try:
        store = get_store()
        render_cost_review(store, source, prefix, actor,
                           evidence_saver=lambda raw, inputs, **kwargs: save_evidence_repair(
                               get_store(), plan, raw, inputs, **kwargs))
    except Exception as exc:
        st.error(f"補存資料讀取未完成：{exc}")
