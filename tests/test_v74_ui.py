"""Real Streamlit widgets, isolated fake storage; never accesses credentials."""
import ast
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def app_source(existing=False, failure=False, same_identity=False):
    source = Path(__file__).parents[1] / "dolly_parser.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    existing_name = "新品測試收納包" if same_identity else "別款商品"
    rows = [
        ["no1", existing_name, "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/個", "大陸運費rmb", "國際運費", "預估到手成本", "v多品村"],
        ["2026/9/12", "舊商品資訊", 60, 62, 64, 68, 9.3, 71.4, 0, 0.61, 47.6, ""],
        ["", "裝箱 300個/箱", "", "", "", "", "", "", "廣州包郵", "", "", ""],
        ["", "單個重量 68g", "", "", "", "", "", "", "", "", "", ""],
        ["", "貨號 A0081", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
    ] if (existing or same_identity) else []
    formula_rows = [row[:] for row in rows]
    if formula_rows:
        for col in (2, 3, 4, 5, 7, 8, 9, 10):
            formula_rows[1][col] = f"=OLD_{col}"
    fake_sheets = {title: rows if title == "G正版" else [] for title in ("G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾")}
    replacements = {
        "get_settings_cached": "return dict(ex_rate=4.8, intl_rate=8.5, dom_rate=1.5)",
        "get_all_sheets_data": f"return {None if failure else fake_sheets!r}",
        "get_target_formula_block": f"return {{'worksheet_id': 123, 'block': {formula_rows!r}}}",
        "save_bulk_to_worksheet": "st.session_state['test_saved_rows'] = bulk_rows\nreturn True",
        "update_existing_product": (
            "st.session_state['test_updated'] = {"
            "'category': category_name, 'base_row': base_row, 'no': expected_no, "
            "'new_block': new_block, 'safety_only': safety_only, "
            "'identity_evidence': identity_evidence}\nreturn True"
        ),
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
    return next(b for b in app.button if b.label == "💾 新增商品")


def correction_button(app):
    return next(
        b for b in app.button
        if b.label in ("🛠️ 套用原位修正", "🛡️ 原位清除不安全的衍生數字")
    )


def enter_correction_mode(app):
    next(r for r in app.radio if r.label == "操作方式").set_value("修正既有商品").run()
    return app


def choose_target(app):
    target = next(s for s in app.selectbox if s.label.startswith("🎯 要修正的原商品"))
    assert target.value == ""
    target.set_value("1|no1").run()
    return app


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


@pytest.mark.parametrize("raw", [VALID.replace("单价：9.3元\n", ""), VALID.replace("重量：68g(单个)", ""), VALID+"\n包裝費另加:15元", VALID+"\n整箱重量:90kg"])
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
    assert not any(b.label == "💾 新增商品" for b in app.button)


def test_confirmation_resets_after_edit():
    app = paste(VALID)
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    next(n for n in app.number_input if n.label == "進價(RMB)").set_value(10.0).run()
    assert save_button(app).disabled


def test_manual_non_rack_fee_note_reactivates_blocker():
    app = paste(VALID)
    extra = next(t for t in app.text_area if t.label.startswith("額外備註"))
    extra.set_value("材質：ABS\n打包費另加 15 元").run()
    assert save_button(app).disabled
    assert any("有非木架額外費用" in error.value for error in app.error)


def test_mc_z0001_wooden_rack_note_can_save_with_no_rack_cost_basis():
    raw = """新品#正版授权
三丽鸥浮雕系列茗芙4.5英寸饭碗
带镭射标/2个颜色
编号:MC-Z0001
每箱数量:48pcs
单个价格:12元
产品尺寸:12*6.8cm
整箱重量:约20.5kg
包装:牛皮纸盒
木架大约:4-7kg(15元)"""
    app = paste(raw)
    assert not app.exception
    assert not app.error
    extra = next(t for t in app.text_area if t.label.startswith("額外備註"))
    assert "木架大約:4-7kg(15元)" in extra.value
    assert "可加木架；成本按無木架的進價及重量計算" in extra.value
    labels = [item.label for item in app.text_input]
    assert "供應商確認依據／費用與重量換算說明" not in labels
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    save_button(app).click().run()
    rows = app.session_state["test_saved_rows"]
    assert rows[1][6] == 12
    assert "(20.5/48)*1000*1.05" in rows[1][7]
    formulas = [str(cell) for row in rows for cell in row if isinstance(cell, str) and cell.startswith("=")]
    assert all("4-7" not in formula for formula in formulas)


def test_optional_rack_note_deletion_blocks_save():
    raw = """新品#正版授权
三丽鸥浮雕系列茗芙4.5英寸饭碗
带镭射标/2个颜色
编号:MC-Z0001
每箱数量:48pcs
单个价格:12元
产品尺寸:12*6.8cm
整箱重量:约20.5kg
包装:牛皮纸盒
木架大约:4-7kg(15元)"""
    app = paste(raw)
    extra = next(t for t in app.text_area if t.label.startswith("額外備註"))
    extra.set_value("包裝:牛皮紙盒").run()
    assert any("木架的原文或無木架成本規則已被刪除" in error.value for error in app.error)
    app.checkbox[-1].check().run()
    assert save_button(app).disabled


def test_included_rack_remains_blocked_after_review():
    raw = """測試商品
型號:INCLUDED-RACK
每箱數量:48pcs
單個價格:15元(已含木架)
整箱重量:27.5kg"""
    app = paste(raw)
    app.checkbox[-1].check().run()
    assert save_button(app).disabled
    assert any("缺少明確無木架進價與重量" in error.value for error in app.error)


def test_correction_never_auto_selects_and_requires_bound_review():
    app = enter_correction_mode(paste(VALID, same_identity=True))
    assert correction_button(app).disabled
    choose_target(app)
    assert correction_button(app).disabled
    review = next(c for c in app.checkbox if c.label.startswith("我確認目標是 G正版 no1"))
    review.check().run()
    assert not correction_button(app).disabled
    correction_button(app).click().run()
    result = app.session_state["test_updated"]
    assert (result["category"], result["base_row"], result["no"]) == ("G正版", 1, "no1")
    assert result["new_block"][0][0] == "no1"
    assert result["new_block"][1][0] == "2026/9/12"
    assert result["identity_evidence"] == "貨號＋品名完全相符"


def test_one_identity_field_mismatch_needs_separate_confirmation():
    app = enter_correction_mode(paste(VALID, existing=True))
    choose_target(app)
    assert any("只有貨號符合" in warning.value for warning in app.warning)
    assert correction_button(app).disabled
    partial = next(c for c in app.checkbox if c.label.startswith("我已核對原圖／完整原文"))
    partial.check().run()
    assert correction_button(app).disabled
    review = next(c for c in app.checkbox if c.label.startswith("我確認目標是 G正版 no1"))
    review.check().run()
    assert not correction_button(app).disabled


def test_incomplete_correction_can_only_clear_derived_cells():
    raw = VALID.replace("重量：68g(单个)", "")
    app = enter_correction_mode(paste(raw, same_identity=True))
    choose_target(app)
    button = correction_button(app)
    assert button.label == "🛡️ 原位清除不安全的衍生數字"
    assert button.disabled
    next(c for c in app.checkbox if c.label.startswith("我確認目標是 G正版 no1")).check().run()
    assert not correction_button(app).disabled
    correction_button(app).click().run()
    result = app.session_state["test_updated"]
    assert result["safety_only"] is True
    assert result["new_block"][1][6] == 9.3
    assert all(result["new_block"][1][col] == "" for col in (2, 3, 4, 5, 7, 8, 9, 10))
