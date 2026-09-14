"""No credentials or Google writes: exercise actual app functions via AST."""
import ast
import datetime
import math
import re
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import zhconv

SOURCE = Path(__file__).parents[1] / "dolly_parser.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        node.decorator_list = []
        nodes.append(node)
    elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("UNIT_PAT", "EMOJI_PAT") for t in node.targets):
        nodes.append(node)
ns = dict(re=re, math=math, unicodedata=unicodedata, zhconv=zhconv, datetime=datetime)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)


def samples():
    # Locally preserved clipboard fixtures; no private text is sent to GitHub.
    path = SOURCE.parents[1] / "核對原文樣本-剩餘7款.md"
    if not path.exists():
        return []
    first_two = SOURCE.parents[1] / "核對原文樣本-前2款.md"
    if not first_two.exists():
        return []
    return re.findall(r"```text\n(.*?)\n```", path.read_text(encoding="utf-8") + first_two.read_text(encoding="utf-8"), re.S)


@pytest.mark.parametrize("raw,code,price,qty,kg,g,size", [
    ("新品收納包\n编号：A0081\n箱数：300pcs\n单价：9.3元\n尺寸：10*8.5*2.5cm\n重量：68g(单个)\n包装：12个/opp袋", "A0081", 9.3, 300, 0, 68, "10*8.5*2.5cm"),
    ("新品手電筒\nUSB充电\n型号：511\n箱数：240pcs\n单价：3.7元\n产品：9.3cm\n箱重：14kg", "511", 3.7, 240, 14, 0, "9.3cm"),
    ("新品掛件\n型号:X123\n每箱数量:300pcs\n单个价格:9.2元\n产品尺寸:3.5-4.3cm\n整箱重量:约18kg", "X123", 9.2, 300, 18, 0, "3.5-4.3cm"),
])
def test_formats(raw, code, price, qty, kg, g, size):
    c, p = ns["parse_text"](raw)
    assert (p[0]["code"], c["price"], c["qty"], c["weight"], c["unit_weight_g"], c["prod_size"]) == (code, price, qty, kg, g, size)
    assert not c["issues"]
    assert "編號" not in p[0]["name"]
    assert "USB" not in p[0]["name"]


@pytest.mark.parametrize(
    "price_line,qty_line,expected_price,expected_unit",
    [
        ("單套價格:19.8元", "每箱數量:20套", 19.8, "套"),
        ("單盒價格:9.9元", "每箱數量:30盒", 9.9, "盒"),
        ("單個價格:12元", "每箱數量:48個", 12, "個"),
        ("單件價格:7.5元", "每箱數量:60件", 7.5, "個"),
        ("單瓶價格:8元", "每箱數量:24瓶", 8, "瓶"),
        ("單罐價格:6.2元", "每箱數量:36罐", 6.2, "罐"),
        ("單包價格:5元", "每箱數量:40包", 5, "包"),
        ("單袋價格:4.6元", "每箱數量:50袋", 4.6, "袋"),
    ],
)
def test_unit_price_line_is_metadata_not_product_name(
    price_line, qty_line, expected_price, expected_unit
):
    raw = "\n".join([
        "史努比家族豎紋陶瓷碗4件套",
        "帶鐳射標（4個/1套）",
        "型號:FU-26-8051SN",
        qty_line,
        price_line,
        "整箱重量:18kg",
    ])
    common, products = ns["parse_text"](raw)

    assert products == [{
        "code": "FU-26-8051SN",
        "name": "史努比家族豎紋陶瓷碗4件套",
    }]
    assert (common["price"], common["price_unit"]) == (
        expected_price,
        expected_unit,
    )


def test_unit_price_line_is_not_name_even_without_product_code():
    common, products = ns["parse_text"](
        "史努比家族豎紋陶瓷碗4件套\n單套價格:19.8元"
    )

    assert products == [{"code": "", "name": "史努比家族豎紋陶瓷碗4件套"}]
    assert (common["price"], common["price_unit"]) == (19.8, "套")


def test_nine_original_clipboards():
    raws = samples()
    if not raws:
        pytest.skip("Private local clipboard fixtures are not published")
    expected = [("HM-MF26282",23,80,17,0), ("26039-42",63.5,60,18.5,0),
                ("AB0173",8.8,96,11.3,0), ("YSK03884",9.2,300,18,0),
                ("MC-Z0001",12,48,20.5,0), ("511",3.7,240,14,0), ("A0081",9.3,300,0,68),
                ("K-8141",9.9,30,4.7,0), ("7028",31.5,48,17,0)]
    assert len(raws) == len(expected)
    for raw, exp in zip(raws, expected):
        c, p = ns["parse_text"](raw)
        assert (p[0]["code"],c["price"],c["qty"],c["weight"],c["unit_weight_g"]) == exp
        assert not c["issues"], (exp[0], c["issues"])


def test_mc_z0001_wooden_rack_is_optional_and_uses_no_rack_inputs():
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
    c, products = ns["parse_text"](raw)
    assert products[0]["code"] == "MC-Z0001"
    assert products[0]["name"].startswith("三麗鷗浮雕系列茗芙4.5")
    assert (c["price"], c["qty"], c["weight"], c["unit_weight_g"]) == (12, 48, 20.5, 0)
    assert c["issues"] == []
    assert "木架大約:4-7kg(15元)" in c["extra_tags"]
    assert "可加木架；成本按無木架的進價及重量計算" in c["extra_tags"]
    assert ns["cost_blockers"](12, 48, 20.5, 0, 1.5, 8.5, 4.8, c["issues"], "個", "個") == []
    formulas = ns["build_cost_formulas"](2, 20.5, 0, 48, 1.5, 8.5, 4.8, "v多品村", final_price=12)
    assert "(20.5/48)*1000*1.05" in formulas["weight"]
    assert "4-7" not in "".join(formulas.values())
    assert "15" not in formulas["cost"]


def test_labeled_wooden_rack_price_and_weight_never_override_base_inputs():
    raw = """測試商品
型號:RACK-SAFE-1
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
木架另加單價:15元
木架另加整箱重量:7kg"""
    c, _ = ns["parse_text"](raw)
    assert (c["price"], c["qty"], c["weight"]) == (12, 48, 20.5)
    assert c["issues"] == []


def test_same_line_base_values_survive_optional_rack_clause():
    raw = """測試商品
型號:RACK-SAME-LINE
每箱數量:48pcs
單個價格:12元 整箱重量:20.5kg 木架另加:4-7kg(15元)"""
    c, _ = ns["parse_text"](raw)
    assert (c["price"], c["qty"], c["weight"]) == (12, 48, 20.5)
    assert c["issues"] == []


def test_negative_rack_is_not_labeled_optional():
    raw = """測試商品
型號:NO-RACK
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
不配木架"""
    c, _ = ns["parse_text"](raw)
    assert c["rack_state"] == "negative"
    assert c["issues"] == []
    assert "可加木架" not in c["extra_tags"]


def test_included_rack_without_explicit_no_rack_basis_blocks():
    raw = """測試商品
型號:INCLUDED-RACK
每箱數量:48pcs
單個價格:15元(已含木架)
整箱重量:27.5kg"""
    c, _ = ns["parse_text"](raw)
    assert c["rack_state"] == "included"
    assert c["price"] == 0
    assert c["weight"] == 0
    assert any("缺少明確無木架進價與重量" in issue for issue in c["issues"])
    assert "可加木架" not in c["extra_tags"]


def test_included_rack_can_only_use_explicit_no_rack_basis():
    raw = """測試商品
型號:INCLUDED-WITH-BASE
每箱數量:48pcs
單個價格:15元(已含木架)
整箱重量:27.5kg(已含木架)
無木架單價:12元
無木架整箱重量:20.5kg"""
    c, _ = ns["parse_text"](raw)
    assert c["rack_state"] == "included"
    assert (c["price"], c["qty"], c["weight"]) == (12, 48, 20.5)
    assert c["issues"] == []
    assert "可加木架" not in c["extra_tags"]


def test_rack_only_pending_does_not_block_base_cost():
    raw = """測試商品
型號:RACK-PENDING
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
可加木架，木架費待定"""
    c, _ = ns["parse_text"](raw)
    assert c["rack_state"] == "optional"
    assert (c["price"], c["qty"], c["weight"]) == (12, 48, 20.5)
    assert c["issues"] == []


def test_bare_pending_rack_blocks_until_declared_optional():
    raw = """測試商品
型號:RACK-PENDING-BARE
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
木架費待確認"""
    c, _ = ns["parse_text"](raw)
    assert c["rack_state"] == "pending"
    assert any("木架條件待確認" in issue for issue in c["issues"])


@pytest.mark.parametrize("fee_line", [
    "貼標費:2元",
    "配件費另收3元",
    "稅費加收5元",
    "物流費另計4元",
    "紙箱費:1.5元",
    "保險費:2元",
    "報關:3元",
    "倉儲費:4元",
])
def test_non_rack_fees_remain_noted_and_blocked(fee_line):
    raw = """測試商品
型號:FEE-1
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
""" + fee_line
    c, _ = ns["parse_text"](raw)
    assert any("非木架額外費用" in issue for issue in c["issues"])
    assert fee_line.replace("个", "個") in c["extra_tags"]


@pytest.mark.parametrize("extra_line", ["木架另加15元", "紙箱費:2元", "保險費:3元", "報關費:4元"])
def test_rack_and_fee_lines_never_enter_product_name(extra_line):
    raw = """正確商品名稱
{extra_line}
型號:NAME-SAFE
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg""".format(extra_line=extra_line)
    _, products = ns["parse_text"](raw)
    assert products[0]["name"] == "正確商品名稱"


def test_optional_rack_does_not_hide_other_fee_on_same_line():
    c, _ = ns["parse_text"]("""測試商品
型號:RACK-FEE
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
木架另加15元，貼標費另收2元""")
    assert any("非木架額外費用" in issue for issue in c["issues"])


def test_product_name_containing_wood_frame_is_not_a_rack_policy():
    c, _ = ns["parse_text"]("""木框掛畫
型號:WOOD-ART
每箱數量:24pcs
單個價格:10元
整箱重量:12kg""")
    assert c["rack_state"] == "none"
    assert c["issues"] == []


def test_optional_rack_note_integrity_requires_source_and_policy():
    raw = """測試商品
型號:RACK-NOTE
每箱數量:48pcs
單個價格:12元
整箱重量:20.5kg
木架另加15元"""
    c, _ = ns["parse_text"](raw)
    assert ns["wooden_rack_note_integrity_issues"](c, c["extra_tags"]) == []
    assert ns["wooden_rack_note_integrity_issues"](c, "包裝:紙盒")


def test_units_and_metadata():
    c, p = ns["parse_text"]("新品#正版授权\n測試密實袋\n带镭射标（40个/盒）\n型号:TEST-BOX\n每箱数量:30盒\n单盒价格:9.9元\n产品尺寸:12.5*17cm\n包装尺寸:19*6.5*4.5cm\n外箱尺寸:47*21*21.5cm\n整箱重量:4.7kg")
    assert (c["price"], c["qty"], c["weight"], c["price_unit"], c["qty_unit"]) == (9.9,30,4.7,"盒","盒")
    assert "40個/盒" in c["extra_tags"]
    assert "30盒/箱" in ns["build_carton_note_row"](30,"v多品村","盒")[1]
    c, p = ns["parse_text"]("新品#正版授权\n測試保溫杯530ml\n带镭射标/4个颜色\n材质:内316外304\n型号:TEST-CUP\n每箱数量:48pcs\n单个价格:31.5元\n产品尺寸:7.4*7.4*22.8cm\n彩盒尺寸:7.6*7.6*23.8cm\n外箱尺寸:67*38.5*42.5\n整箱重量:17kg")
    assert (c["price"], c["qty"], c["weight"],p[0]["code"]) == (31.5,48,17,"TEST-CUP")
    assert "內316外304" in c["extra_tags"] and "4個顏色" in c["extra_tags"]


def test_vendor_inline_carton_weight_after_explicit_carton_quantity():
    raw = """FF806274，不带电 线控起重机是[Fireworks]24.6元，一箱30只，27.5KG
彩盒尺寸46.5*6.3*40.2CM
外箱规格98.5*48*83.5CM"""
    c, p = ns["parse_text"](raw)
    assert (p[0]["code"], c["price"], c["qty"], c["qty_unit"]) == (
        "FF806274", 24.6, 30, "個"
    )
    assert (c["weight"], c["color_box_size"], c["outer_box_size"]) == (
        27.5, "46.5*6.3*40.2CM", "98.5*48*83.5CM"
    )
    assert c["issues"] == []


@pytest.mark.parametrize("line,kg,g", [("毛重:500克",0.5,0), ("單重:0.068kg",0,68), ("重量:68g(單個)",0,68), ("15kg",0,0), ("木架:4-7kg(15元)",0,0), ("毛淨重:12/10kg",12,0)])
def test_weight_scope(line,kg,g):
    c, _ = ns["parse_text"](line)
    assert (c["weight"],c["unit_weight_g"]) == (kg,g)


def test_no_guessing_qty_or_batch_parameters():
    c, _ = ns["parse_text"]("包裝:12個/opp袋")
    assert c["qty"] == 0
    c, _ = ns["parse_text"]("型號:A001\n單價:3元\n箱數:30個\n型號:B002\n單價:5元\n箱數:60個")
    assert c["issues"]


@pytest.mark.parametrize("text", ["單價:9-12元", "單價:1,200元", "箱數:12.5個", "箱數:30-50個", "單價:10元起", "單價:約10元", "單價:10元\n款式待確認"])
def test_ambiguous_numbers_are_not_silently_used(text):
    assert ns["parse_text"](text)[0]["issues"]


def test_freight_is_not_purchase_price():
    c, _ = ns["parse_text"]("運費:15元")
    assert c["price"] == 0
    assert c["issues"]


@pytest.mark.parametrize("args", [(2,0,68,300,-1,8.5,4.8), (2,0,68,300,1,8.5,0), (2,0,68,0,1,8.5,4.8), (2,0,68,float("nan"),1,8.5,4.8)])
def test_formula_helper_fails_closed_independently(args):
    assert set(ns["build_cost_formulas"](*args).values()) == {""}


@pytest.mark.parametrize("changes", [dict(price=0),dict(qty=0),dict(carton_kg=0),dict(price=float("nan")),dict(ex=0),dict(unit_g=10),dict(issues=["木架未確認"]),dict(price_unit="盒"),dict(qty_unit="")])
def test_blockers(changes):
    args=dict(price=9.3,qty=300,carton_kg=20.4,unit_g=0,dom=1.5,intl=8.5,ex=4.8,qty_unit="個")
    args.update(changes)
    assert ns["cost_blockers"](**args)


def test_valid_cost_and_blank_guards():
    args=(2,0,68,300,1.5,8.5,4.8,"v多品村")
    for kw in (dict(final_price=0),dict(blocked=True)):
        assert set(ns["build_cost_formulas"](*args,**kw).values()) == {""}
    formulas=ns["build_cost_formulas"](*args,final_price=9.3)
    assert all('NOT(ISNUMBER(G2))' in v and 'IFERROR' in v for v in formulas.values())
    assert '68*1.05' in formulas["weight"]
    assert ns["cost_blockers"](9.3,300,0,68,1.5,8.5,4.8,qty_unit="個") == []


def test_same_code_different_product_is_conflict():
    rows=[["no1","積木"],[],[],[],["","貨號 A0081"]]
    msg=ns["duplicate_messages"]([dict(code="a0081",name="收納包")],{"W玩具":rows})
    assert "同貨號不同品名衝突" in msg[0]


def test_edited_supplemental_notes_are_rescanned_for_uncertainty():
    assert ns["supplemental_uncertainty_issues"]("木架另加 15 元") == []
    assert ns["supplemental_uncertainty_issues"]("木架另加 15 元，打包費另加 2 元")
    assert ns["supplemental_uncertainty_issues"]("運費待確認")
    assert ns["supplemental_uncertainty_issues"]("包裝費另加 2 元")
    assert ns["supplemental_uncertainty_issues"]("材質：ABS\n4個顏色") == []


def test_append_user_entered_text_is_protected_from_formula_execution():
    rows = block()
    rows[0][1] = "=1+1"
    rows[4][1] = "+SUM(A1:A2)"
    protected = ns["protect_user_entered_rows"](rows)
    assert protected[0][1] == "'=1+1"
    assert protected[4][1] == "'+SUM(A1:A2)"
    rows[1][2] = "=SAFE_FORMULA()"
    assert ns["protect_user_entered_rows"](rows)[1][2] == "=SAFE_FORMULA()"


def mock_cloud(monkeypatch, fresh=None):
    sheet=Mock(title="G正版", row_count=100)
    sheet.get_all_values.return_value=[] if fresh is None else fresh
    book=Mock()
    book.worksheet.return_value=sheet
    book.worksheets.return_value=[sheet]
    monkeypatch.setitem(ns,"st",Mock())
    monkeypatch.setitem(ns,"get_credentials",Mock())
    monkeypatch.setitem(ns,"gspread",SimpleNamespace(authorize=Mock(), exceptions=SimpleNamespace(WorksheetNotFound=KeyError)))
    monkeypatch.setitem(ns,"open_spreadsheet",Mock(return_value=book))
    monkeypatch.setitem(ns,"get_all_sheets_data",Mock())
    return sheet,book


def block():
    return [["no1","測試商品"]+[""]*10,["2026/9/13"]+[""]*11,[""]*12,[""]*12,["","貨號 TEST001"]+[""]*10,[""]*12]


def test_stale_snapshot_no_write(monkeypatch):
    sheet,_=mock_cloud(monkeypatch, [["人工新資料"]])
    assert not ns["save_bulk_to_worksheet"]("G正版",block(),1,expected_rows=[])
    sheet.update.assert_not_called()


def test_missing_sheet_no_creation(monkeypatch):
    sheet,book=mock_cloud(monkeypatch)
    book.worksheet.side_effect=KeyError()
    assert not ns["save_bulk_to_worksheet"]("正版",block(),1,expected_rows=[])
    book.add_worksheet.assert_not_called()


def test_readback_and_retry_protection(monkeypatch):
    sheet,_=mock_cloud(monkeypatch)
    rows=block()
    sheet.get.side_effect=[rows, [[r[0]] for r in rows]]
    assert ns["save_bulk_to_worksheet"]("G正版",rows,1,expected_rows=[])
    sheet.get_all_values.return_value=rows
    assert not ns["save_bulk_to_worksheet"]("G正版",rows,8,expected_rows=rows)
    assert sheet.update.call_count == 1


def test_uncertain_write_not_success(monkeypatch):
    sheet,_=mock_cloud(monkeypatch)
    sheet.get.side_effect=RuntimeError("timeout")
    assert not ns["save_bulk_to_worksheet"]("G正版",block(),1,expected_rows=[])
    assert "可能已寫入" in ns["st"].error.call_args[0][0]
