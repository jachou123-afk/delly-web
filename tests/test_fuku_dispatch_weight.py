"""Free delivered 富酷 quotes retain raw weight notes without inventing billed weight."""

from copy import deepcopy

import pytest

from dispatch_manager import new_batch
from dispatch_review import inspect_source
from line_ad_copy import validate_sheet_pricing

TEST_CODES = {"G正版": "G", "S生活用品": "S", "富酷": "FK"}


def fuku_block(unit="盒", weight_note="重量 700g/880g（原文，未指定淨重／毛重）"):
    return [
        ["no1", "測試商品", "10%報價", "13%報價", "15%報價", "20%報價",
         "進價rmb", "重量g/盒", "大陸運費rmb", "國際運費", "預估到手成本", "v富酷"],
        ["2026/11/3", f"計價單位：{unit}\n內容：一盒", "286.1", "296", "302.9",
         "321.9", "51.5", "", "0", "0", "257.5"],
        ["", f"裝箱 36{unit}/箱", "", "", "", "", "", "", "包郵海快到台灣"],
        ["", weight_note], ["", "貨號 L269301"], [],
    ]


def test_fuku_free_delivered_quote_builds_tiered_ad_copy():
    source = inspect_source("富酷", fuku_block(), TEST_CODES)
    assert source["errors"] == []
    assert "售價287元/盒" in source["copy"]
    source.update(identity="富酷:no1", key="富酷:1:no1", source_hash="test-source")
    batch = new_batch("test", "【直發】自動排廣告群組", [source], "Codex",
                      pricing_policy="cost_tier_13_18_v1")
    assert "售價296元/盒" in batch["items"][0]["copy"]


@pytest.mark.parametrize("change", [
    lambda rows: rows[2].__setitem__(8, "台灣包郵"),
    lambda rows: rows[1].__setitem__(9, "5"),
    lambda rows: rows[3].__setitem__(1, "重量 700g/880g"),
    lambda rows: rows[0].__setitem__(11, "v多品村"),
])
def test_fuku_weight_exception_requires_matching_evidence(change):
    rows = deepcopy(fuku_block())
    change(rows)
    with pytest.raises(ValueError):
        validate_sheet_pricing(rows)
