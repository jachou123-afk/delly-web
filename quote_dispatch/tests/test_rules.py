import json
import shutil

import pytest

from quote_dispatch.config import (
    RULES_DIR,
    RuleConfigError,
    _load_rules,
    advertising_target,
    canonical_target,
    normalize_vendor,
    supplier_rule,
)


@pytest.mark.parametrize("source,expected", [
    ("V-非凡", "v菲凡"),
    ("v多品", "v多品村"),
    ("卡樂星", "v優娜卡樂星"),
    ("阿汤", "v阿湯"),
])
def test_confirmed_aliases_come_from_rule_files(source, expected):
    assert normalize_vendor(source) == expected


def test_only_multi_product_village_has_free_shipping():
    assert supplier_rule("v多品村").domestic_shipping == "free"
    for vendor in ("v菲凡", "v優娜卡樂星", "v阿湯", "v自訂廠商"):
        assert supplier_rule(vendor).domestic_shipping == "calculated"
        assert supplier_rule(vendor).domestic_shipping_note == ""


def test_line_room_rename_is_exact_not_fuzzy():
    current = advertising_target()
    assert canonical_target("【自動排廣告群組】") == current
    assert canonical_target("【利潤10%】自動排廣告群組") == current
    assert canonical_target("【利潤15%】自動排廣告群組") != current


def test_supplier_file_change_does_not_bleed_to_other_suppliers(tmp_path):
    copied = tmp_path / "rules"
    shutil.copytree(RULES_DIR, copied)
    multi_path = copied / "suppliers" / "v多品村.yaml"
    multi = json.loads(multi_path.read_text(encoding="utf-8"))
    multi["pricing"]["domestic_shipping_note"] = "測試用包郵文字"
    multi_path.write_text(json.dumps(multi, ensure_ascii=False), encoding="utf-8")

    registry = _load_rules(copied)
    assert registry.suppliers["v多品村"].domestic_shipping_note == "測試用包郵文字"
    assert registry.suppliers["v菲凡"].domestic_shipping == "calculated"
    assert registry.suppliers["v菲凡"].domestic_shipping_note == ""


def test_invalid_supplier_rule_fails_closed(tmp_path):
    copied = tmp_path / "rules"
    shutil.copytree(RULES_DIR, copied)
    path = copied / "suppliers" / "v菲凡.yaml"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["pricing"]["domestic_shipping"] = "guess"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuleConfigError, match="內陸運費"):
        _load_rules(copied)
