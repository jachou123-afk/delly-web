"""Offline fake SDK and temporary DB only; no actual credentials or archive."""
import base64,copy,csv,hashlib,io,json,sqlite3,sys
from pathlib import Path
from zipfile import ZipFile
import pytest
from wecom_archive import download as a

PNG=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg==")

def message(native=False):
    items=[]
    for n in range(25):
        image={"sdkfileid":"image-"+str(n),"filesize":len(PNG),"md5sum":hashlib.md5(PNG).hexdigest()}
        items += [{"type":"ChatRecordImage","content":image if native else json.dumps(image),"msgtime":1792281600+n,"from_chatroom":True},
                  {"type":"ChatRecordText","content":json.dumps({"content":"original quoted text"}),"msgtime":1792281600+n,"from_chatroom":True}]
    video={"sdkfileid":"video-0","filesize":8,"md5sum":"f"*32,"play_length":1}
    items.append({"type":"ChatRecordVideo","content":video if native else json.dumps(video),"msgtime":1792454400,"from_chatroom":True})
    return {"msgid":"saved-forward","msgtype":"chatrecord","msgtime":1793232000000,"chatrecord":{"title":"forwarded original","item":items}}

@pytest.fixture
def seeded(tmp_path,monkeypatch):
    for name,value in {"BASE":tmp_path,"DATABASE":tmp_path/"messages.sqlite","MEDIA_DIR":tmp_path/"media",
                       "PRIVATE_KEY":tmp_path/"never-read-private-key.pem","CREDENTIALS":tmp_path/"never-read-credentials.json"}.items():monkeypatch.setattr(a,name,value)
    a.MEDIA_DIR.mkdir()
    msg=message();body=json.dumps(msg,ensure_ascii=False)
    with a._database() as db:
        db.execute("INSERT INTO messages VALUES(?,?,?,?)",(2355,"saved-forward",2,body))
        db.execute("INSERT INTO messages VALUES(?,?,?,?)",(1,"ordinary",2,json.dumps({"msgtype":"text","text":{"content":'{"sdkfileid":"not-media"}'}})))
        a._set_metadata(db,"last_seq",2397);a._set_metadata(db,"fresh_key_version",2)
        a._set_metadata(db,"scope_verified_at","2026-10-01T00:00:00+08:00")
        a._set_metadata(db,"fresh_key_fingerprint","fake-public-key-fingerprint")
    state={"requested":[],"constructors":0,"credentials":0,"fail":set(),"hook":None}
    class FakeSdk:
        def __init__(self,*args):state["constructors"]+=1;assert args==("fake-corp","fake-secret")
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get_chat_data(self,*args):raise AssertionError("GetChatData is forbidden in media-only")
        def media_chunks(self,identity):
            state["requested"].append(identity)
            if state["hook"]:state["hook"]()
            yield PNG[:7]
            if identity in state["fail"]:raise a.ArchiveError("fake media SDK error")
            yield PNG[7:]
    def credentials(saved):state["credentials"]+=1;assert saved is True;return "fake-corp","fake-secret"
    monkeypatch.setattr(a,"FinanceSdk",FakeSdk);monkeypatch.setattr(a,"_credentials",credentials)
    return {"msg":msg,"body":body,"state":state,"output":tmp_path/"archive.zip"}

def guard():
    with sqlite3.connect(a.DATABASE) as db:return a._archive_source_guard(db)

def dry():return a.media_only([2355])
def apply(plan):return a.media_only([2355],apply=True,expected_scope_sha256=plan["scope_sha256"])

@pytest.mark.parametrize("native",[False,True])
def test_actual_shape_51_items_26_refs_without_changing_message(native):
    value=message(native);before=copy.deepcopy(value)
    assert len(list(a._media_ids(value)))==len(set(a._media_ids(value)))==26
    assert value==before and len(value["chatrecord"]["item"])==51

def test_arbitrary_text_JSON_is_never_treated_as_sdk_reference():
    assert list(a._media_ids({"msgtype":"text","text":{"content":'{"sdkfileid":"opaque-secret"}'}}))==[]
    text={"msgtype":"chatrecord","chatrecord":{"item":[{"type":"ChatRecordText","content":{"sdkfileid":"opaque-secret"}}]}}
    assert list(a._media_ids(text))==[]

@pytest.mark.parametrize("payload",['{"sdkfileid":"opaque-secret"', '["opaque-secret"]',
                                  '{"sdkfileid":"one","sdkfileid":"opaque-secret"}',
                                  '{"sdkfileid":""}','{"sdkfileid":"\\u0000opaque-secret"}',
                                  '{"sdkfileid":"\\ud800"}', '{"sdkfileid":NaN}'])
def test_known_media_bad_JSON_ids_are_safe_fixed_errors(payload):
    value={"msgtype":"chatrecord","chatrecord":{"item":[{"type":"ChatRecordImage","content":payload}]}}
    with pytest.raises(a.ArchiveError) as exc:list(a._media_ids(value))
    assert "opaque-secret" not in str(exc.value)

@pytest.mark.parametrize("kind",["ChatRecordUnknown",[],None])
def test_unknown_forward_kind_is_not_silent_success(kind):
    with pytest.raises(a.ArchiveError):list(a._media_ids({"msgtype":"chatrecord","chatrecord":{"item":[{"type":kind,"content":"{}"}]}}))

def test_forward_bounds_reject_before_registration():
    value={"msgtype":"chatrecord","chatrecord":{"item":[{"type":"ChatRecordImage","content":"x"*(a.MAX_CHATRECORD_MEDIA_CONTENT_BYTES+1)}]}}
    with pytest.raises(a.ArchiveError):list(a._media_ids(value))
    value["chatrecord"]["item"]=[{}]*(a.MAX_CHATRECORD_ITEMS+1)
    with pytest.raises(a.ArchiveError):list(a._media_ids(value))

def test_dryrun_is_readonly_no_lock_privatekey_credentials_or_SDK(seeded):
    before=a.DATABASE.read_bytes();result=dry()
    assert result["unregistered"]==result["referenced_media"]==26
    assert result["source_guard"]["messages"]==2 and result["source_guard"]["cursor"]=="2397"
    assert a.DATABASE.read_bytes()==before and not (a.BASE/"download-writer.lock").exists()
    assert seeded["state"]["constructors"]==seeded["state"]["credentials"]==0
    assert not a.PRIVATE_KEY.exists()
    assert "sdkfileid" not in json.dumps(result) and "image-0" not in json.dumps(result)

def test_apply_all26_preserves_messages_metadata_and_uses_only_media(seeded):
    before=guard();plan=dry();result=apply(plan)
    assert result["complete"] and result["new_media_registered"]==result["downloaded"]==26 and result["download_failed"]==0
    assert guard()==before and len(set(seeded["state"]["requested"]))==26 and "not-media" not in seeded["state"]["requested"]
    assert seeded["state"]["constructors"]==seeded["state"]["credentials"]==1 and not a.PRIVATE_KEY.exists()
    with sqlite3.connect(a.DATABASE) as db:
        assert db.execute("SELECT body FROM messages WHERE seq=2355").fetchone()[0]==seeded["body"]
        assert db.execute("SELECT COUNT(*) FROM media WHERE ready=1").fetchone()[0]==26
        for identity,filename in db.execute("SELECT sdkfileid,filename FROM media"):
            assert filename==hashlib.sha256(identity.encode()).hexdigest()+".bin"
            assert (a.MEDIA_DIR/filename).read_bytes()==PNG

def test_stale_scope_or_missing_seq_refuses_before_any_registration(seeded):
    before=guard();plan=dry()
    with sqlite3.connect(a.DATABASE) as db:db.execute("UPDATE metadata SET value='2398' WHERE name='last_seq'")
    with pytest.raises(a.ArchiveError,match="dry-run 後變更"):apply(plan)
    with sqlite3.connect(a.DATABASE) as db:assert db.execute("SELECT COUNT(*) FROM media").fetchone()[0]==0
    assert not seeded["state"]["requested"] and seeded["state"]["credentials"]==0
    with pytest.raises(a.ArchiveError):a.media_only([99999])

def test_idempotent_after_new_dryrun_never_redownloads_or_reads_credentials(seeded):
    apply(dry());before=guard();requested=list(seeded["state"]["requested"]);constructors=seeded["state"]["constructors"]
    result=apply(dry())
    assert result["new_media_registered"]==result["downloaded"]==0 and result["complete"]
    assert guard()==before and seeded["state"]["requested"]==requested and seeded["state"]["constructors"]==constructors

def test_existing_ready_filename_and_original_bytes_remain_untouched(seeded):
    old="original-kept.bin";(a.MEDIA_DIR/old).write_bytes(PNG)
    with sqlite3.connect(a.DATABASE) as db:db.execute("INSERT INTO media VALUES(?,?,1)",("image-0",old))
    result=apply(dry())
    assert result["downloaded"]==result["new_media_registered"]==25 and "image-0" not in seeded["state"]["requested"]
    assert (a.MEDIA_DIR/old).read_bytes()==PNG
    with sqlite3.connect(a.DATABASE) as db:assert db.execute("SELECT filename,ready FROM media WHERE sdkfileid='image-0'").fetchone()==(old,1)

def test_existing_ready_missing_file_does_not_reset_ready_or_start_SDK(seeded):
    with sqlite3.connect(a.DATABASE) as db:db.execute("INSERT INTO media VALUES(?,?,1)",("image-0","lost.bin"))
    plan=dry();assert plan["ready_files_missing"]==1
    with pytest.raises(a.ArchiveError,match="不自動重置"):apply(plan)
    assert seeded["state"]["constructors"]==0
    with sqlite3.connect(a.DATABASE) as db:assert db.execute("SELECT ready FROM media WHERE sdkfileid='image-0'").fetchone()[0]==1

def test_partial_failure_is_resumable_only_failed_media_retried(seeded):
    seeded["state"]["fail"].add("image-1");before=guard();first=apply(dry())
    assert not first["complete"] and first["downloaded"]==25 and first["download_failed"]==1 and guard()==before
    assert not list(a.MEDIA_DIR.glob("*.part"))
    first_calls=list(seeded["state"]["requested"]);seeded["state"]["fail"].clear();second=apply(dry())
    assert second["complete"] and second["new_media_registered"]==0 and second["downloaded"]==1
    assert seeded["state"]["requested"][len(first_calls):]==["image-1"] and guard()==before

def test_archive_source_guard_detects_other_writer_without_overwriting_it(seeded):
    changed=False
    def writer():
        nonlocal changed
        if changed:return
        changed=True
        with sqlite3.connect(a.DATABASE) as db:db.execute("UPDATE metadata SET value='external' WHERE name='last_seq'")
    seeded["state"]["hook"]=writer
    with pytest.raises(a.ArchiveError,match="水位變更"):apply(dry())
    with sqlite3.connect(a.DATABASE) as db:assert db.execute("SELECT value FROM metadata WHERE name='last_seq'").fetchone()[0]=="external"

def test_sync_and_media_only_share_writer_lock_no_automatic_steal(seeded):
    lock=a.BASE/"download-writer.lock";lock.write_bytes(b"another-live-writer");a.PRIVATE_KEY.write_bytes(b"unused")
    plan=dry()
    with pytest.raises(a.ArchiveError,match="寫入鎖"):apply(plan)
    with pytest.raises(a.ArchiveError,match="寫入鎖"):a.sync(100,1,saved_credentials=True)
    assert lock.read_bytes()==b"another-live-writer" and seeded["state"]["constructors"]==0

def test_export_unregistered_refs_preserves_previous_zip_and_cache_not_latest(seeded):
    seeded["output"].write_bytes(b"previous ZIP unchanged");before=seeded["output"].read_bytes()
    with pytest.raises(a.ArchiveError,match="尚未登記"):a.export(seeded["output"])
    with pytest.raises(a.ArchiveError,match="尚未登記"):a._archive_current(seeded["output"])
    assert seeded["output"].read_bytes()==before

def test_export_after_repair_maps26_preserves_rawJSON_messages_and_bytes(seeded):
    before=guard();apply(dry());a.export(seeded["output"])
    with ZipFile(seeded["output"]) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read("manifest.json"));assert manifest["export_format"]==3 and manifest["messages"]==2 and manifest["media_files"]==26
        content=list(csv.reader(io.StringIO(archive.read("messages.csv").decode("utf-8-sig"))))
        row=next(row for row in content[1:] if row[0]=="2355")
        assert row[7]==seeded["body"] and len(row[-1].splitlines())==26
        assert all(archive.read(name)==PNG for name in row[-1].splitlines())
        original=[json.loads(line) for line in archive.read("messages.jsonl").decode().splitlines()]
        assert next(m["message"] for m in original if m["seq"]==2355)==seeded["msg"]
    assert guard()==before and a._archive_current(seeded["output"])

def test_media_only_CLI_defaults_dry_and_partial_returns_failure(seeded,monkeypatch,capsys):
    monkeypatch.setattr(sys,"argv",["download.py","media-only","--seq","2355"])
    assert a.main()==0 and json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not seeded["state"]["requested"]
    seeded["state"]["fail"].add("image-1");plan=dry()
    monkeypatch.setattr(sys,"argv",["download.py","media-only","--seq","2355","--apply","--expected-scope-sha256",plan["scope_sha256"]])
    assert a.main()==1 and json.loads(capsys.readouterr().out)["complete"] is False
