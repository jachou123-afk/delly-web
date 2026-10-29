"""Exact observed per-file 408 contract against fake transport only."""
from copy import deepcopy
import json
import pytest
from synology_image_store import NasDiagnosticError
from test_nas_original_stat import prepared, assert_read_only, calls
from test_synology_image_store import Response


def execute(file_builder, *, status=200):
    store,session,metadata,path,good=prepared()
    good["data"]["files"]=file_builder(path)
    session.override=lambda p:Response(good,status=status) if p["api"]=="SYNO.FileStation.List" else None
    return store,session,metadata


def assert_one_read_only(session):
    assert sum(v["method"]=="getinfo" for v in calls(session))==1
    assert_read_only(session)
    assert not session.objects


def test_exact_observed_per_file_408_is_missing_without_download_or_write():
    store,session,metadata=execute(lambda path:[{"path":path,"code":408}])
    with store:result=store.stat_asset(metadata)
    assert result=={"sha256":metadata["sha256"],"exists":False,"size":None,"expected_size":metadata["size"],"size_matches":False}
    assert set(result)=={"sha256","exists","size","expected_size","size_matches"}
    assert_one_read_only(session)


@pytest.mark.parametrize("extras",[
    {"isdir":False},{"isdir":True},{"size":1},{"size":None},{"additional":{}},
    {"additional":{"size":1}},{"error":{"code":408}},{"error":None},
    {"name":"private-path synthetic-secret"},{"unknown":"private-path synthetic-secret"},
    {"isdir":False,"additional":{"size":123}},
])
def test_any_extra_field_keeps_per_file_408_fail_closed(extras):
    store,session,metadata=execute(lambda path:[{"path":path,"code":408,**extras}])
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="response_shape"
    assert "private-path" not in error.value.safe_summary() and "synthetic-secret" not in error.value.safe_summary()
    assert_one_read_only(session)


@pytest.mark.parametrize("code",["408",408.0,True,False,None,0,400,401,403,407,409,410,-1,1000000,{},[]])
def test_only_real_integer_408_is_missing(code):
    store,session,metadata=execute(lambda path:[{"path":path,"code":code}])
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="response_shape"
    assert_one_read_only(session)


@pytest.mark.parametrize("path_value",["/other/private-path","/wrong/../expected",None,True,[],{}])
def test_path_must_be_exact_string_not_normalized_or_inferred(path_value):
    store,session,metadata=execute(lambda path:[{"path":path_value,"code":408}])
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="response_shape"
    assert_one_read_only(session)


@pytest.mark.parametrize("builder",[
    lambda path:[],lambda path:{"path":path,"code":408},lambda path:[{"path":path,"code":408},{"path":path,"code":408}],
    lambda path:[{"code":408}],lambda path:[{"path":path}],lambda path:[408],
    lambda path:[{"path":path,"error":{"code":408}}],
])
def test_no_ambiguous_or_nested_error_shape_is_missing(builder):
    store,session,metadata=execute(builder)
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="response_shape"
    assert_one_read_only(session)


@pytest.mark.parametrize("status",[408,502])
def test_http_error_does_not_become_missing_even_with_exact_body(status):
    store,session,metadata=execute(lambda path:[{"path":path,"code":408}],status=status)
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="http" and error.value.http_status==status
    assert_one_read_only(session)


def test_ordinary_existing_without_code_control_is_unchanged():
    store,session,metadata,_,good=prepared()
    with store:result=store.stat_asset(metadata)
    assert result["exists"] is True and result["size_matches"] is True and result["size"]==metadata["size"]
    assert_one_read_only(session)


@pytest.mark.parametrize("code",[0,400,408,409,"408",True])
def test_code_with_otherwise_valid_existing_metadata_is_error_not_exists(code):
    store,session,metadata,_,good=prepared()
    good["data"]["files"][0]["code"]=code
    session.override=lambda p:Response(good) if p["api"]=="SYNO.FileStation.List" else None
    with store:
        with pytest.raises(NasDiagnosticError) as error:store.stat_asset(metadata)
    assert error.value.kind=="response_shape"
    assert_one_read_only(session)
