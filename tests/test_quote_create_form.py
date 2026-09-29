from streamlit.testing.v1 import AppTest

from quote_create_form import advance_create_revision, create_basis_digest


APP = '''
import streamlit as st
from quote_create_form import render_create_form

price = st.number_input("進價", value=10.0)
notes = st.text_input("備註", value="原文備註")
blocked = st.checkbox("外部安全阻擋")
receipt = render_create_form(
    basis={"draft": 1, "price": price, "notes": notes},
    categories=["G正版", "T玩具"], vendors=["v甲", "v乙"],
    disabled=blocked,
)
if receipt is not None:
    st.session_state.setdefault("receipts", []).append(receipt)
st.session_state["current_receipt"] = receipt
'''


def start():
    app = AppTest.from_string(APP).run()
    assert not app.exception
    return app


def confirmation(app):
    return app.checkbox[1]


def submit(app, category="G正版", vendor="v甲", confirmed=True):
    app.selectbox[0].set_value(category)
    app.selectbox[1].set_value(vendor)
    confirmation(app).set_value(confirmed)
    app.button[0].click().run()
    assert not app.exception
    return app.session_state["current_receipt"]


def test_first_submit_receives_new_choices_and_confirmation_together():
    app = start()
    assert not confirmation(app).value
    assert not app.button[0].disabled
    receipt = submit(app, "T玩具", "v乙")
    assert receipt == {
        "category": "T玩具", "vendor": "v乙", "confirmed": True,
        "basis": create_basis_digest({"draft": 1, "price": 10.0, "notes": "原文備註"}),
        "revision": 1,
    }
    assert not confirmation(app).value
    assert app.selectbox[0].value == "T玩具"
    assert app.selectbox[1].value == "v乙"


def test_submission_without_check_is_not_confirmed():
    app = start()
    receipt = submit(app, confirmed=False)
    assert receipt["confirmed"] is False
    assert not confirmation(app).value


def test_each_submission_requires_a_new_confirmation_even_when_choices_change():
    app = start()
    assert submit(app)["confirmed"] is True
    app.selectbox[0].set_value("T玩具")
    app.selectbox[1].set_value("v乙")
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["current_receipt"]["confirmed"] is False
    assert app.session_state["current_receipt"]["category"] == "T玩具"
    assert app.session_state["current_receipt"]["vendor"] == "v乙"
    assert submit(app, "T玩具", "v乙")["confirmed"] is True


def test_ordinary_rerun_does_not_replay_a_submission():
    app = start()
    submit(app)
    app.run()
    assert not app.exception
    assert app.session_state["current_receipt"] is None
    assert len(app.session_state["receipts"]) == 1


def test_changed_external_basis_resets_confirmation_and_never_revives_old_key():
    app = start()
    original_key = confirmation(app).key
    submit(app)
    app.number_input[0].set_value(12.0).run()
    assert not app.exception
    second_key = confirmation(app).key
    assert second_key != original_key
    assert not confirmation(app).value
    assert app.session_state["current_receipt"] is None
    app.number_input[0].set_value(10.0).run()
    assert not app.exception
    assert confirmation(app).key not in {original_key, second_key}
    assert not confirmation(app).value
    receipt = submit(app)
    assert receipt["revision"] == 3


def test_external_notes_change_invalidates_review_and_retains_submitted_choices():
    app = start()
    submit(app, "T玩具", "v乙")
    previous_key = confirmation(app).key
    app.text_input[0].set_value("新增附加費用說明").run()
    assert not app.exception
    assert confirmation(app).key != previous_key
    assert not confirmation(app).value
    assert app.selectbox[0].value == "T玩具"
    assert app.selectbox[1].value == "v乙"


def test_cost_change_in_same_event_cannot_authorize_the_previous_form_basis():
    app = start()
    app.number_input[0].set_value(12.0)
    app.selectbox[0].set_value("G正版")
    app.selectbox[1].set_value("v甲")
    confirmation(app).check()
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["current_receipt"] is None
    assert not confirmation(app).value
    assert submit(app)["confirmed"] is True


def test_external_safety_block_disables_submit_independently_of_confirmation():
    app = start()
    app.checkbox[0].check().run()
    assert app.button[0].disabled
    assert app.session_state["current_receipt"] is None


def test_revision_helper_is_monotonic_without_mutating_previous_state():
    first = advance_create_revision(None, "A")
    unchanged = advance_create_revision(first, "A")
    second = advance_create_revision(first, "B")
    third = advance_create_revision(second, "A")
    assert first == unchanged == {"basis": "A", "revision": 1}
    assert second == {"basis": "B", "revision": 2}
    assert third == {"basis": "A", "revision": 3}
