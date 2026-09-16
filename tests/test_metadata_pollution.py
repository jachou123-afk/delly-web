"""Regression coverage for metadata that must not pollute product names.

The parser is loaded from the real Streamlit source through AST so these tests
remain read-only with respect to Google Sheets and do not require credentials.
"""

import ast
import datetime
import math
import re
import unicodedata
from pathlib import Path

import pytest
import zhconv

from license_markers import (
    has_affirmative_license_marker,
    is_standalone_license_marker,
    strip_affirmative_license_markers,
)
from line_ad_copy import _ad_detail_lines


SOURCE = Path(__file__).parents[1] / "dolly_parser.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODES = []
for node in TREE.body:
    if isinstance(node, ast.FunctionDef):
        node.decorator_list = []
        NODES.append(node)
    elif isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in ("UNIT_PAT", "EMOJI_PAT")
        for target in node.targets
    ):
        NODES.append(node)

NS = {
    "re": re,
    "math": math,
    "unicodedata": unicodedata,
    "zhconv": zhconv,
    "datetime": datetime,
    "has_affirmative_license_marker": has_affirmative_license_marker,
    "is_standalone_license_marker": is_standalone_license_marker,
    "strip_affirmative_license_markers": strip_affirmative_license_markers,
}
exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), "exec"), NS)


U4 = """新品#正版授权
Hellokitty系列二合一抱枕毛毯
（带镭射标）
货号:U4
每箱数量:6pcs
单个价格:70元
抱枕尺寸:35*38cm
毛毯尺寸:100*140cm
外箱尺寸:50*38*30cm
整箱重量:6kg
包装:吊牌+独立opp袋
(3箱起订/3箱起订)"""

RAU0669 = """新品#正版授权
蜡笔小新系列头戴蓝牙耳机
（带镭射标）
型号:RAU0669
每箱数量:10pcs
单个价格:50元
包装尺寸:23*23*6cm
外箱尺寸:47.5*32.5*27.5cm
整鞋重量:约6kg
(3箱起订/3箱起订)"""

CODE_480 = """新品#正版授权
宝可梦款惬意甜点串珠挂件
带镭射标/2个图案
编号:480-2012-1
每箱数量:72pcs（平均混）
单个价格:14.8元
产品长度:17.5cm
卡纸尺寸:21*6.8cm
外箱尺寸:40*30*30cm
整箱重量:约5.8kg
包装:卡纸+opp袋"""


def parse(raw):
    return NS["parse_text"](raw)


def blockers(common):
    return NS["cost_blockers"](
        common["price"],
        common["qty"],
        common["weight"],
        common["unit_weight_g"],
        0,
        0,
        4.8,
        common["issues"],
        common["price_unit"],
        common["qty_unit"],
    )


def test_u4_named_dimensions_and_moq_stay_in_notes_not_name():
    common, products = parse(U4)

    assert products == [{
        "code": "U4",
        "name": "Hellokitty系列二合一抱枕毛毯",
    }]
    assert (
        common["price"],
        common["qty"],
        common["weight"],
        common["prod_size"],
        common["color_box_size"],
        common["outer_box_size"],
    ) == (70, 6, 6, "", "", "50*38*30cm")
    assert common["extra_tags"].splitlines() == [
        "正版授權",
        "(帶鐳射標)",
        "抱枕尺寸:35*38cm",
        "毛毯尺寸:100*140cm",
        "包裝:吊牌+獨立opp袋",
        "(3箱起訂/3箱起訂)",
    ]
    assert blockers(common) == []


def test_unknown_weight_label_is_preserved_but_never_guessed():
    common, products = parse(RAU0669)

    assert products == [{
        "code": "RAU0669",
        "name": "蠟筆小新系列頭戴藍牙耳機",
    }]
    assert (common["price"], common["qty"], common["weight"]) == (50, 10, 0)
    assert common["unit_weight_g"] == 0
    assert common["color_box_size"] == "23*23*6cm"
    assert common["outer_box_size"] == "47.5*32.5*27.5cm"
    assert common["extra_tags"].splitlines() == [
        "正版授權",
        "(帶鐳射標)",
        "包裝尺寸:23*23*6cm",
        "整鞋重量:約6kg",
        "(3箱起訂/3箱起訂)",
    ]
    assert any("未識別重量欄位「整鞋重量」" in issue for issue in common["issues"])
    assert "缺少有效重量來源" in blockers(common)


def test_unknown_weight_label_blocks_even_with_another_valid_weight():
    common, products = parse(
        "測試商品\n型號:X1\n每箱數量:100pcs\n單個價格:5元\n"
        "單個重量:60g\n整鞋重量:約6kg"
    )

    assert products == [{"code": "X1", "name": "測試商品"}]
    assert (common["unit_weight_g"], common["weight"]) == (60, 0)
    assert any("未識別重量欄位「整鞋重量」" in issue for issue in common["issues"])
    assert any("未識別重量欄位「整鞋重量」" in reason for reason in blockers(common))


def test_bare_weight_with_unit_has_unknown_scope_and_is_not_guessed():
    common, products = parse(
        "万圣节按键灯蒸笼包钥匙扣\n展示盒24个\n价格：2.4\n"
        "装箱数量：672个\n箱规：55*35*50\n重量19.2KG"
    )

    assert products == [{"code": "", "name": "萬聖節按鍵燈蒸籠包鑰匙扣"}]
    assert (common["price"], common["qty"], common["qty_unit"]) == (2.4, 672, "個")
    assert (common["weight"], common["unit_weight_g"]) == (0, 0)
    assert "展示盒24個" in common["extra_tags"].splitlines()
    assert "箱規:55*35*50" in common["extra_tags"].splitlines()
    assert "重量19.2KG" in common["extra_tags"].splitlines()
    assert any("箱規" in issue and "缺少單位" in issue for issue in common["issues"])
    assert any("未標明單個或整箱" in issue for issue in common["issues"])
    assert any("未標明單個或整箱" in reason for reason in blockers(common))


def test_bare_weight_with_explicit_unit_scope_remains_valid_unit_weight():
    common, products = parse(
        "測試商品\n型號:W120\n每箱數量:10pcs\n單個價格:5元\n"
        "重量：120g(单个)"
    )

    assert products == [{"code": "W120", "name": "測試商品"}]
    assert (common["weight"], common["unit_weight_g"]) == (0, 120)
    assert not common["issues"]
    assert blockers(common) == []


@pytest.mark.parametrize(
    "weight_line",
    [
        "整鞋重量:約6kg（含包裝）",
        "整鞋重量:約6kg。",
        "整鞋重量:大約6kg",
        "整鞋重量:6斤",
        "總重量:6斤",
        "總重:6kg",
        "整鞋重量:大概6kg",
        "整鞋重量:約為6kg",
        "整鞋重量:不詳",
        "G.W.:6kg",
    ],
)
def test_loose_weight_like_fields_block_without_overriding_valid_weight(weight_line):
    common, products = parse(
        "測試商品\n型號:LOOSE-W\n每箱數量:100pcs\n單個價格:5元\n"
        f"單個重量:60g\n{weight_line}"
    )

    normalized_line = zhconv.convert(unicodedata.normalize("NFKC", weight_line), "zh-tw")
    assert products == [{"code": "LOOSE-W", "name": "測試商品"}]
    assert (common["unit_weight_g"], common["weight"]) == (60, 0)
    assert normalized_line in common["extra_tags"].splitlines()
    assert any("未識別重量欄位" in issue for issue in common["issues"])
    assert any("未識別重量欄位" in reason for reason in blockers(common))
    assert normalized_line not in _ad_detail_lines(normalized_line)


@pytest.mark.parametrize(
    "weight_line,expected_carton,expected_unit",
    [
        ("整箱重量:6kg", 6, 0),
        ("整箱毛重:6kg", 6, 0),
        ("箱重:6kg", 6, 0),
        ("毛重:6kg", 6, 0),
        ("單個重量:60g", 0, 60),
        ("每件重量:60g", 0, 60),
    ],
)
def test_supported_weight_labels_do_not_raise_unknown_label_issue(
    weight_line, expected_carton, expected_unit
):
    common, _ = parse(
        "測試商品\n型號:X2\n每箱數量:100pcs\n單個價格:5元\n"
        + weight_line
    )

    assert (common["weight"], common["unit_weight_g"]) == (
        expected_carton,
        expected_unit,
    )
    assert not any("未識別重量欄位" in issue for issue in common["issues"])


def test_named_length_dimensions_and_mix_condition_are_preserved():
    common, products = parse(CODE_480)

    assert products == [{
        "code": "480-2012-1",
        "name": "寶可夢款愜意甜點串珠掛件",
    }]
    assert (
        common["price"],
        common["qty"],
        common["weight"],
        common["prod_size"],
        common["color_box_size"],
        common["outer_box_size"],
    ) == (14.8, 72, 5.8, "", "", "40*30*30cm")
    assert common["extra_tags"].splitlines() == [
        "正版授權",
        "帶鐳射標/2個圖案",
        "平均混",
        "產品長度:17.5cm",
        "卡紙尺寸:21*6.8cm",
        "整箱重量:約5.8kg",
        "包裝:卡紙+opp袋",
    ]
    assert blockers(common) == []


@pytest.mark.parametrize("condition", ["2箱起訂", "2箱起批"])
def test_order_condition_is_preserved_without_polluting_name(condition):
    common, products = parse(
        f"測試商品\n{condition}\n型號:MOQ1\n每箱數量:10pcs\n"
        "單個價格:5元\n整箱重量:1kg"
    )

    assert products == [{"code": "MOQ1", "name": "測試商品"}]
    assert condition in common["extra_tags"].splitlines()
    assert blockers(common) == []


@pytest.mark.parametrize(
    "name",
    [
        "大尺寸收納袋",
        "重量級拳擊手套",
        "超長度充電線",
        "GW聯名收納包",
        "總重風格吊飾",
    ],
)
def test_metadata_detection_does_not_remove_normal_product_names(name):
    common, products = parse(
        f"{name}\n型號:N1\n每箱數量:10pcs\n單個價格:5元\n整箱重量:1kg"
    )

    assert products == [{"code": "N1", "name": name}]
    assert not common["issues"]


def test_line_copy_hides_unknown_weight_fields_without_hiding_weight_words():
    lines = _ad_detail_lines(
        "包裝尺寸:23*23*6cm\n"
        "整鞋重量:約6kg\n"
        "毛毯重量:1kg\n"
        "顏色:黑色 整鞋重量:約6kg\n"
        "重量級設計\n"
        "(3箱起訂/3箱起訂)"
    )

    assert lines == [
        "包裝尺寸:23*23*6cm",
        "顏色:黑色",
        "重量級設計",
        "(3箱起訂/3箱起訂)",
    ]


def test_decimal_comma_in_dimension_is_not_guessed_and_blocks_cost():
    common, products = parse(
        "瘋狂動物城系列文具套裝\n型號:A90215\n每箱數量:12pcs\n"
        "單個價格:30元\n包裝尺寸:38.8*14*16,5cm\n整箱重量:6kg"
    )

    assert products == [{"code": "A90215", "name": "瘋狂動物城系列文具套裝"}]
    assert common["color_box_size"] == ""
    assert "包裝尺寸:38.8*14*16,5cm" in common["extra_tags"].splitlines()
    assert any("小數逗號" in issue for issue in common["issues"])
    assert any("小數逗號" in reason for reason in blockers(common))


def test_dimension_without_unit_is_not_guessed_and_blocks_cost():
    common, products = parse(
        "三麗鷗家族系列輕享雙飲保溫杯530ml\n型號:7028\n"
        "每箱數量:48pcs\n單個價格:31.5元\n產品尺寸:7.4*7.4*22.8cm\n"
        "彩盒尺寸:7.6*7.6*23.8cm\n外箱尺寸:67*38.5*42.5\n整箱重量:17kg"
    )

    assert products == [{
        "code": "7028",
        "name": "三麗鷗家族系列輕享雙飲保溫杯530ml",
    }]
    assert common["outer_box_size"] == ""
    assert "外箱尺寸:67*38.5*42.5" in common["extra_tags"].splitlines()
    assert any("外箱尺寸" in issue and "缺少單位" in issue for issue in common["issues"])
    assert any("缺少單位" in reason for reason in blockers(common))


def test_repeated_packaging_label_is_preserved_and_blocks_for_review():
    common, products = parse(
        "三麗鷗凱蒂貓鹿皮拼毛毛手機斜挎包\n型號:JH-26155-6\n"
        "每箱數量:20pcs\n單個價格:30元\n整箱重量:8kg\n"
        "包裝:包裝:吊牌+獨立opp袋"
    )

    assert products == [{
        "code": "JH-26155-6",
        "name": "三麗鷗凱蒂貓鹿皮拼毛毛手機斜挎包",
    }]
    assert "包裝:包裝:吊牌+獨立opp袋" in common["extra_tags"].splitlines()
    assert any("包裝" in issue and "重複" in issue for issue in common["issues"])
    assert any("包裝" in reason and "重複" in reason for reason in blockers(common))


def test_valid_dimension_range_is_preserved_without_blocking():
    common, products = parse(
        "三麗鷗家族街頭穿搭系列掛件\n型號:YSK03884\n"
        "每箱數量:300pcs\n單個價格:9.2元\n"
        "產品尺寸:3.5-4.3cm\n整箱重量:約18kg\n包裝:吊牌+獨立opp袋"
    )

    assert products == [{
        "code": "YSK03884",
        "name": "三麗鷗家族街頭穿搭系列掛件",
    }]
    assert common["prod_size"] == "3.5-4.3cm"
    assert blockers(common) == []


def test_display_box_dimension_and_emoji_carton_qualifier_stay_together():
    common, products = parse(
        "TEST-1150 測試按鍵（4色混裝） 🍫⌨️\n"
        "💰單價：5.2元\n📦裝箱量：576個/箱（24盒×24個）\n"
        "產品尺寸：4×2×7cm\n"
        "彩盒尺寸：8×15×21CM（展示盒）\n"
        "外箱規格：43×32×49CM\n毛重：20KG"
    )

    assert products[0]["code"] == "TEST-1150"
    assert (common["price"], common["qty"], common["qty_unit"], common["weight"]) == (5.2, 576, "個", 20)
    assert common["color_box_size"] == "8×15×21CM（展示盒）"
    assert "24盒×24個" in common["extra_tags"].splitlines()
    assert blockers(common) == []
    assert _ad_detail_lines(f"彩盒尺寸 {common['color_box_size']}") == [
        "彩盒尺寸 8×15×21CM（展示盒）",
    ]


@pytest.mark.parametrize("field", [
    "彩盒尺寸：8×15×21CM（單個小盒）",
    "產品尺寸：8×15×21CM（展示盒）",
    "彩盒尺寸：8×15×21（展示盒）",
])
def test_unrecognized_or_unitless_dimension_qualifier_still_blocks(field):
    common, _ = parse(
        f"測試商品\n型號:DIM2\n每箱數量:24pcs\n單個價格:5元\n{field}\n整箱重量:1kg"
    )

    assert common["issues"]
    assert blockers(common)


def test_carton_and_fulfilment_qualifiers_are_preserved_not_named():
    common, products = parse(
        "瘋狂動物城朱迪收納架\n型號:5106-4\n每箱數量:6pcs（捆）\n"
        "單個價格:100元\n整箱重量:12kg\n(6個/套1個編織袋出貨)"
    )

    assert products == [{"code": "5106-4", "name": "瘋狂動物城朱迪收納架"}]
    assert (common["qty"], common["qty_unit"]) == (6, "個")
    assert "捆" in common["extra_tags"].splitlines()
    assert "(6個/套1個編織袋出貨)" in common["extra_tags"].splitlines()
    assert blockers(common) == []


def test_no_code_product_keeps_named_dimensions_but_blocks_unitless_fields():
    common, products = parse(
        "pu材质慢回弹减压捏捏乐\n价格：6.6元\n装箱数：200个/混装\n"
        "箱规：52*42*62.5\n重量：17.3\n牛油果：11.8*7.5*9\n"
        "甜甜圈：11*11\n汉堡：9*10.3\n热狗：13.2*6.7"
    )

    assert products == [{"code": "", "name": "pu材質慢回彈減壓捏捏樂"}]
    assert (common["price"], common["qty"], common["qty_unit"]) == (6.6, 200, "個")
    assert "pu材質慢回彈減壓捏捏樂" not in common["extra_tags"].splitlines()
    assert "混裝" in common["extra_tags"].splitlines()
    for line in (
        "牛油果:11.8*7.5*9",
        "甜甜圈:11*11",
        "漢堡:9*10.3",
        "熱狗:13.2*6.7",
    ):
        assert line in common["extra_tags"].splitlines()
    assert common["weight"] == 0
    assert common["outer_box_size"] == ""
    assert "箱規:52*42*62.5" in common["extra_tags"].splitlines()
    assert "重量:17.3" in common["extra_tags"].splitlines()
    assert any("箱規" in issue and "缺少單位" in issue for issue in common["issues"])
    assert any("重量" in issue and "缺少單位" in issue for issue in common["issues"])
    assert blockers(common)


@pytest.mark.parametrize(
    "feature",
    [
        "打開:105*68.5cm",
        "白盒:17.2*4.7*4.7cm",
    ],
)
def test_labeled_customer_dimension_is_preserved_without_name_pollution(feature):
    common, products = parse(
        f"測試商品\n型號:DIM1\n每箱數量:10pcs\n單個價格:5元\n"
        f"{feature}\n整箱重量:1kg"
    )

    assert products == [{"code": "DIM1", "name": "測試商品"}]
    assert feature in common["extra_tags"].splitlines()
    assert blockers(common) == []


def test_white_box_is_packaging_size_and_suppresses_product_size_in_line_copy():
    common, products = parse(
        "太陽能露營伸縮燈\n型號:CL-701\n每箱數量:24pcs\n單個價格:10元\n"
        "產品尺寸:12*4cm\n白盒:17.2*4.7*4.7cm\n整箱重量:6kg"
    )

    assert products == [{"code": "CL-701", "name": "太陽能露營伸縮燈"}]
    assert common["prod_size"] == "12*4cm"
    assert common["color_box_size"] == "17.2*4.7*4.7cm"
    assert "白盒:17.2*4.7*4.7cm" in common["extra_tags"].splitlines()

    detail_lines = _ad_detail_lines(
        f"尺寸 {common['prod_size']}\n"
        f"彩盒尺寸 {common['color_box_size']}\n"
        + common["extra_tags"]
    )
    assert detail_lines == ["白盒:17.2*4.7*4.7cm"]


def test_line_copy_hides_unitless_outer_and_weight_fields():
    assert _ad_detail_lines(
        "箱規:60*40*50\n重量:17.3\n打開:105*68.5cm\n白盒:17.2*4.7*4.7cm"
    ) == [
        "打開:105*68.5cm",
        "白盒:17.2*4.7*4.7cm",
    ]
