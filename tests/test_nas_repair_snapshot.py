"""Native gspread over in-memory HTTP plus synthetic NAS; no external calls."""
from copy import deepcopy
import json
import re
from types import SimpleNamespace
from urllib.parse import unquote

from gspread.http_client import HTTPClient
from gspread.spreadsheet import Spreadsheet
import pytest
import requests

from category_codes import CATEGORY_SHEET
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import HEADER, asset_bytes, encode_record
from nas_dispatch_storage import NasDispatchStore, LOCATION_SHEET
from nas_repair_snapshot import prepare_batched_existing_image_repair as _prepare_native, BAD_RESPONSE
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair
import quote_image_repair_batch as batch
import nas_original_adoption as adoption
from quote_image_repair_zip import read_repair_zip
from test_nas_dispatch_storage import Network
from test_quote_image_repair_zip import image, pack, sha
from test_synology_image_store import config, Response


def prepare_batched_existing_image_repair(store, identity, *, source_hint=None):
    return _prepare_native(store, identity, source_hint=source_hint if source_hint is not None else {
        "identity":identity,"category":identity.rsplit(":",1)[0],"row":1})


class GoogleMemory:
    def __init__(self, sheets=None):
        self.book=FakeSpreadsheet(sheets or product_rows((1,2)))
        for title in (CATEGORY_SHEET,PRODUCT_IMAGE_SHEET,LOCATION_SHEET):
            self.book.add_worksheet(title,rows=1000,cols=20).rows=[list(HEADER)]
        for title in ("_報價依據","_發送批次","_人工核對","_LINE紀錄"):
            self.book.add_worksheet(title,rows=1000,cols=20).rows=[["preserve unrelated data"]]
        self.ids={title:n for n,title in enumerate(self.book.sheets)}
        self.grid_rows={}
        self.grid_cols={}
        self.id="synthetic-book"
        self.calls=[]
        self.before=self.after=None
        self.error=None

    @staticmethod
    def col(value):
        n=0
        for c in value:n=n*26+ord(c)-64
        return n

    @staticmethod
    def column(number):
        value=""
        while number:
            number,rem=divmod(number-1,26);value=chr(65+rem)+value
        return value

    def metadata(self):
        return {"spreadsheetId":self.id,"properties":{"title":"synthetic","locale":"zh_TW"},"sheets":[{
            "properties":{"sheetId":self.ids[title],"title":title,"gridProperties":{
                "rowCount":self.grid_rows.get(title,1000),"columnCount":self.grid_cols.get(title,20)}}}
            for title in self.book.sheets]}

    def read(self, area, render=None):
        match=re.fullmatch(r"(?:'((?:[^']|'')*)'|([^!]+))(?:!(.*))?",area)
        assert match,area
        title=match[1].replace("''", "'") if match[1] is not None else match[2]
        grid_rows=self.grid_rows.get(title,1000);grid_cols=self.grid_cols.get(title,20)
        span=match[3] or f"A1:{self.column(grid_cols)}{grid_rows}"
        bound=re.fullmatch(r"([A-Z]*)([0-9]*):([A-Z]*)([0-9]*)",span)
        if bound:
            c1=self.col(bound[1] or "A");r1=int(bound[2] or 1)
            c2=self.col(bound[3]) if bound[3] else grid_cols;r2=int(bound[4] or grid_rows)
        else:
            single=re.fullmatch(r"([A-Z]+)([0-9]+)",span);assert single,span
            c1=c2=self.col(single[1]);r1=r2=int(single[2])
        ws=self.book.sheets[title]
        if render=="FORMULA" and c1==1 and c2==12 and r2-r1==5:
            values=ws.get(f"A{r1}:L{r2}",value_render_option="FORMULA")
        else:
            values=[list(row[c1-1:c2]) for row in ws.rows[r1-1:r2]]
        for row in values:
            while row and row[-1]=="":row.pop()
        while values and not values[-1]:values.pop()
        canonical="'"+title.replace("'","''")+f"'!{self.column(c1)}{r1}:{self.column(c2)}{r2}"
        return {"range":canonical,"majorDimension":"ROWS",**({"values":deepcopy(values)} if values else {})}

    def request(self, **kwargs):
        method=kwargs["method"].upper();url=unquote(kwargs["url"]);params=kwargs.get("params") or {}
        operation=("batch" if url.endswith("/values:batchGet") else "append" if url.endswith(":append") else
                   "values" if "/values/" in url else "metadata")
        call={"method":method,"operation":operation,"ranges":deepcopy(params.get("ranges")),"render":params.get("valueRenderOption")}
        self.calls.append(call)
        if self.before:self.before(call)
        if self.error:raise self.error
        if operation=="metadata":
            body=self.metadata()
            if params.get("fields")=="spreadsheetId,sheets.properties":
                body={k:body[k] for k in ("spreadsheetId","sheets")}
        elif operation=="batch":
            body={"spreadsheetId":self.id,"valueRanges":[self.read(area,params.get("valueRenderOption")) for area in params["ranges"]]}
        elif operation=="values":body=self.read(url.split("/values/",1)[1],params.get("valueRenderOption"))
        else:
            assert method=="POST"
            area=url.split("/values/",1)[1][:-len(":append")]
            title=re.fullmatch(r"'((?:[^']|'')*)'!.*",area)[1].replace("''","'")
            values=kwargs["json"]["values"]
            ws=self.book.sheets[title]
            ws.append_rows(values,value_input_option="RAW",table_range="A:H")
            body={"spreadsheetId":self.id,"updates":{"updatedRows":len(values),
                "updatedRange":"'"+title+f"'!A{len(ws.rows)-len(values)+1}:H{len(ws.rows)}"}}
        if self.after:
            changed=self.after(call,body)
            if changed is not None:body=changed
        return SimpleNamespace(ok=True,status_code=200,json=lambda:deepcopy(body),text="synthetic private detail")


def fixture(*, warm=True):
    memory=GoogleMemory()
    google=Spreadsheet(HTTPClient(None,session=memory),{"id":memory.id})
    network=Network()
    store=NasDispatchStore(google,config(),nas_factory=network.factory)
    raw=image(8);key=sha(raw)
    package=read_repair_zip(pack({"schema":1,"products":[{"sheet":"G正版","actual_no":"no1","sha256":[key]}]},[(f"images/{key}.png",raw)]))
    asset=package["assets"][key]
    metadata=store._nas()._metadata(asset)
    network.objects[store._nas()._path(metadata)]=asset_bytes(asset)
    if warm:
        for title in (CATEGORY_SHEET,PRODUCT_IMAGE_SHEET,LOCATION_SHEET):store._sheet(title)
    memory.calls.clear()
    return memory,store,network,package,asset


def all_rows(memory):return {name:deepcopy(ws.rows) for name,ws in memory.book.sheets.items()}
def get_count(memory):return sum(call["method"]=="GET" for call in memory.calls)
def uploads(network):return [kw for s in network.sessions for _,kw in s.calls if kw["data"]["api"]=="SYNO.FileStation.Upload"]


def test_one_guard_is_exactly_metadata_target_formula_then_open_formatted_batch():
    memory,store,_,_,_=fixture()
    original=prepare_image_repair(store,"G正版:no1")
    memory.calls.clear();before=all_rows(memory)
    plan=prepare_batched_existing_image_repair(store,"G正版:no1")
    assert [c["operation"] for c in memory.calls]==["metadata","batch","batch"]
    assert [c["render"] for c in memory.calls]==[None,"FORMULA","FORMATTED_VALUE"]
    record_ids=plan["source"].pop("_repair_record_sheet_ids")
    original.pop("snapshot_digest");plan.pop("snapshot_digest")
    assert plan==original and set(record_ids)=={CATEGORY_SHEET,PRODUCT_IMAGE_SHEET}
    assert all_rows(memory)==before


def test_guard_calls_always_make_new_three_reads_not_cache_previous_values():
    memory,store,_,_,_=fixture()
    first=prepare_batched_existing_image_repair(store,"G正版:no1")
    memory.book.sheets["G正版"].rows[0][1]="updated title"
    second=prepare_batched_existing_image_repair(store,"G正版:no1")
    assert get_count(memory)==6 and first["snapshot_digest"]!=second["snapshot_digest"]


def test_existing_adoption_warm_reads_drop_34_to_22_without_write_or_NAS_change(monkeypatch):
    results=[]
    for optimized in (False,True):
        memory,store,network,package,asset=fixture()
        run=batch.prepare_batch_repair(store,package,mode="existing_only")
        memory.calls.clear();physical=dict(network.objects)
        with monkeypatch.context() as m:
            if not optimized:m.setattr(batch,"supports_batched_existing_snapshot",lambda _:False)
            result=batch.process_next_repair(store,package,run,actor="synthetic")
        assert result["items"][0]["status"]=="completed"
        assert result["items"][0]["receipt"]["sha256"]==[asset["sha256"]]
        assert len(network.sessions)==2 and network.objects==physical and not uploads(network)
        assert len([c for c in memory.calls if c["method"]=="POST"])==2
        if optimized:
            assert len([c for c in memory.calls if c["operation"]=="metadata"])==4
            assert len([c for c in memory.calls if c["operation"]=="batch" and c["render"]=="FORMATTED_VALUE"])==4
            assert len([c for c in memory.calls if c["operation"]=="batch" and c["render"]=="FORMULA"])==4
        results.append((get_count(memory),all_rows(memory),result["items"][0]["receipt"]))
    assert [x[0] for x in results]==[34,22]
    for result in results:
        assert set(result[1])==set(results[0][1])
    assert results[0][1]["G正版"]==results[1][1]["G正版"]
    for title in ("_報價依據","_發送批次","_人工核對","_LINE紀錄"):
        assert results[0][1][title]==results[1][1][title]==[["preserve unrelated data"]]


@pytest.mark.parametrize("damage",["book","count","duplicate","unknown","columns","type","dimension","extra","missing_range","nonfinite"])
def test_malformed_or_misbound_formatted_response_never_publishes(damage):
    memory,store,network,_,_=fixture()
    def corrupt(call,body):
        if call["operation"]!="batch" or call["render"]!="FORMATTED_VALUE":return
        if damage=="book":body["spreadsheetId"]="another-book"
        elif damage=="count":body["valueRanges"].pop()
        elif damage=="duplicate":body["valueRanges"][1]=deepcopy(body["valueRanges"][0])
        elif damage=="unknown":body["valueRanges"][0]["range"]="'Unknown'!A1:L1000"
        elif damage=="columns":body["valueRanges"][0]["values"][0]+=["extra"]*20
        elif damage=="type":body["valueRanges"][0]["values"]=[{"wrong":"shape"}]
        elif damage=="dimension":body["valueRanges"][0]["majorDimension"]="COLUMNS"
        elif damage=="extra":body["valueRanges"][0]["private_body"]="do not expose"
        elif damage=="missing_range":body["valueRanges"][0].pop("range")
        else:body["valueRanges"][0]["values"][0][0]=float("nan")
    memory.after=corrupt
    before=all_rows(memory)
    with pytest.raises(DispatchError,match="回應範圍或格式"):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert all_rows(memory)==before and len(memory.calls)==3 and not network.sessions


def test_reordered_exact_value_ranges_match_by_identity_not_response_order():
    memory,store,_,_,_=fixture()
    def reorder(call,body):
        if call["operation"]=="batch":body["valueRanges"].reverse()
    memory.after=reorder
    assert prepare_batched_existing_image_repair(store,"G正版:no1")["source"]["no"]=="no1"


@pytest.mark.parametrize("damage",["missing_record_sheet","bad_header","bad_chain","duplicate_metadata","wrong_metadata_id","tiny_grid","formula_range","formula_shape","formula_empty","formula_hole"])
def test_missing_sheets_headers_chain_metadata_and_formula_shapes_fail_closed(damage):
    memory,store,network,_,_=fixture()
    if damage=="missing_record_sheet":del memory.book.sheets[CATEGORY_SHEET]
    elif damage=="bad_header":memory.book.sheets[PRODUCT_IMAGE_SHEET].rows[0][0]="wrong"
    elif damage=="bad_chain":
        memory.book.sheets[CATEGORY_SHEET].rows+=encode_record("category_codes",{"schema":1,"codes":{"G正版":"G","S生活用品":"S"}},"wrong-parent")
    def corrupt(call,body):
        if damage=="duplicate_metadata" and call["operation"]=="metadata":body["sheets"].append(deepcopy(body["sheets"][0]))
        if damage=="wrong_metadata_id" and call["operation"]=="metadata":body["sheets"][0]["properties"]["sheetId"]=True
        if damage=="tiny_grid" and call["operation"]=="metadata":body["sheets"][0]["properties"]["gridProperties"]["columnCount"]=3
        if call["render"]=="FORMULA":
            if damage=="formula_range":body["valueRanges"][0]["range"]="'G正版'!A7:L12"
            elif damage=="formula_shape":body["valueRanges"][0]["values"]="invalid"
            elif damage=="formula_empty":body["valueRanges"][0].pop("values")
            elif damage=="formula_hole":body["valueRanges"][0]["values"][1][6]=""
    memory.after=corrupt
    before=all_rows(memory)
    with pytest.raises((DispatchError,ValueError)):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert all_rows(memory)==before and not network.sessions


def test_duplicate_product_identity_is_never_selected_from_batched_catalog():
    memory,store,network,_,_=fixture()
    memory.book.sheets["G正版"].rows+=deepcopy(memory.book.sheets["G正版"].rows[:6])
    with pytest.raises(DispatchError,match="身分重複"):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert not network.sessions


@pytest.mark.parametrize("failure",["network","429"])
def test_read_failure_does_not_retry_or_fall_back(failure):
    from gspread.exceptions import APIError
    memory,store,_,_,_=fixture()
    if failure=="network":memory.error=requests.ConnectionError("synthetic private URL")
    else:
        def failed(**kw):
            memory.calls.append({"method":kw["method"]})
            return SimpleNamespace(ok=False,status_code=429,json=lambda:{"error":{"code":429,"message":"private"}},text="private")
        memory.request=failed
    with pytest.raises((requests.ConnectionError,APIError)):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert len(memory.calls)==1


@pytest.mark.parametrize("change",["source","formula","record_sheet_replaced","binding","duplicate"])
def test_change_during_first_NAS_read_stops_before_index_publication(change):
    memory,store,network,package,_=fixture()
    original=network.factory
    changed=False
    def factory(settings):
        nas=original(settings);base=nas._session_factory
        def session():
            nonlocal changed
            result=base()
            def hook(params):
                nonlocal changed
                if params["api"]!="SYNO.FileStation.Download" or changed:return
                changed=True
                if change=="source":memory.book.sheets["G正版"].rows[0][1]="different"
                elif change=="formula":
                    ws=memory.book.sheets["G正版"]
                    ws.formula_override=deepcopy(ws.get("A1:L6",value_render_option="FORMULA"));ws.formula_override[1][10]+="+0"
                elif change=="record_sheet_replaced":memory.ids[PRODUCT_IMAGE_SHEET]+=100
                elif change=="duplicate":memory.book.sheets["G正版"].rows+=deepcopy(memory.book.sheets["G正版"].rows[:6])
                else:
                    source=store.catalog()[0]
                    value={"schema":1,"identity":source["identity"],"subject":"other","assets":[package["products"][0]["sha256"][0]]}
                    memory.book.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(source["identity"],value)
            result.override=hook
            return result
        nas._session_factory=session
        return nas
    store._nas_factory=factory
    run=batch.prepare_batch_repair(store,package,mode="existing_only")
    memory.calls.clear();result=batch.process_next_repair(store,package,run,actor="synthetic")
    assert result["items"][0]["status"]=="failed" and changed
    assert not [c for c in memory.calls if c["method"]=="POST"]
    assert memory.book.sheets[LOCATION_SHEET].rows==[HEADER] and not uploads(network)


def test_normal_upload_keeps_original_nonbatched_source_prepare(monkeypatch):
    memory,store,_,package,_=fixture()
    run=batch.prepare_batch_repair(store,package,mode="upload")
    def forbidden(*args,**kwargs):raise AssertionError("normal upload used optimized existing-only guard")
    monkeypatch.setattr(batch,"prepare_batched_existing_image_repair",forbidden)
    memory.calls.clear();result=batch.process_next_repair(store,package,run,actor="synthetic",limit=1)
    assert result["items"][0]["status"]=="completed"
    assert not any(call["render"]=="FORMATTED_VALUE" and call["operation"]=="batch" and any(PRODUCT_IMAGE_SHEET in r for r in call["ranges"] or []) for call in memory.calls)


@pytest.mark.parametrize("phase",["location_write","second_NAS_read"])
@pytest.mark.parametrize("change",["source","formula","binding","record_sheet_replaced","location"])
def test_changes_at_later_write_and_second_read_boundaries_are_not_cached(phase,change):
    memory,store,network,package,_=fixture()
    def changing():
        if change=="source":memory.book.sheets["G正版"].rows[0][1]="changed source"
        elif change=="formula":
            ws=memory.book.sheets["G正版"]
            ws.formula_override=deepcopy(ws.get("A1:L6",value_render_option="FORMULA"));ws.formula_override[1][10]+="+0"
        elif change=="record_sheet_replaced":memory.ids[CATEGORY_SHEET]+=200
        elif change=="location":
            key=package["products"][0]["sha256"][0]
            meta=store._nas()._metadata(package["assets"][key]);meta["size"]+=1
            memory.book.sheets[LOCATION_SHEET].rows+=encode_record(key,meta)
        else:
            source=store.catalog()[0]
            previous=store.product_image_bindings().get(source["identity"])
            value={"schema":1,"identity":source["identity"],"subject":"other writer","assets":package["products"][0]["sha256"]}
            memory.book.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record(source["identity"],value,previous["_revision"] if previous else "")
    if phase=="location_write":memory.book.sheets[LOCATION_SHEET].before_append=changing
    else:
        original=network.factory
        def factory(settings):
            nas=original(settings);base=nas._session_factory
            def session():
                result=base()
                def hook(params):
                    if params["api"]=="SYNO.FileStation.Download" and len(network.sessions)==2:
                        changing()
                result.override=hook
                return result
            nas._session_factory=session
            return nas
        store._nas_factory=factory
    run=batch.prepare_batch_repair(store,package,mode="existing_only")
    memory.calls.clear();result=batch.process_next_repair(store,package,run,actor="synthetic")
    assert result["items"][0]["status"]=="failed" and result["items"][0]["receipt"] is None
    if phase=="location_write" and change!="binding":
        assert memory.book.sheets[PRODUCT_IMAGE_SHEET].rows==[HEADER]
    assert not uploads(network)


@pytest.mark.parametrize("damage",["book","missing_sheets","not_list","bad_item","missing_grid","bad_rows","bad_cols"])
def test_metadata_identity_and_grid_shapes_fail_before_value_reads(damage):
    memory,store,network,_,_=fixture()
    def corrupt(call,body):
        if call["operation"]!="metadata":return
        if damage=="book":body["spreadsheetId"]="wrong-book"
        elif damage=="missing_sheets":body.pop("sheets")
        elif damage=="not_list":body["sheets"]={}
        elif damage=="bad_item":body["sheets"][0]={"properties":[]}
        elif damage=="missing_grid":body["sheets"][0]["properties"].pop("gridProperties")
        elif damage=="bad_rows":body["sheets"][0]["properties"]["gridProperties"]["rowCount"]=True
        else:body["sheets"][0]["properties"]["gridProperties"]["columnCount"]="20"
    memory.after=corrupt
    with pytest.raises(DispatchError,match="回應範圍或格式"):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert len(memory.calls)==1 and not network.sessions


def test_google_canonical_unquoted_simple_sheet_ranges_are_unambiguous():
    memory,store,_,_,_=fixture()
    def unquote_ranges(call,body):
        if call["operation"]=="batch":
            for value in body["valueRanges"]:
                value["range"]=value["range"].replace("'","")
    memory.after=unquote_ranges
    assert prepare_batched_existing_image_repair(store,"G正版:no1")["source"]["identity"]=="G正版:no1"


def test_sheet_names_with_apostrophe_and_exclamation_are_escaped_not_split():
    memory,store,_,_,_=fixture()
    title="W'特別!分頁"
    memory.book.add_worksheet(title,rows=1000,cols=20).rows=[]
    memory.ids[title]=998
    plan=prepare_batched_existing_image_repair(store,"G正版:no1")
    assert plan["source"]["no"]=="no1"
    formatted=next(c for c in memory.calls if c["render"]=="FORMATTED_VALUE")
    assert "'W''特別!分頁'!A:L" in formatted["ranges"]


def test_uncertain_location_write_sends_once_no_binding_or_second_NAS_read():
    memory,store,network,package,_=fixture()
    memory.book.sheets[LOCATION_SHEET].fail_append_after_write=True
    run=batch.prepare_batch_repair(store,package,mode="existing_only")
    memory.calls.clear();result=batch.process_next_repair(store,package,run,actor="synthetic")
    assert result["items"][0]["status"]=="failed" and result["items"][0]["receipt"] is None
    assert len([c for c in memory.calls if c["method"]=="POST"])==1
    assert memory.book.sheets[PRODUCT_IMAGE_SHEET].rows==[HEADER] and len(network.sessions)==1
    assert not uploads(network)


@pytest.mark.parametrize("sheet",[CATEGORY_SHEET,PRODUCT_IMAGE_SHEET])
@pytest.mark.parametrize("where",["header","record"])
def test_extra_record_columns_are_not_silently_clipped(sheet,where):
    memory,store,network,_,_=fixture()
    ws=memory.book.sheets[sheet]
    if where=="header":ws.rows[0].append("unexpected I header")
    else:
        value={"schema":1,"codes":{"G正版":"G","S生活用品":"S"}}
        ws.rows+=encode_record("category_codes",value)
        ws.rows[-1].append("unexpected I record value")
    before=all_rows(memory)
    with pytest.raises(DispatchError):prepare_batched_existing_image_repair(store,"G正版:no1")
    assert not network.sessions and all_rows(memory)==before
    formatted=next(call for call in memory.calls if call["render"]=="FORMATTED_VALUE")
    assert "'"+sheet+"'" in formatted["ranges"]


def test_record_rows_keep_get_all_values_padding_for_legitimate_empty_stamp():
    memory,store,_,_,_=fixture()
    ws=memory.book.sheets[CATEGORY_SHEET]
    ws.rows+=encode_record("category_codes",{"schema":1,"codes":{"G正版":"G","S生活用品":"S"}})
    ws.rows[-1].pop()  # Empty stamp is omitted by Google values, then padded.
    original=prepare_image_repair(store,"G正版:no1")
    plan=prepare_batched_existing_image_repair(store,"G正版:no1")
    assert plan["source"]["source_hash"]==original["source"]["source_hash"]
    assert plan["binding"]==original["binding"] and get_count(memory)>0


@pytest.mark.parametrize("extra",[False,True])
def test_record_grid_columns_beyond_Z_are_converted_and_validated(extra):
    memory,store,network,_,_=fixture()
    memory.grid_cols.update({CATEGORY_SHEET:30,PRODUCT_IMAGE_SHEET:30})
    if extra:
        memory.book.sheets[PRODUCT_IMAGE_SHEET].rows[0]+=[""]*19+["unexpected AB header"]
        with pytest.raises(DispatchError,match="欄位不符"):
            prepare_batched_existing_image_repair(store,"G正版:no1")
        assert not network.sessions
    else:
        assert prepare_batched_existing_image_repair(store,"G正版:no1")["source"]["no"]=="no1"
    formatted=next(c for c in memory.calls if c["render"]=="FORMATTED_VALUE")
    assert "'"+PRODUCT_IMAGE_SHEET+"'" in formatted["ranges"]


@pytest.mark.parametrize("hint",[None,{}, {"identity":"G正版:no2","category":"G正版","row":1},
    {"identity":"G正版:no1","category":"S生活用品","row":1},
    {"identity":"G正版:no1","category":"G正版","row":True},
    {"identity":"G正版:no1","category":"G正版","row":0}])
def test_malformed_source_hint_fails_before_any_google_or_NAS_read(hint):
    memory,store,network,_,_=fixture()
    with pytest.raises(DispatchError,match="位置提示不完整"):
        _prepare_native(store,"G正版:no1",source_hint=hint)
    assert not memory.calls and not network.sessions


def test_stale_hint_stops_without_retry_when_actual_product_row_moved():
    memory,store,network,_,_=fixture()
    rows=memory.book.sheets["G正版"].rows
    rows[:]=deepcopy(rows[6:12])+deepcopy(rows[:6])
    with pytest.raises(DispatchError,match="位置無法唯一識別"):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert get_count(memory)==3 and not network.sessions


def test_final_formula_response_binding_revision_race_fails_instead_of_stale_completed_receipt():
    memory,store,network,package,_=fixture()
    queue=batch.prepare_batch_repair(store,package,mode="existing_only")
    formula_reads=0
    changed=False
    def race(call,body):
        nonlocal formula_reads,changed
        if call["render"]!="FORMULA":return
        formula_reads+=1
        if formula_reads!=4:return
        previous=store.product_image_bindings()["G正版:no1"]
        value={key:deepcopy(v) for key,v in previous.items() if key!="_revision"}
        value["actor"]="concurrent writer after final FORMULA response"
        memory.book.sheets[PRODUCT_IMAGE_SHEET].rows+=encode_record("G正版:no1",value,previous["_revision"])
        changed=True
    memory.calls.clear();memory.after=race
    result=batch.process_next_repair(store,package,queue,actor="synthetic")
    assert changed and formula_reads==4
    assert result["items"][0]["status"]=="failed" and result["items"][0]["receipt"] is None
    assert len(network.sessions)==2 and not uploads(network)
    assert store.product_image_bindings()["G正版:no1"]["actor"]=="concurrent writer after final FORMULA response"


@pytest.mark.parametrize("growth",["duplicate_product","binding_tail","record_column"])
def test_metadata_to_formatted_grid_growth_does_not_hide_tail_records_or_extra_columns(growth):
    memory,store,network,package,_=fixture()
    changed=False
    def expand_after_metadata(call,body):
        nonlocal changed
        if call["operation"]!="metadata" or changed:return
        changed=True
        if growth=="duplicate_product":
            ws=memory.book.sheets["G正版"]
            ws.rows+=[[]]*(1000-len(ws.rows))+deepcopy(ws.rows[:6])
            memory.grid_rows["G正版"]=1006
        elif growth=="binding_tail":
            ws=memory.book.sheets[PRODUCT_IMAGE_SHEET]
            ws.rows+=[[]]*(1000-len(ws.rows))+encode_record("G正版:no1",{
                "schema":1,"identity":"G正版:no1","subject":"concurrent tail binding",
                "assets":package["products"][0]["sha256"]})
            memory.grid_rows[PRODUCT_IMAGE_SHEET]=len(ws.rows)+1
        else:
            memory.grid_cols[PRODUCT_IMAGE_SHEET]=30
            memory.book.sheets[PRODUCT_IMAGE_SHEET].rows[0]+=[""]*19+["unexpected AB header"]
    memory.after=expand_after_metadata
    if growth=="binding_tail":
        plan=prepare_batched_existing_image_repair(store,"G正版:no1")
        assert plan["binding"]["subject"]=="concurrent tail binding"
    else:
        with pytest.raises(DispatchError,match="身分重複|欄位不符"):
            prepare_batched_existing_image_repair(store,"G正版:no1")
    assert changed and not network.sessions
    formatted=next(c for c in memory.calls if c["render"]=="FORMATTED_VALUE")
    assert "'G正版'!A:L" in formatted["ranges"] and "'"+PRODUCT_IMAGE_SHEET+"'" in formatted["ranges"]


@pytest.mark.parametrize("damage",["product_rows","record_rows","product_column","record_column","shifted_start"])
def test_open_range_response_cannot_be_truncated_below_fresh_metadata_or_change_column_semantics(damage):
    memory,store,network,_,_=fixture()
    def truncated(call,body):
        if call["render"]!="FORMATTED_VALUE":return
        title=PRODUCT_IMAGE_SHEET if damage.startswith("record") else "G正版"
        value=next(v for v in body["valueRanges"] if v["range"].startswith("'"+title+"'!"))
        if damage.endswith("rows"):value["range"]=value["range"].replace("1000","999")
        elif damage=="product_column":value["range"]="'G正版'!A1:K1000"
        elif damage=="record_column":value["range"]="'"+PRODUCT_IMAGE_SHEET+"'!A1:S1000"
        else:value["range"]="'G正版'!B1:L1000"
    memory.after=truncated
    with pytest.raises(DispatchError,match="回應範圍或格式"):
        prepare_batched_existing_image_repair(store,"G正版:no1")
    assert get_count(memory)==3 and not network.sessions
