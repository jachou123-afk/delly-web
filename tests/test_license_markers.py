import pytest

from license_markers import (
    has_affirmative_license_marker,
    is_standalone_license_marker,
    strip_affirmative_license_markers,
)


@pytest.mark.parametrize(
    "marker",
    [
        "授權",
        "授权",
        "#授權",
        "新品#授权",
        "正版授權",
        "新品 # 正版授权",
        "爆品#正版授權",
    ],
)
def test_affirmative_standalone_markers_are_recognized_and_removed(marker):
    assert is_standalone_license_marker(marker)
    assert has_affirmative_license_marker(marker)
    assert strip_affirmative_license_markers(marker) == ""


@pytest.mark.parametrize(
    "statement",
    [
        "非授權",
        "非正版授權",
        "無授權",
        "无正版授权",
        "沒有正版授權",
        "不是正版授權",
        "並非官方正版授權",
        "未獲正版授權",
        "未获得正版授权",
        "未取得正版授權",
        "未經正版授權",
        "未经正版授权",
        "未經官方正版授權",
        "未经官方正版授权",
        "未獲官方正版授權",
        "未得到正版授權",
        "未有正版授權",
        "尚未有正版授權",
        "不具正版授權",
        "不具有正版授權",
        "不含正版授權",
        "正版授權待確認",
        "正版授权待确认",
        "是否正版授權",
        "需取得正版授權",
        "需要先獲得正版授權",
        "尚未獲得正版授權",
        "尚未取得正版授權",
        "尚未得到正版授權",
        "正版授權？",
        "疑似正版授權",
        "可能是正版授權",
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
def test_negative_or_uncertain_clause_is_never_licensed_or_stripped(statement):
    assert not is_standalone_license_marker(statement)
    assert not has_affirmative_license_marker(statement)
    assert strip_affirmative_license_markers(statement) == statement


def test_clause_scope_allows_a_separate_explicit_affirmative_marker():
    text = "前款未經官方正版授權；#授權"

    assert has_affirmative_license_marker(text)
    assert strip_affirmative_license_markers(text) == "前款未經官方正版授權；"


def test_clause_scope_removes_separate_branded_affirmative_marker_only():
    text = "前款未經官方正版授權；正版授權"

    assert has_affirmative_license_marker(text)
    assert strip_affirmative_license_markers(text) == "前款未經官方正版授權；"


@pytest.mark.parametrize(
    "statement",
    [
        "無線耳機，正版授權",
        "非洲系列；正版授權",
        "沒有疑問，正版授權",
    ],
)
def test_unrelated_context_in_another_clause_does_not_block_license(statement):
    assert has_affirmative_license_marker(statement)


def test_inline_positive_branded_marker_is_removed_from_product_name():
    text = "正版授權 新品測試商品"

    assert has_affirmative_license_marker(text)
    assert strip_affirmative_license_markers(text) == "新品測試商品"


def test_established_sheet_name_prefix_is_allowlisted():
    text = "新品#正版授權 三麗鷗測試商品"

    assert has_affirmative_license_marker(text)
    assert strip_affirmative_license_markers(text) == "三麗鷗測試商品"


def test_mn202450_exact_source_prefix_preserves_miniso_name_content():
    text = "爆品#正版授权MINISO"

    assert has_affirmative_license_marker(text)
    assert strip_affirmative_license_markers(text) == "MINISO"
