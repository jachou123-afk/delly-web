"""Batch new-quote choices and consume each confirmation exactly once.

This module never reads or writes cloud data. The caller must validate the
returned choices, run its current safety checks, and perform the write.
"""

import hashlib

import streamlit as st


CONFIRM_LABEL = "我已逐欄對照原文、補充資訊及廠商，確認新增這 1 款商品"


def create_basis_digest(basis):
    """Fingerprint all caller-supplied fields outside the creation form."""
    return hashlib.sha256(repr(basis).encode("utf-8")).hexdigest()


def advance_create_revision(previous, basis_digest):
    """Return a monotonic revision; changing A -> B -> A never reuses A."""
    previous = previous or {}
    if previous.get("basis") == basis_digest:
        return dict(previous)
    return {
        "basis": basis_digest,
        "revision": previous.get("revision", 0) + 1,
    }


def _capture_submission(receipt_key, selection_key, widget_keys, basis, revision):
    """Read freshly submitted values, then consume the checkbox before rerun."""
    category_key, vendor_key, confirm_key = widget_keys
    selection = {
        "category": st.session_state.get(category_key, ""),
        "vendor": st.session_state.get(vendor_key, ""),
    }
    st.session_state[receipt_key] = {
        **selection,
        "confirmed": bool(st.session_state.get(confirm_key, False)),
        "basis": basis,
        "revision": revision,
    }
    st.session_state[selection_key] = selection
    st.session_state[confirm_key] = False


def render_create_form(*, basis, categories, vendors, disabled=False,
                       key_prefix="quote_create"):
    """Return a one-use submission dict, or None when nothing was submitted.

    ``basis`` must contain every material field outside this form, including
    source/draft identity, edited product fields, costs, notes, and image hashes.
    The returned ``basis`` is its digest. ``confirmed`` records only this submit's
    checkbox; the caller must still reject missing/invalid choices and all other
    safety blockers. Category/vendor/checkbox values do not control widget keys
    or the button's disabled state.
    """
    basis_digest = create_basis_digest(basis)
    revision_key = f"_{key_prefix}_revision"
    receipt_key = f"_{key_prefix}_receipt"
    selection_key = f"_{key_prefix}_selection"
    state = advance_create_revision(st.session_state.get(revision_key), basis_digest)
    st.session_state[revision_key] = state
    revision = state["revision"]
    scope = f"{key_prefix}_{revision}"
    widget_keys = (f"{scope}_category", f"{scope}_vendor", f"{scope}_confirm")
    category_options = list(dict.fromkeys(("", *categories)))
    vendor_options = list(dict.fromkeys(("", *vendors)))
    previous_selection = st.session_state.get(selection_key, {})
    category_default = previous_selection.get("category", "")
    vendor_default = previous_selection.get("vendor", "")

    with st.form(f"{scope}_form", enter_to_submit=False):
        st.selectbox(
            "📂 分頁", category_options,
            index=category_options.index(category_default) if category_default in category_options else 0,
            format_func=lambda value: value or "請選擇本款分頁",
            key=widget_keys[0],
        )
        st.selectbox(
            "🏷️ 廠商", vendor_options,
            index=vendor_options.index(vendor_default) if vendor_default in vendor_options else 0,
            format_func=lambda value: value or "請明確選擇本款廠商（不依貨號或格式猜測）",
            key=widget_keys[1],
        )
        st.checkbox(CONFIRM_LABEL, key=widget_keys[2])
        submitted = st.form_submit_button(
            "💾 新增商品", type="primary", disabled=disabled,
            on_click=_capture_submission,
            args=(receipt_key, selection_key, widget_keys, basis_digest, revision),
        )

    # Pop even an obsolete receipt, so another rerun cannot replay a request.
    receipt = st.session_state.pop(receipt_key, None)
    if not submitted or disabled or receipt is None:
        return None
    if receipt["basis"] != basis_digest or receipt["revision"] != revision:
        return None
    return receipt
