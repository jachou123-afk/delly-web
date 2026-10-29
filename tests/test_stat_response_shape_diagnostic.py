"""Fixed diagnostics only; fake transport, no NAS or writes."""
from dataclasses import asdict
import json

import pytest

from synology_image_store import (NasDiagnosticError, NasStatResponseShape,
                                  _stat_response_shape, _json_shape_type)
from test_nas_original_stat import prepared, assert_read_only, calls
from test_synology_image_store import Response


SENSITIVE = ("private-path", "synthetic-secret", "account-name", "remote-key", "sid-value")


def diagnose(mutator):
    store, session, metadata, path, good = prepared()
    mutator(good["data"], path)
    session.override = lambda p: Response(good) if p["api"] == "SYNO.FileStation.List" else None
    with store:
        with pytest.raises(NasDiagnosticError) as observed:
            store.stat_asset(metadata)
        error = observed.value
    assert error.kind == "response_shape"
    assert type(error.stat_shape) is NasStatResponseShape
    assert sum(item["method"] == "getinfo" for item in calls(session)) == 1
    assert_read_only(session)
    for marker in SENSITIVE:
        assert marker not in error.safe_summary()
        assert marker not in json.dumps(asdict(error.stat_shape))
    return error


def test_nested_408_only_reports_shape_and_numeric_codes_never_missing():
    def mutate(data, path):
        data["files"] = [{"path": path, "isdir": False, "additional": {}, "code": 408,
                          "error": {"code": 408, "message": "private-path synthetic-secret"},
                          "name": "account-name", "remote-key": "sid-value"}]
    error = diagnose(mutate)
    assert asdict(error.stat_shape) == {
        "data_type": "object", "files_type": "array", "files_cardinality": "one",
        "file_type": "object", "path_type": "string", "path_exact_match": True,
        "isdir_type": "bool", "isdir_value": False, "additional_type": "object",
        "size_type": "missing", "file_code": 408, "file_error_type": "object", "file_error_code": 408}
    assert "file.code=408" in error.safe_summary()
    assert "file.error.code=408" in error.safe_summary()
    assert "path_exact=true" in error.safe_summary()


@pytest.mark.parametrize("value,kind", [
    (None, "null"), (True, "bool"), (0, "int"), (-1, "int"),
    (1.0, "float"), ("private-path synthetic-secret", "string"),
    (["account-name"], "array"), ({"remote-key": "sid-value"}, "object"),
])
def test_size_only_reports_type_and_never_value(value, kind):
    error = diagnose(lambda data, path: data["files"][0]["additional"].update(size=value))
    assert error.stat_shape.size_type == kind
    assert "size=" + kind in error.safe_summary()


@pytest.mark.parametrize("mutate,expected", [
    (lambda d, p: d.pop("files"), ("missing", "not_array", "unavailable")),
    (lambda d, p: d.update(files=None), ("null", "not_array", "unavailable")),
    (lambda d, p: d.update(files={"private-path": "synthetic-secret"}), ("object", "not_array", "unavailable")),
    (lambda d, p: d.update(files=[]), ("array", "empty", "unavailable")),
    (lambda d, p: d.update(files=[{}, {"name": "account-name"}]), ("array", "many", "unavailable")),
    (lambda d, p: d.update(files=["synthetic-secret"]), ("array", "one", "string")),
])
def test_only_exact_single_dict_file_is_inspected(mutate, expected):
    shape = diagnose(mutate).stat_shape
    assert (shape.files_type, shape.files_cardinality, shape.file_type) == expected
    assert shape.path_exact_match is None and shape.file_code is None and shape.file_error_code is None


@pytest.mark.parametrize("value,kind,boolean", [
    (True, "bool", True), (0, "int", None), ("synthetic-secret", "string", None),
    (None, "null", None), (["private-path"], "array", None),
])
def test_isdir_exposes_only_boolean_or_type(value, kind, boolean):
    error = diagnose(lambda data, path: data["files"][0].update(isdir=value))
    assert error.stat_shape.isdir_type == kind and error.stat_shape.isdir_value is boolean


def test_path_only_exact_match_never_exposes_path_and_missing_is_distinct():
    error = diagnose(lambda data, path: data["files"][0].update(path="/private-path/synthetic-secret"))
    assert error.stat_shape.path_type == "string" and error.stat_shape.path_exact_match is False
    error = diagnose(lambda data, path: data["files"][0].pop("path"))
    assert error.stat_shape.path_type == "missing" and error.stat_shape.path_exact_match is None


@pytest.mark.parametrize("value", [-1, 1000000, True, "408", 408.0, {"remote-key": "sid-value"}, [408]])
def test_file_codes_are_bounded_real_integers_only(value):
    def mutate(data, path):
        data["files"] = [{"error": {"code": value, "message": "synthetic-secret"}, "code": value}]
    error = diagnose(mutate)
    assert error.stat_shape.file_code is None and error.stat_shape.file_error_code is None
    assert "file.code=" not in error.safe_summary() and "file.error.code=" not in error.safe_summary()


@pytest.mark.parametrize("value", [0, 408, 999999])
def test_file_code_safe_numeric_boundaries(value):
    error = diagnose(lambda data, path: data.update(files=[{"code": value, "error": {"code": value}}]))
    assert error.stat_shape.file_code == value and error.stat_shape.file_error_code == value


def test_typed_container_cannot_inject_remote_text_or_unbounded_codes():
    shape = NasStatResponseShape(data_type="synthetic-secret", files_type=["private-path"],
        files_cardinality="account-name", file_type="remote-key", path_type="sid-value",
        path_exact_match="synthetic-secret", isdir_type="private-path", isdir_value=1,
        additional_type={"remote-key": "synthetic-secret"}, size_type="account-name",
        file_code=True, file_error_type="sid-value", file_error_code=1000000)
    error = NasDiagnosticError("private-path synthetic-secret", kind="response_shape", stat_shape=shape)
    assert all(value is None or value in ("unavailable", "not_array") for value in asdict(shape).values())
    assert not any(marker in error.safe_summary() for marker in SENSITIVE)
    assert len(error.safe_summary()) < 500
    assert NasDiagnosticError("secret", kind="http", http_status=502, stat_shape=shape).stat_shape is None
    assert NasDiagnosticError("secret", kind="response_shape", stat_shape={"remote-key": "sid-value"}).stat_shape is None


def test_fixed_field_extraction_ignores_extra_remote_keys_even_if_fields_present():
    error = diagnose(lambda data, path: data.update(files=[{
        "path": path, "isdir": False, "additional": {"size": "synthetic-secret", "remote-key": "sid-value"},
        "name": "account-name", "remote-key": "private-path", "error": {"message": "synthetic-secret"}}]))
    assert set(asdict(error.stat_shape)) == {
        "data_type", "files_type", "files_cardinality", "file_type", "path_type", "path_exact_match",
        "isdir_type", "isdir_value", "additional_type", "size_type", "file_code", "file_error_type", "file_error_code"}
    assert error.stat_shape.file_error_type == "object" and error.stat_shape.file_error_code is None


def test_formal_ui_error_adapter_receives_safe_shape_without_ui_patch():
    from quote_image_inspection import inspection_error
    error = diagnose(lambda data, path: data.update(files=[{"error": {"code": 408}}]))
    assert inspection_error(error) == error.safe_summary()
    assert "file.error.code=408" in inspection_error(error)


def test_json_type_labels_do_not_reflect_arbitrary_class_names():
    class synthetic_secret:
        pass
    assert _json_shape_type(synthetic_secret()) == "other"
    assert _stat_response_shape({"files": [synthetic_secret()]}, "/private-path").file_type == "other"
