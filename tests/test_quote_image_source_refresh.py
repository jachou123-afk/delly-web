"""Synthetic Sheet/NAS only; no live product, source text, or originals."""
from copy import deepcopy
import json

import pytest

from dispatch_manager import DispatchError, digest
from dispatch_storage import decode_records, encode_record
from nas_dispatch_storage import LOCATION_SHEET
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair
from quote_image_append import _require_plan_binding
from quote_image_source_refresh import (MODE, _attempt_digest, can_refresh_binding_source,
    prepare_source_refresh_attempt, refresh_binding_source, verify_source_refresh_attempt)
from test_quote_image_append import fixture as append_fixture, rows


def fixture(count=2, extra=False):
    sheet,store,net,oldplan,originals,_=append_fixture(count)
    if extra:
        old=store.product_image_bindings()[oldplan["source"]["identity"]]
        value={k:deepcopy(v) for k,v in old.items() if k!="_revision"}
        value["custom_metadata"]={"nested":["keep",{"extra":True}],"empty":[]}
        sheet.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(old["identity"],value,old["_revision"])
    sheet.sheets["G正版"].rows[1][0]="2026/10/30"
    plan=prepare_image_repair(store,oldplan["source"]["identity"])
    assert plan["source"]["source_hash"]!=plan["binding"]["source_hash"]
    attempt=prepare_source_refresh_attempt(plan,actor="refresh operator",confirmed_same_product=True)
    net.sessions.clear()
    return sheet,store,net,plan,originals,attempt


def assert_only_binding_append(sheet,before,attempt):
    after=rows(sheet)
    assert {k:v for k,v in after.items() if k!=PRODUCT_IMAGE_SHEET}=={k:v for k,v in before.items() if k!=PRODUCT_IMAGE_SHEET}
    assert after[PRODUCT_IMAGE_SHEET][:len(before[PRODUCT_IMAGE_SHEET])]==before[PRODUCT_IMAGE_SHEET]
    new=decode_records(after[PRODUCT_IMAGE_SHEET][len(before[PRODUCT_IMAGE_SHEET]):])
    assert len(new)==1 and new[0]["parent"]==attempt["plan"]["binding"]["_revision"]
    assert new[0]["id"]==attempt["binding"]["_revision"]


@pytest.mark.parametrize("count",[1,2,10])
@pytest.mark.parametrize("extra",[False,True])
def test_only_source_hash_changes_two_fresh_sessions_zero_upload_zero_location_write(count,extra):
    sheet,store,net,plan,originals,attempt=fixture(count,extra)
    before,physical=rows(sheet),deepcopy(net.objects)
    def forbidden(*a,**kw):raise AssertionError("forbidden asset/location mutation")
    store.put_assets=store.put_asset=store._publish_locations=forbidden
    sheet.sheets[LOCATION_SHEET].before_append=forbidden
    net.on_upload=forbidden
    result=refresh_binding_source(store,attempt)
    old,got=plan["binding"],store.product_image_bindings()[plan["source"]["identity"]]
    assert {k:v for k,v in got.items() if k not in {"_revision","source_hash"}}=={
        k:v for k,v in old.items() if k not in {"_revision","source_hash"}}
    assert got["source_hash"]==plan["source"]["source_hash"] and got==attempt["binding"]
    assert len(net.sessions)==2 and not net.uploads() and net.objects==physical
    assert_only_binding_append(sheet,before,attempt)
    receipt=result["receipt"]
    assert receipt["mode"]==MODE and receipt["previous_binding"]==old and receipt["binding"]==got
    assert receipt["sha256"]==[a["sha256"] for a in originals] and receipt["main_sha256"]==originals[0]["sha256"]
    assert receipt["previous_source_hash"]==old["source_hash"] and receipt["source_hash"]==got["source_hash"]
    assert receipt["digest"]==digest({k:v for k,v in receipt.items() if k!="digest"})
    assert receipt["actor"]=="refresh operator" and got["actor"]==old["actor"]
    with pytest.raises(DispatchError):_require_plan_binding(plan,plan["source"])
    fresh=prepare_image_repair(store,plan["source"]["identity"])
    if count<10:assert _require_plan_binding(fresh,fresh["source"])==got
    else:
        with pytest.raises(DispatchError):_require_plan_binding(fresh,fresh["source"])


@pytest.mark.parametrize("confirmed",[False,None,1,"yes"])
def test_explicit_confirmation_required_before_io(confirmed):
    sheet,_,net,plan,_,_=fixture()
    before=rows(sheet)
    with pytest.raises(DispatchError,match="逐張核對"):
        prepare_source_refresh_attempt(plan,actor="operator",confirmed_same_product=confirmed)
    assert rows(sheet)==before and not net.sessions


@pytest.mark.parametrize("damage",["missing","schema_bool","subject","source_hash","same_hash","revision","actor","snapshot","duplicate_asset","bad_asset","NaN"])
def test_bad_or_unneeded_plan_has_no_io(damage):
    sheet,_,net,plan,_,_=fixture()
    if damage=="missing":plan["binding"]=None
    elif damage=="schema_bool":plan["binding"]["schema"]=True
    elif damage=="subject":plan["binding"]["subject"]="different"
    elif damage=="source_hash":plan["binding"]["source_hash"]="bad"
    elif damage=="same_hash":plan["binding"]["source_hash"]=plan["source"]["source_hash"]
    elif damage=="revision":plan["binding"]["_revision"]="other"
    elif damage=="actor":plan["binding"]["actor"]=""
    elif damage=="snapshot":plan["snapshot_digest"]="0"*64
    elif damage=="duplicate_asset":plan["binding"]["assets"]*=2
    elif damage=="bad_asset":plan["binding"]["assets"]=["bad"]
    else:plan["binding"]["extra"]=float("nan")
    assert not can_refresh_binding_source(plan)
    with pytest.raises(DispatchError):prepare_source_refresh_attempt(plan,actor="operator",confirmed_same_product=True)
    assert not net.sessions


@pytest.mark.parametrize("damage",["extra_target_field","changed_order","changed_old_actor","records","extra_attempt_key","numeric_type"])
def test_recomputed_attempt_digest_cannot_add_payload_fields_change_order_or_records(damage):
    sheet,store,net,_,_,attempt=fixture()
    before=rows(sheet)
    if damage=="extra_target_field":attempt["binding"]["unexpected"]=True
    elif damage=="changed_order":attempt["binding"]["assets"].reverse()
    elif damage=="changed_old_actor":attempt["binding"]["actor"]="not preserved"
    elif damage=="records":attempt["records"][-1][-2]+="broken"
    elif damage=="numeric_type":attempt["binding"]["schema"]=1.0
    else:attempt["unexpected"]=True
    attempt["digest"]=_attempt_digest(attempt)
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert rows(sheet)==before and not net.sessions


@pytest.mark.parametrize("replacement",[1,1.0])
def test_full_binding_CAS_rejects_extra_payload_bool_int_float_confusion(replacement):
    sheet,store,net,_,_,attempt=fixture(extra=True)
    original=store.product_image_bindings
    def confused():
        result=original()
        result[attempt["binding"]["identity"]]["custom_metadata"]["nested"][1]["extra"]=replacement
        return result
    store.product_image_bindings=confused
    before=rows(sheet)
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert rows(sheet)==before and not net.sessions


def test_full_source_snapshot_change_with_same_block_hash_and_subject_is_not_ignored():
    sheet,store,net,_,_,attempt=fixture()
    original=store.category_settings
    def changed_codes():
        result=deepcopy(original());result["codes"]["G正版"]="X"
        return result
    store.category_settings=changed_codes
    before=rows(sheet)
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert rows(sheet)==before and not net.sessions


@pytest.mark.parametrize("phase",[1,2])
@pytest.mark.parametrize("change",["source","formula","binding","extra_binding","location"])
def test_races_during_each_NAS_read_never_produce_completed_receipt(phase,change):
    sheet,store,net,plan,_,attempt=fixture()
    changed=False
    def mutate(params):
        nonlocal changed
        if changed or len(net.sessions)!=phase:return
        changed=True
        if change=="source":sheet.sheets["G正版"].rows[1][6]="999"
        elif change=="formula":
            sheet.sheets["G正版"].formula_override=deepcopy(plan["formulas"])
            sheet.sheets["G正版"].formula_override[1][10]+="+0"
        elif change in {"binding","extra_binding"}:
            old=store.product_image_bindings()[plan["source"]["identity"]]
            value={k:deepcopy(v) for k,v in old.items() if k!="_revision"}
            value["actor" if change=="binding" else "concurrent_extra"]="other writer"
            sheet.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(old["identity"],value,old["_revision"])
        else:
            locs=store.image_locations(plan["binding"]["assets"])
            sha=plan["binding"]["assets"][0]
            value=deepcopy(locs[sha]);value["size"]+=1
            sheet.sheets[LOCATION_SHEET].rows+=encode_record(sha,value)
    net.on_download=mutate
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert changed and not net.uploads()
    if phase==1:assert attempt["state"]=="prepared"
    else:assert attempt["state"]=="write_attempted"


@pytest.mark.parametrize("change",["source","formula","binding"])
def test_races_at_append_boundary_are_failed_after_write_without_retry(change):
    sheet,store,net,plan,_,attempt=fixture()
    def mutation():
        if change=="source":sheet.sheets["G正版"].rows[1][0]="2026/10/31"
        elif change=="formula":
            sheet.sheets["G正版"].formula_override=deepcopy(plan["formulas"])
            sheet.sheets["G正版"].formula_override[1][10]+="+0"
        else:
            old=plan["binding"]
            value={k:deepcopy(v) for k,v in old.items() if k!="_revision"};value["actor"]="other writer"
            sheet.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(old["identity"],value,old["_revision"])
    sheet.sheets[PRODUCT_IMAGE_SHEET].before_append=mutation
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert attempt["state"]=="write_attempted" and not net.uploads()
    before=rows(sheet)
    with pytest.raises(DispatchError,match="不可重送"):refresh_binding_source(store,attempt)
    assert rows(sheet)==before


@pytest.mark.parametrize("failure",["missing_index","missing_file","corrupt_file","first_timeout"])
def test_partial_failure_before_write_preserves_all_sheet_rows(failure):
    sheet,store,net,_,originals,attempt=fixture()
    path=store._nas()._path(store._nas()._metadata(originals[0]))
    if failure=="missing_index":sheet.sheets[LOCATION_SHEET].rows=sheet.sheets[LOCATION_SHEET].rows[:1]
    elif failure=="missing_file":del net.objects[path]
    elif failure=="corrupt_file":net.objects[path]=b"corrupt"
    else:
        def timeout(params):raise TimeoutError("private response")
        net.on_download=timeout
    before=rows(sheet)
    with pytest.raises(Exception):refresh_binding_source(store,attempt)
    assert rows(sheet)==before and attempt["state"]=="prepared" and not net.uploads()


@pytest.mark.parametrize("server_wrote",[False,True])
def test_uncertain_write_is_never_resent_and_explicit_recovery_is_readonly(server_wrote):
    sheet,store,net,plan,_,attempt=fixture()
    ws=sheet.sheets[PRODUCT_IMAGE_SHEET]
    original=ws.append_rows
    calls=0
    def failing(*a,**kw):
        nonlocal calls
        calls+=1
        if server_wrote:original(*a,**kw)
        raise TimeoutError("private unknown write result")
    ws.append_rows=failing
    with pytest.raises(DispatchError,match="只能唯讀"):refresh_binding_source(store,attempt)
    assert calls==1 and attempt["state"]=="write_attempted"
    before=rows(sheet);net.sessions.clear()
    with pytest.raises(DispatchError,match="不可重送"):refresh_binding_source(store,attempt)
    assert calls==1 and not net.sessions
    attempt=json.loads(json.dumps(attempt))  # Recovery works with the exact persisted prepared target.
    report=verify_source_refresh_attempt(store,attempt)
    assert rows(sheet)==before and calls==1 and not net.uploads()
    if server_wrote:
        assert report["結果"]=="已刷新" and len(net.sessions)==2
        assert report["receipt"]["binding"]==attempt["binding"]
    else:assert report["結果"]=="未發布" and not net.sessions and "receipt" not in report


def test_second_read_failure_after_known_write_requires_readonly_recovery():
    sheet,store,net,_,_,attempt=fixture()
    def timeout(params):
        if len(net.sessions)==2:raise TimeoutError("private second read error")
    net.on_download=timeout
    with pytest.raises(TimeoutError):refresh_binding_source(store,attempt)
    before=rows(sheet);net.on_download=None;net.sessions.clear()
    assert verify_source_refresh_attempt(store,attempt)["結果"]=="已刷新"
    assert rows(sheet)==before and len(net.sessions)==2 and not net.uploads()


def test_readonly_recovery_rejects_same_payload_with_different_revision():
    sheet,store,net,plan,_,attempt=fixture()
    old=plan["binding"];value={k:v for k,v in attempt["binding"].items() if k!="_revision"}
    sheet.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(old["identity"],value,old["_revision"])
    before=rows(sheet)
    with pytest.raises(DispatchError):verify_source_refresh_attempt(store,attempt)
    assert rows(sheet)==before and not net.sessions


def test_final_formula_response_new_same_image_binding_revision_is_not_success():
    sheet,store,net,plan,_,attempt=fixture()
    ws=sheet.sheets["G正版"];original=ws.get;reads=0;changed=False
    def racing_get(area,value_render_option=None):
        nonlocal reads,changed
        result=original(area,value_render_option)
        if value_render_option=="FORMULA":
            reads+=1
            if reads==5:
                current=store.product_image_bindings()[plan["source"]["identity"]]
                value={k:deepcopy(v) for k,v in current.items() if k!="_revision"}
                value["actor"]="writer at final FORMULA response"
                sheet.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(current["identity"],value,current["_revision"])
                changed=True
        return result
    ws.get=racing_get
    with pytest.raises(DispatchError):refresh_binding_source(store,attempt)
    assert changed and reads==5 and len(net.sessions)==2 and not net.uploads()
