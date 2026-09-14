"""Real Streamlit widgets, isolated fake storage; never accesses credentials."""
import ast
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def app_source(existing=False, failure=False, same_identity=False, ad_failure=False, ad_invalid=False, existing_vendor="v多品村"):
    source = Path(__file__).parents[1] / "dolly_parser.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    existing_name = "新品測試收納包" if same_identity else "別款商品"
    rows = [
        ["no1", existing_name, "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/個", "大陸運費rmb", "國際運費", "預估到手成本", existing_vendor],
        ["2026/9/12", "計價單位：個\n尺寸 10*8.5*2.5cm\n外箱尺寸 20*20*20cm\n木架另加15元", 52.9, 54.7, 56, 59.5, 9.3, 71.4, 0, 0.61, 47.6, ""],
        ["", "裝箱 300個/箱", "", "", "", "", "", "", "廣州包郵", "", "", ""],
        ["", "單個重量 68g", "", "", "", "", "", "", "", "", "", ""],
        ["", "貨號 A0081", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
    ] if (existing or same_identity) else []
    if ad_invalid:
        rows[1][10] = ""
    formula_rows = [row[:] for row in rows]
    if formula_rows:
        for col in (2, 3, 4, 5, 7, 8, 9, 10):
            formula_rows[1][col] = f"=OLD_{col}"
    fake_sheets = {title: rows if title == "G正版" else [] for title in ("G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾")}
    replacements = {
        "persist_quote_evidence": "st.session_state['test_evidence'] = dict(raw=raw_source, inputs=inputs, parsed=parsed, notes=notes)\nreturn True",
        "get_settings_cached": "return dict(ex_rate=4.8, intl_rate=8.5, dom_rate=1.5)",
        "get_all_sheets_data": f"return {None if failure else fake_sheets!r}",
        "get_target_formula_block": f"return {{'worksheet_id': 123, 'block': {formula_rows!r}}}",
        "get_fresh_line_ad_block": "raise ValueError('雲表商品已變更')" if ad_failure else f"return {rows!r}",
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


def paste(raw, select_source=True, **kwargs):
    app = AppTest.from_string(app_source(**kwargs), default_timeout=15).run()
    app.text_area[0].set_value(raw).run()
    if select_source:
        next(s for s in app.selectbox if s.label == "📂 分頁").set_value("G正版").run()
        if not kwargs.get("failure"):
            next(s for s in app.selectbox if s.label == "🏷️ 廠商").set_value("v菲凡").run()
    assert not app.exception
    return app


VALID = "新品測試收納包\n编号：A0081\n箱数：300pcs\n单价：9.3元\n尺寸：10*8.5*2.5cm\n重量：68g(单个)\n包装：12个/opp袋"
VENDOR_INLINE_CARTON = """FF806274，不带电 线控起重机是[Fireworks]24.6元，一箱30只，27.5KG
彩盒尺寸46.5*6.3*40.2CM
外箱规格98.5*48*83.5CM"""


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


def test_line_ad_preview_reads_saved_block_and_filters_internal_fields():
    app = AppTest.from_string(app_source(existing=True), default_timeout=15).run()
    loader = next(c for c in app.checkbox if c.label == "載入雲表商品")
    loader.check().run()
    product = next(
        s for s in app.selectbox
        if s.label.startswith("選擇商品（可搜尋")
    )
    product.set_value("1|no1").run()
    button = next(b for b in app.button if b.label == "檢查並產生 LINE 文案")
    assert button.disabled
    assert not any("BGD-G-1" in code.value for code in app.code)
    next(c for c in app.checkbox if c.label.startswith("我已逐欄對照本款完整原文")).check().run()
    next(b for b in app.button if b.label == "檢查並產生 LINE 文案").click().run()

    assert not app.exception
    copy_text = next(code.value for code in app.code if "BGD-G-1" in code.value)
    assert "尺寸 10*8.5*2.5cm" in copy_text
    assert "外箱" not in copy_text
    assert "重量" not in copy_text
    assert "木架" not in copy_text
    assert "售價53元/個" in copy_text
    assert copy_text.endswith("交貨2-3週")


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
    assert app.session_state["test_evidence"]["raw"] == VALID
    assert app.session_state["test_evidence"]["inputs"]["ex_rate"] == 4.8
    assert app.session_state["test_evidence"]["parsed"]["price"] == 9.3


def test_vendor_inline_carton_weight_renders_and_can_save():
    app = paste(VENDOR_INLINE_CARTON)
    values = {item.label: item.value for item in app.number_input}
    assert values["進價(RMB)"] == 24.6
    assert values["裝箱量"] == 30
    assert values["整箱毛重(kg)"] == 27.5
    assert not app.error
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    save_button(app).click().run()
    rows = app.session_state["test_saved_rows"]
    assert "(27.5/30)*1000*1.05" in rows[1][7]


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


def test_new_product_never_guesses_supplier_or_category():
    app = paste(VALID, select_source=False)
    assert next(s for s in app.selectbox if s.label == "📂 分頁").value == ""
    assert next(s for s in app.selectbox if s.label == "🏷️ 廠商").value == ""
    app.checkbox[-1].check().run()
    assert save_button(app).disabled
    next(s for s in app.selectbox if s.label == "📂 分頁").set_value("S生活用品").run()
    app.checkbox[-1].check().run()
    assert save_button(app).disabled


def test_switch_supplier_format_clears_draft_and_confirmation():
    app = paste(VALID)
    next(s for s in app.selectbox if s.label == "📂 分頁").set_value("S生活用品").run()
    next(s for s in app.selectbox if s.label == "🏷️ 廠商").set_value("v多品村").run()
    next(n for n in app.number_input if n.label == "內陸運費(R/kg)").set_value(0.0).run()
    app.checkbox[-1].check().run()
    assert not save_button(app).disabled
    app.text_area[0].set_value(VENDOR_INLINE_CARTON).run()
    assert not app.exception
    assert next(s for s in app.selectbox if s.label == "📂 分頁").value == ""
    assert next(s for s in app.selectbox if s.label == "🏷️ 廠商").value == ""
    assert next(n for n in app.number_input if n.label == "內陸運費(R/kg)").value == 1.5
    assert next(n for n in app.number_input if n.label == "整箱毛重(kg)").value == 27.5
    assert not app.checkbox[-1].value
    assert save_button(app).disabled
    next(s for s in app.selectbox if s.label == "📂 分頁").set_value("S生活用品").run()
    next(s for s in app.selectbox if s.label == "🏷️ 廠商").set_value("v菲凡").run()
    app.checkbox[-1].check().run()
    save_button(app).click().run()
    rows = app.session_state["test_saved_rows"]
    assert rows[0][11] == "v菲凡"
    assert "*1.5" in rows[1][8]
    assert rows[2][8] == ""


def test_same_parsed_values_and_return_to_old_source_do_not_restore_manual_edits():
    app = paste(VALID)
    next(n for n in app.number_input if n.label == "進價(RMB)").set_value(10.0).run()
    next(t for t in app.text_input if t.label.startswith("產品尺寸")).set_value("99cm").run()
    next(t for t in app.text_area if t.label.startswith("額外備註")).set_value("上一款手動備註").run()
    for raw in (VALID.replace("A0081", "A0082"), VALID):
        app.text_area[0].set_value(raw).run()
        assert not app.exception
        assert next(n for n in app.number_input if n.label == "進價(RMB)").value == 9.3
        assert next(t for t in app.text_input if t.label.startswith("產品尺寸")).value == "10*8.5*2.5cm"
        assert "上一款" not in next(t for t in app.text_area if t.label.startswith("額外備註")).value
        assert next(s for s in app.selectbox if s.label == "🏷️ 廠商").value == ""


def test_changed_cloud_data_does_not_leave_a_copy_on_screen():
    app = AppTest.from_string(app_source(existing=True, ad_failure=True), default_timeout=15).run()
    next(c for c in app.checkbox if c.label == "載入雲表商品").check().run()
    next(s for s in app.selectbox if s.label.startswith("選擇商品（可搜尋")).set_value("1|no1").run()
    next(c for c in app.checkbox if c.label.startswith("我已逐欄對照本款完整原文")).check().run()
    next(b for b in app.button if b.label == "檢查並產生 LINE 文案").click().run()
    assert not app.exception
    assert any("雲表商品已變更" in error.value for error in app.error)
    assert not any("BGD-G-1" in code.value for code in app.code)


def test_program_checks_run_before_human_ad_review():
    app = AppTest.from_string(app_source(existing=True, ad_invalid=True), default_timeout=15).run()
    next(c for c in app.checkbox if c.label == "載入雲表商品").check().run()
    next(s for s in app.selectbox if s.label.startswith("選擇商品（可搜尋")).set_value("1|no1").run()
    assert not app.exception
    assert any("程式檢查未通過" in error.value for error in app.error)
    assert next(c for c in app.checkbox if c.label.startswith("我已逐欄對照本款完整原文")).disabled
    assert next(b for b in app.button if b.label == "檢查並產生 LINE 文案").disabled
    assert not any("BGD-G-1" in code.value for code in app.code)
