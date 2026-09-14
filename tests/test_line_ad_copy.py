import copy
from decimal import Decimal, ROUND_HALF_UP

import pytest

from line_ad_copy import (
    build_bgd_code,
    build_line_ad_copy,
    build_line_ad_copy_from_sheet_block,
    ceil_ad_price,
)


def sheet_block(no, name, info, quote_10, carton, weight=""):
    # Complete, internally consistent displayed costing cells. These are
    # synthetic formatter fixtures, not additional audited supplier products.
    cost = (Decimal(str(quote_10)) * Decimal("0.9")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return [
        [no, name, "10%報價", "13%報價", "15%報價", "20%報價", "", "", "", "", "", "v多品村"],
        ["2026/9/12", info, quote_10, "", "", "", "9.3", "71.4", "0", "0.61", str(cost)],
        ["", carton],
        ["", weight],
        ["", "貨號 TEST"],
        [""],
    ]


def test_a0081_exact_line_copy_omits_weight():
    block = sheet_block(
        "no221",
        "新品蒙奇奇系列收納包",
        "\n".join(
            [
                "計價單位：個",
                "尺寸 10*8.5*2.5cm",
                "6個圖案混",
                "包裝:12個/opp袋",
            ]
        ),
        "52.9",
        "裝箱 300個/箱",
        "單個重量 68g",
    )

    assert build_line_ad_copy_from_sheet_block("S生活用品", block) == "\n".join(
        [
            "蒙奇奇系列收納包",
            "BGD-S-221",
            "尺寸 10*8.5*2.5cm",
            "6個圖案混",
            "包裝:12個/opp袋",
            "裝箱 300個/箱",
            "售價53元/個",
            "交貨2-3週",
        ]
    )


def test_mc_z0001_omits_outer_box_weight_and_all_wooden_rack_notes():
    block = sheet_block(
        "no1155",
        "三麗鷗浮雕系列茗芙4.5英寸飯碗",
        "\n".join(
            [
                "計價單位：個",
                "尺寸 12*6.8cm",
                "外箱尺寸 50.5*34*50cm",
                "新品#正版授權",
                "帶鐳射標/2個顏色",
                "整箱重量:約20.5kg",
                "包裝:牛皮紙盒",
                "木架大約:4-7kg(15元)",
                "可加木架；成本按無木架的進價及重量計算",
            ]
        ),
        84.3,
        "裝箱 48個/箱",
        "整箱毛重 20.5KG",
    )

    result = build_line_ad_copy_from_sheet_block("G正版", block)

    assert result.splitlines()[:3] == [
        "正版授權",
        "三麗鷗浮雕系列茗芙4.5英寸飯碗",
        "BGD-G-1155",
    ]
    assert "新品" not in result
    assert "BGD-G-1155" in result
    assert "尺寸 12*6.8cm" in result
    assert "帶鐳射標/2個顏色" in result
    assert "包裝:牛皮紙盒" in result
    assert "外箱" not in result
    assert "重量" not in result
    assert "木架" not in result
    assert "木框" not in result


def test_color_box_is_retained_while_outer_box_is_omitted():
    block = sheet_block(
        "no1137",
        "三麗鷗家族系列輕享雙飲保溫杯530ml",
        "\n".join(
            [
                "計價單位：個",
                "尺寸 7.4*7.4*22.8cm",
                "彩盒尺寸 7.6*7.6*23.8cm",
                "外箱尺寸 67*38.5*42.5",
                "材質:內316外304",
            ]
        ),
        184.9,
        "裝箱 48個/箱",
        "整箱毛重 17KG",
    )

    result = build_line_ad_copy_from_sheet_block("G正版", block)

    assert "彩盒尺寸 7.6*7.6*23.8cm" in result
    assert "尺寸 7.4*7.4*22.8cm" not in result
    assert "外箱尺寸" not in result
    assert "售價185元/個" in result


def test_identical_packaging_dimensions_are_shown_once_and_prefer_color_box():
    result = build_line_ad_copy(
        name="正版授權 新品測試商品",
        category_name="G正版",
        no_value="no1136",
        quote_10=60.2,
        unit="盒",
        details="\n".join(
            [
                "尺寸 12.5*17cm",
                "包裝尺寸:19 x 6.5 x 4.5 CM",
                "彩盒尺寸 19*6.5*4.5cm",
            ]
        ),
        carton_text="裝箱 30盒/箱",
    )

    lines = result.splitlines()
    assert lines[:3] == ["正版授權", "測試商品", "BGD-G-1136"]
    assert "尺寸 12.5*17cm" not in lines
    assert "彩盒尺寸 19*6.5*4.5cm" in lines
    assert not any(line.startswith("包裝尺寸") for line in lines)


def test_distinct_packaging_dimensions_are_both_retained():
    result = build_line_ad_copy(
        name="測試盲盒",
        category_name="G正版",
        no_value="no1147",
        quote_10=52.6,
        unit="個",
        details="彩盒尺寸 8*8*11.5cm\n端盒尺寸 32.4*16.4*11.8cm",
        carton_text="裝箱 96個/箱",
    )

    assert "彩盒尺寸 8*8*11.5cm" in result
    assert "端盒尺寸 32.4*16.4*11.8cm" in result


def test_packaging_method_does_not_suppress_product_size():
    result = build_line_ad_copy(
        name="新品蒙奇奇系列收納包",
        category_name="S生活用品",
        no_value="no221",
        quote_10=52.9,
        unit="個",
        details="尺寸 10*8.5*2.5cm\n包裝:12個/opp袋",
        carton_text="裝箱 300個/箱",
    )

    assert result.splitlines()[0] == "蒙奇奇系列收納包"
    assert "尺寸 10*8.5*2.5cm" in result
    assert "包裝:12個/opp袋" in result
    assert "正版授權" not in result


def test_category_name_alone_does_not_invent_license_label():
    result = build_line_ad_copy(
        name="測試商品",
        category_name="G正版",
        no_value="no1",
        quote_10=10,
        unit="個",
        details="尺寸 1*1cm",
        carton_text="裝箱 10個/箱",
    )

    assert result.splitlines()[0] == "測試商品"
    assert "正版授權" not in result


def test_simplified_license_marker_is_moved_to_traditional_first_line():
    result = build_line_ad_copy(
        name="新品測試商品",
        category_name="G正版",
        no_value="no2",
        quote_10=10,
        unit="個",
        details="新品#正版授权\n產品尺寸:1*1cm",
        carton_text="裝箱 10個/箱",
    )

    assert result.splitlines()[:3] == ["正版授權", "測試商品", "BGD-G-2"]
    assert "新品" not in result
    assert "正版授权" not in result


def test_rwshm_0004_plain_authorization_marker_is_moved_to_first_line():
    result = build_line_ad_copy(
        name="海綿寶寶網格隨身手機包盲盒",
        category_name="G正版",
        no_value="no1116",
        quote_10=153.4,
        unit="個",
        details="新品#授权\n帶鐳射標（4個/端盒）\n彩盒尺寸:15*5*24.5cm\n端盒尺寸:30.5*10.5*24.7cm",
        carton_text="裝箱 48個/箱",
    )

    lines = result.splitlines()
    assert lines[:3] == [
        "正版授權",
        "海綿寶寶網格隨身手機包盲盒",
        "BGD-G-1116",
    ]
    assert "新品#授权" not in lines
    assert lines.count("正版授權") == 1


def test_mn202450_source_marker_adds_license_without_leaking_marker_detail():
    result = build_line_ad_copy(
        name="MINISO貓福珊迪系列毛茸茸派對手辦盲盒",
        category_name="G正版",
        no_value="no1124",
        quote_10=106,
        unit="個",
        details="爆品#正版授权MINISO\n帶鐳射標（6個/端盒）\n彩盒尺寸:7*7*10cm\n端盒尺寸:21.5*14.5*10.5cm",
        carton_text="裝箱 108個/箱",
    )

    lines = result.splitlines()
    assert lines[:3] == [
        "正版授權",
        "MINISO貓福珊迪系列毛茸茸派對手辦盲盒",
        "BGD-G-1124",
    ]
    assert not any("爆品#正版" in line for line in lines)
    assert lines.count("正版授權") == 1


@pytest.mark.parametrize(
    "statement",
    [
        "未授權",
        "未經授權",
        "未经授权",
        "非正版授權",
        "無正版授權",
        "沒有正版授權",
        "不是正版授權",
        "並非正版授權",
        "未獲正版授權",
        "未取得正版授權",
        "未經正版授權",
        "未经正版授权",
        "未經官方正版授權",
        "未獲官方正版授權",
        "未得到正版授權",
        "未有正版授權",
        "尚未有正版授權",
        "不具正版授權",
        "不具有正版授權",
        "不含正版授權",
        "正版授權待確認",
        "是否正版授權",
        "需取得正版授權",
        "尚未獲得正版授權",
        "尚未得到正版授權",
        "未曾獲得正版授權",
        "未能取得正版授權",
        "無法取得正版授權",
        "無法確認正版授權",
        "不能取得正版授權",
        "不能確認正版授權",
        "缺乏正版授權",
        "缺少正版授權",
        "未提供正版授權",
        "尚未提供正版授權",
        "正版授權已失效",
        "已失效的正版授權",
        "正版授權被撤銷",
        "未確認正版授權",
        "非屬正版授權",
        "不屬於正版授權",
        "需正版授權",
        "本款為正版授權",
    ],
)
def test_negative_authorization_statement_does_not_add_license_label(statement):
    result = build_line_ad_copy(
        name="測試商品",
        category_name="G正版",
        no_value="no2",
        quote_10=10,
        unit="個",
        details=statement,
        carton_text="裝箱 10個/箱",
    )

    assert result.splitlines()[0] == "測試商品"
    assert result.splitlines().count("正版授權") == 0
    assert statement in result.splitlines()


def test_name_that_is_empty_after_removing_new_label_fails_closed():
    with pytest.raises(ValueError, match="商品名稱不可空白"):
        build_line_ad_copy(
            name="新品",
            category_name="S生活用品",
            no_value="no3",
            quote_10=10,
            unit="個",
            details="尺寸 1*1cm",
            carton_text="裝箱 10個/箱",
        )


@pytest.mark.parametrize(
    ("quote_10", "expected"),
    [(52, 52), ("52.0", 52), (52.1, 53), ("52.9", 53)],
)
def test_quote_10_always_rounds_up(quote_10, expected):
    assert ceil_ad_price(quote_10) == expected


@pytest.mark.parametrize("quote_10", ["", 0, -1, True, "待補", float("nan")])
def test_invalid_quote_10_fails_closed(quote_10):
    with pytest.raises(ValueError):
        ceil_ad_price(quote_10)


def test_bgd_identifier_has_two_hyphens_and_does_not_guess_categories():
    assert build_bgd_code("G正版", "no1136") == "BGD-G-1136"
    assert build_bgd_code("S生活用品", "NO221") == "BGD-S-221"
    with pytest.raises(ValueError):
        build_bgd_code("W玩具", "no1")
    with pytest.raises(ValueError):
        build_bgd_code("G正版", "no11A")


def test_k8141_uses_box_as_the_price_unit():
    result = build_line_ad_copy(
        name="三麗鷗庫洛米雙鏈密實袋(小號)",
        category_name="G正版",
        no_value="no1136",
        quote_10=60.2,
        unit="盒",
        details="計價單位：盒\n尺寸 12.5*17cm\n包裝尺寸:19*6.5*4.5cm",
        carton_text="裝箱 30盒/箱",
    )

    assert "BGD-G-1136" in result
    assert "售價61元/盒" in result
    assert "售價61元/個" not in result


def test_carton_weight_suffix_is_removed_without_losing_carton_quantity():
    result = build_line_ad_copy(
        name="新品蒙奇奇系列收納包",
        category_name="S生活用品",
        no_value="no221",
        quote_10=52.9,
        unit="個",
        details="尺寸 10*8.5*2.5cm",
        carton_text="裝箱 300個/箱 單個重量 68g",
    )
    assert "裝箱 300個/箱" in result
    assert "重量" not in result


def test_unit_mismatch_fails_closed():
    with pytest.raises(ValueError, match="不一致"):
        build_line_ad_copy(
            name="測試商品",
            category_name="G正版",
            no_value="no1",
            quote_10=10,
            unit="盒",
            details="尺寸 1*1cm",
            carton_text="裝箱 30個/箱",
        )


def test_ad_formatter_does_not_mutate_the_sheet_block():
    block = sheet_block(
        "no1155",
        "測試商品",
        "計價單位：個\n外箱尺寸 10*10*10cm\n木架另加15元",
        10,
        "裝箱 10個/箱",
        "整箱毛重 10KG",
    )
    original = copy.deepcopy(block)

    result = build_line_ad_copy_from_sheet_block("G正版", block)

    assert block == original
    assert "外箱" not in result and "木架" not in result and "重量" not in result


@pytest.mark.parametrize("raw,public,private", [
    ("正版授權，材質:內316外304", "材質:內316外304", "unused"),
    ("包裝:牛皮紙盒，木架另加15元", "包裝:牛皮紙盒", "木架"),
    ("重量68g，4個顏色", "4個顏色", "重量"),
    ("4個顏色，重量68g", "4個顏色", "重量"),
    ("尺寸10㎝ 單個重量68g", "尺寸10cm", "單個"),
    ("單個重量68g；材質:ABS；外箱尺寸20*20*20cm", "材質:ABS", "外箱"),
    ("包裝:盒裝，成本按無木架計算", "包裝:盒裝", "成本"),
    ("附加費用確認：供應商确认每個另加2元，確認完成\n包裝:紙盒", "包裝:紙盒", "確認"),
])
def test_mixed_line_fields_keep_public_details(raw, public, private):
    result = build_line_ad_copy(name="測試商品", category_name="G正版", no_value="no1", quote_10=10,
                                unit="個", details=raw, carton_text="裝箱 10個/箱")
    assert public in result
    assert private not in result
    if raw.startswith("正版授權"):
        assert result.splitlines()[0] == "正版授權"


@pytest.mark.parametrize("raw", ["1,2", "52,9", "1,000,00", "NaN", "Infinity", "=10", True])
def test_malformed_display_numbers_are_not_guessed(raw):
    with pytest.raises(ValueError):
        ceil_ad_price(raw)


def test_valid_thousands_grouping_is_accepted():
    assert ceil_ad_price("1,234.5") == 1235


def test_zero_carton_is_blocked():
    with pytest.raises(ValueError, match="大於零"):
        build_line_ad_copy(name="測試商品", category_name="G正版", no_value="no1", quote_10=10,
                           unit="個", details="", carton_text="裝箱 0個/箱")


@pytest.mark.parametrize("col", [6, 7, 8, 9, 10])
@pytest.mark.parametrize("bad", ["", "#ERROR!", -1, True])
def test_incomplete_costing_blocks_ad_even_when_quote_exists(col, bad):
    block = sheet_block("no1", "測試商品", "計價單位：個", 52.9, "裝箱 300個/箱")
    block[1][col] = bad
    with pytest.raises(ValueError):
        build_line_ad_copy_from_sheet_block("G正版", block)


def test_stale_or_overwritten_quote_blocks_ad():
    block = sheet_block("no1", "測試商品", "計價單位：個", 52.9, "裝箱 300個/箱")
    block[1][2] = "60"
    with pytest.raises(ValueError, match="不一致"):
        build_line_ad_copy_from_sheet_block("G正版", block)


def test_missing_vendor_blocks_ad():
    block = sheet_block("no1", "測試商品", "計價單位：個", 52.9, "裝箱 300個/箱")
    block[0][11] = ""
    with pytest.raises(ValueError, match="廠商"):
        build_line_ad_copy_from_sheet_block("G正版", block)
