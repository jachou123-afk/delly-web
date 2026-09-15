"""Internal cost evidence, deliberately separate from outbound ad copy."""
import streamlit as st

from cost_audit import INPUT_LABELS, UNITS, audit, evidence_status, fingerprint
from quote_evidence import clear_evidence_caches


def render_cost_review(store, source, prefix, actor):
    st.markdown("#### 報價表重算與原文（內部，不會放進 LINE 文案）")
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
        st.success("表內重算一致：使用已保存的參數或原表／公式數字重算；不代表廠商原文已核對。")
    else:
        st.warning("計算：有差異或缺少依據，不能列為已核對。")
        for error in report["errors"]:
            st.caption(error)
    if report["source_ready"]:
        st.info("原文已保存，待核對。確認原文、參數與商品相符後，再勾選本款確認。")
    else:
        st.warning(evidence_status(report if loaded else None) + "。表內數字不是完整廠商原文；舊資料不會自動回補。")
    with st.expander("查看原文與計算參數"):
        st.caption(f"保存位置：Google 試算表「{report.get('storage_title', '原採購報價雲表')}」→「_報價依據」分頁；不是 Streamlit 暫存。")
        if report.get("storage_url"):
            st.link_button("開啟原 Google 雲表保存位置", report["storage_url"])
        saved = report.get("saved_evidence")
        if saved:
            st.caption("保存時間：" + (report.get("saved_at") or "舊紀錄未提供"))
            st.caption("保存方式：" + ("報價當次保存" if saved["origin"] == "quote_save" else "人工補存，不是當時的原始保存紀錄"))
            if not report["source_ready"]:
                st.warning("以下是已保存的舊版原文，不作為目前商品的有效核對依據。補存時請重新對照最新商品。")
            st.text(saved["raw_source"])
            st.text(saved["notes"])
        else:
            st.info("尚未保存這款廠商原文。下方參數來自現有報價表／公式，不能還原完整原文。")
        st.table(report["input_rows"])
        if saved and not report["source_ready"]:
            st.caption("舊版保存參數（僅供比對，不自動套用）：")
            st.table([{"項目": label, "舊版保存值": saved.get("inputs", {}).get(key, "未保存")}
                      for key, label in INPUT_LABELS.items()])
    st.caption("沿用現行規則：毛利率 10%（成本 ÷ 0.9）、重量加計 5%；木架／木框依原規則不列入，其他附加費用需已納入進價與重量。驗算一致不代表規則或原始數字已由人工確認。")
    if st.button("重新讀取成本與依據", key=prefix + "_cost_refresh"):
        st.session_state.pop(cache_key, None)
        store._evidence_index = None
        store._evidence_row_index = None
        st.rerun()
    with st.expander("補存／修正原文（不改商品與價格）"):
        st.caption("會存入原 Google 雲表的「_報價依據」，保留歷史版本。已有數字會預填；請對照當時資料，不套用今天的預設值。保存不代表核對完成。")
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
                        clear_evidence_caches(st.session_state)
                        st.session_state["dispatch_notice"] = "原文與參數已存入 Google 雲表「_報價依據」並讀回確認；商品與價格未改。舊驗算結果已清除，請重新驗算與核對。"
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    return report if loaded else None
