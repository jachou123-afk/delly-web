"""Retry only an already-written product's evidence, never its quotation write."""
from copy import deepcopy

from cost_audit import block, fingerprint, number
from dispatch_manager import catalog

PENDING_KEY = "pending_quote_evidence"


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
    ws = store.spreadsheet.worksheet(category)
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
