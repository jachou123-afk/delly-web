"""Real Streamlit widgets, isolated fake storage; never accesses credentials."""
import ast
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def app_source(existing=False, failure=False):
    source = Path(__file__).parents[1] / "dolly_parser.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    rows = [["no1", "別款商品"], [], [], [], ["", "貨號 A0081"]] if existing else []
    fake_sheets = {title: rows if title == "G正版" else [] for title in ("G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾")}
    replacements = {
        "get_settings_cached": "return dict(ex_rate=4.8, intl_rate=8.5, dom_rate=1.5)",
        "get_all_sheets_data": f"return {None if failure else fake_sheets!r}",
        "save_bulk_to_worksheet": "st.session_state['test_saved_rows'] = bulk_rows\nreturn True",
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in replacements:
            node.body = ast.parse(replacements[node.name]).body
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def paste(raw, **kwargs):
    app = AppTest.from_string(app_source(**kwargs), default_timeout=15).run()
    app.text_area[0].set_value(raw).run()
    assert not app.exception
    return app


VALID = "新品測試收納包\n编号：A0081\n箱数：300pcs\n单价：9.3元\n尺寸：10*8.5*2.5cm\n重量：68g(单个)\n包装：12个/opp袋"


def save_button(app):
    return next(b for b in app.button if b.label == "💾 執行存檔")


def test_manual_paste_review_then_save():
    app = paste(VALID)
    assert save_button(app).disabled
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    save_button(app).click().run()
    assert not app.exception
    rows = app.session_state["test_saved_rows"]
    assert rows[4][1] == "貨號 A0081"
    assert "68*1.05" in rows[1][7] or "68.0*1.05" in rows[1][7]
    assert rows[2][1] == "裝箱 300個/箱"


@pytest.mark.parametrize("raw", [VALID.replace("单价：9.3元\n", ""), VALID.replace("重量：68g(单个)", ""), VALID+"\n木架:4-7kg(15元)", VALID+"\n整箱重量:90kg"])
def test_incomplete_or_uncertain_remains_disabled_after_review(raw):
    app = paste(raw)
    app.checkbox[-1].check().run()
    assert save_button(app).disabled
    assert app.error


def test_duplicate_conflict_cannot_save():
    app = paste(VALID, existing=True)
    app.checkbox[-1].check().run()
    assert save_button(app).disabled
    assert any("同貨號不同品名" in e.value for e in app.error)


def test_failed_read_cannot_save():
    app = paste(VALID, failure=True)
    assert not any(b.label == "💾 執行存檔" for b in app.button)


def test_confirmation_resets_after_edit():
    app = paste(VALID)
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    next(n for n in app.number_input if n.label == "進價(RMB)").set_value(10.0).run()
    assert save_button(app).disabled
