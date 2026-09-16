from copy import deepcopy

from quote_dispatch.pipeline.parse import parse_supplier_text, parser_context
from quote_dispatch.pipeline.reconcile import reconcile_batch


def test_parser_routing_uses_common_profile_without_changing_source():
    raw = "廠商原始文案"
    seen = []

    def parser(value):
        seen.append(value)
        return {"ok": True}

    assert parser_context("V-多品村") == {"vendor": "v多品村", "profile": "common"}
    assert parse_supplier_text(raw, "V-多品村", parser) == {"ok": True}
    assert seen == [raw]


def test_reconciliation_stage_is_pure():
    batch = {
        "status": "approved",
        "approved_digest": "same",
        "items": [{
            "id": "G:no1", "excluded": False,
            "source": {"code": "G-1"},
            "image_receipts": [], "text_receipts": [],
        }],
        "observations": [],
    }
    before = deepcopy(batch)
    report = reconcile_batch(batch, lambda _item: "待發", lambda _batch: "same")
    assert report["missing"] == ["G-1"]
    assert not report["can_finish"]
    assert batch == before
