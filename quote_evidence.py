"""Retry only an already-written product's evidence, never its quotation write."""
from copy import deepcopy

from cost_audit import audit, block, fingerprint, make_evidence, number
from dispatch_manager import catalog

PENDING_KEY = "pending_quote_evidence"


def _unique_evidence_source(store, identity):
    products = store.catalog()
    matches = [source for source in products if source["identity"] == identity]
    if len(matches) != 1:
        raise ValueError("商品不存在或 NO 重複，不能補存原文；請重新搜尋")
    source = matches[0]
    if (type(source.get("row")) is not int or source["row"] < 1
            or sum(p["category"] == source["category"] and p["row"] == source["row"]
                   for p in products) != 1):
        raise ValueError("商品位置無法唯一識別，不能補存原文")
    return deepcopy(source)


def prepare_evidence_repair(store, identity):
    """Capture one current quote without creating products or dispatch drafts."""
    source = _unique_evidence_source(store, identity)
    snapshot = {"source": source, "formulas": block(store.read_cost_source(source))}
    return {**snapshot, "snapshot_digest": fingerprint(snapshot)}


def save_evidence_repair(store, plan, raw_source, inputs, *, notes):
    """Supplement evidence only when the current source and costs agree exactly."""
    try:
        snapshot = {key: plan[key] for key in ("source", "formulas")}
        if fingerprint(snapshot) != plan["snapshot_digest"]:
            raise ValueError
        identity = plan["source"]["identity"]
    except (KeyError, TypeError, ValueError):
        raise ValueError("原文補存快照不完整或已變更，請重新載入") from None
    source = _unique_evidence_source(store, identity)
    if fingerprint(source) != fingerprint(plan["source"]):
        raise ValueError("商品來源已變更，未補存原文；請重新載入")
    formulas = block(store.read_cost_source(source))
    if formulas != plan["formulas"]:
        raise ValueError("原公式已變更，未補存原文；請重新載入")
    evidence = make_evidence(source, formulas, raw_source, inputs, notes=notes,
                             origin="review_attachment")
    report = audit(source, formulas, evidence)
    if not report["math_pass"]:
        raise ValueError("原文參數與原商品尚未核對一致，未保存：" + "；".join(report["errors"]))
    store._evidence_index = None
    store._evidence_row_index = None
    saved = store.put_quote_evidence(source, formulas, raw_source, inputs,
                                     notes=notes, origin="review_attachment")
    store._evidence_index = None
    store._evidence_row_index = None
    report, _ = store.cost_audit(source)
    if (not report["source_ready"] or not report["math_pass"]
            or report.get("saved_evidence") != evidence or saved != evidence):
        raise ValueError("原文補存讀回尚未確認，請重新讀取本款依據；不要新增商品")
    return saved


def clear_evidence_caches(state):
    for key in list(state):
        if key.startswith(("dispatch_cost_", "dispatch_bulk_result_")):
            state.pop(key, None)


def queue_evidence(state, category, base_row, expected_block, raw_source, inputs, parsed, notes):
    pending = deepcopy(dict(category=category, base_row=base_row, expected_block=expected_block,
                            raw_source=raw_source, inputs=inputs, parsed=parsed, notes=notes))
    identity = fingerprint(pending)
    state.setdefault(PENDING_KEY, {})[identity] = pending
    return identity, pending


def save_evidence(store, pending):
    category, base_row = pending["category"], pending["base_row"]
    ws = store.worksheet(category)
    area = f"A{base_row}:L{base_row + 5}"
    values = block(ws.get(area, value_render_option="FORMATTED_VALUE"))
    formulas = block(ws.get(area, value_render_option="FORMULA"))
    expected = block(pending["expected_block"])
    coords = [(0, 0), (0, 1), (0, 11), (1, 1), (2, 1), (3, 1), (4, 1)]
    if any(formulas[r][c] != expected[r][c] for r, c in coords):
        raise ValueError("商品身分或來源內容已變更；請保留原文，到這款商品補存並重新核對")
    if number(formulas[1][6]) != number(expected[1][6]):
        raise ValueError("進價已變更，不能將舊參數套用到新版商品")
    if any(formulas[1][c] != expected[1][c] for c in (2, 3, 4, 5, 7, 8, 9, 10)):
        raise ValueError("保存後公式已變更，請重新核對當次參數")
    products = catalog({category: values}, store.category_settings()["codes"])
    if len(products) != 1:
        raise ValueError("無法唯一識別已保存商品")
    source = products[0]
    source["row"] = base_row
    return store.put_quote_evidence(source, formulas, pending["raw_source"], pending["inputs"],
                                    notes=pending["notes"], origin="quote_save", parsed=pending["parsed"])


def render_pending_evidence(store_factory):
    import streamlit as st
    pending = st.session_state.get(PENDING_KEY, {})
    if not pending:
        return
    st.warning("商品已寫入，但原文保存尚未確認。不要重複新增商品；請先重試。未確認雲端保存前，請勿關閉此頁。")
    for identity, snapshot in list(pending.items()):
        st.write(f"待確認：{snapshot['category']}｜{snapshot['expected_block'][0][0]}｜{snapshot['expected_block'][0][1]}")
        if snapshot.get("last_error"):
            st.caption("上次未完成原因：" + snapshot["last_error"])
        with st.expander("查看待保存原文（目前僅暫存在此工作階段）"):
            st.text(snapshot["raw_source"])
        if st.button("重試保存原文與參數（不重複新增商品）", key="retry_evidence_" + identity):
            try:
                save_evidence(store_factory(), snapshot)
            except Exception as exc:
                snapshot["last_error"] = str(exc)
                st.error(f"尚未確認保存成功：{exc}")
            else:
                pending.pop(identity, None)
                clear_evidence_caches(st.session_state)
                st.session_state["quote_evidence_notice"] = "原文與當次參數已存入原 Google 雲表的「_報價依據」，並完成讀回核對。"
                st.rerun()
