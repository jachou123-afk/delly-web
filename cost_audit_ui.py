"""Internal cost evidence, deliberately separate from outbound ad copy."""
import streamlit as st

from cost_audit import INPUT_LABELS, UNITS, audit, fingerprint


def render_cost_review(store, source, prefix, actor):
    st.markdown("#### 成本與售價驗算（內部，不會放進 LINE 文案）")
    cache_key = "dispatch_cost_" + source["identity"] + source["source_hash"]
    if cache_key not in st.session_state:
        try:
            st.session_state[cache_key] = store.cost_audit(source)
        except Exception as exc:
            st.error(f"讀取成本依據失敗，不能確認：{exc}")
    loaded = st.session_state.get(cache_key)
    report, formulas = loaded if loaded else (audit(source), [])
    rows = {row["項目"]: row for row in report["rows"]}
    a, b, c = st.columns(3)
    a.metric("原表到手成本（TWD）", rows["到手成本（TWD）"]["原表／原售價"])
    b.metric("獨立重算成本（TWD）", rows["到手成本（TWD）"]["重算結果"])
    c.metric("原廣告售價（TWD）", rows["廣告售價（TWD）"]["原表／原售價"])
    st.table(report["rows"])
    if report["math_pass"]:
        st.success("計算：各步驟與獨立驗算一致；仍須核對原始數字與費用規則。")
    else:
        st.warning("計算：有差異或缺少依據，不能列為已核對。")
        for error in report["errors"]:
            st.caption(error)
    if report["source_ready"]:
        st.info("來源：原文與參數已保存；請核對下面的原文及數值，最後再勾選本款確認。")
    else:
        st.warning("來源：沒有這版商品的完整依據。下表可能只有雲表／公式中的數字，不能當作廠商原文。")
    st.table(report["input_rows"])
    if report["raw_source"]:
        with st.expander("廠商原文與當次處理依據", expanded=True):
            st.text(report["raw_source"])
            st.caption("依據來源：" + ("報價當次保存" if report["origin"] == "quote_save" else "人工補登，不冒充歷史原始紀錄"))
            st.text(report["notes"])
    st.caption("沿用現行規則：毛利率 10%（成本 ÷ 0.9）、重量加計 5%；木架／木框依原規則不列入，其他附加費用需已納入進價與重量。驗算一致不代表規則或原始數字已由人工確認。")
    if st.button("重新讀取成本與依據", key=prefix + "_cost_refresh"):
        st.session_state.pop(cache_key, None)
        store._evidence_index = None
        st.rerun()
    with st.expander("補存／修正原文與驗算依據（不改報價表）"):
        st.caption("舊資料可在這裡補一次：已有數字會預填；缺的參數需對照當時資料，不能套用今天的預設值。修改後需重新核對。")
        with st.form(prefix + "_cost_evidence_" + fingerprint(report)[:12]):
            raw = st.text_area("本款廠商完整原文（含補充費用）", report["raw_source"], height=160)
            inputs = {}
            columns = st.columns(3)
            for index, (key, label) in enumerate(INPUT_LABELS.items()):
                value = report["inputs"].get(key, "")
                if key == "unit":
                    options = [""] + sorted(UNITS)
                    inputs[key] = columns[index % 3].selectbox(label, options, index=options.index(value) if value in options else 0)
                else:
                    inputs[key] = columns[index % 3].text_input(label, value=value)
            notes = st.text_area("原文核對／參數來源／額外費用處理依據", report["notes"],
                                 placeholder="例如：原文進價及每箱數量相符；匯率與費率取當時報價紀錄；額外費用如何處理。")
            confirmed = st.checkbox("我確認以上是這款商品的來源及參數，不是由售價倒推")
            if st.form_submit_button("保存依據並重新驗算", disabled=not loaded):
                if not confirmed or not actor.strip():
                    st.error("請確認來源及參數，並填寫核對人。")
                else:
                    try:
                        store.put_quote_evidence(source, formulas, raw, inputs,
                                                 notes=f"核對人：{actor}\n{notes.strip()}" if notes.strip() else "",
                                                 origin="review_attachment")
                        st.session_state.pop(cache_key, None)
                        st.session_state["dispatch_notice"] = "已保存依據，原報價表未改寫；請確認重算結果後重新核對本款。"
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    return report if loaded else None
