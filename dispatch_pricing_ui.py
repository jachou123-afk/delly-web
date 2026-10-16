"""Explicit draft price-policy controls, through the existing batch save path."""
import streamlit as st

from dispatch_manager import apply_batch_pricing
from dispatch_pricing import LEGACY, POLICY_LABELS, price_line, validate_policy, batch_pricing_errors


def select_pricing_policy(key, current=LEGACY):
    try:
        validate_policy(current)
    except ValueError:
        st.error("本批定價規則無效，請先處理資料；不自動改回舊價格。")
        return None
    return st.selectbox("廣告定價規則", list(POLICY_LABELS),
                        index=list(POLICY_LABELS).index(current),
                        format_func=POLICY_LABELS.get, key=key)


def render_draft_pricing(store, batch, save):
    with st.expander("本批廣告定價規則"):
        prefix = "dispatch_pricing_" + batch["id"] + batch.get("_revision", "")
        policy = select_pricing_policy(prefix, batch.get("pricing_policy", LEGACY))
        st.caption("只調整本批廣告售價；原表成本、10% 報價及歷史已確認批次保持原樣。")
        if policy is None:
            return
        if policy == batch.get("pricing_policy", LEGACY) and not batch_pricing_errors(batch):
            st.caption("目前已採用此規則；選擇其他規則可預覽本批售價。")
            return
        try:
            preview = apply_batch_pricing(batch, policy, batch["actor"])
        except ValueError as exc:
            st.error(str(exc))
            return
        active = [i for i in preview["items"] if not i["excluded"]]
        st.dataframe([{"品號": i["source"]["code"], "本批售價": price_line(i),
                       "採用規則": POLICY_LABELS[policy]} for i in active],
                     hide_index=True, width="stretch")
        if st.button("套用本批定價規則並儲存草稿", key=prefix + "_apply"):
            save(store, preview, batch)
