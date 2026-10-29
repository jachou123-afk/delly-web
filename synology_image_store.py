"""Opt-in, content-addressed NAS originals. No sharing, deletes or permission APIs.

This adapter is not enabled by merely importing it. Credentials belong in a
server-side secret store, never in product rows, browser URLs or this module.
"""
from dataclasses import dataclass, field
from http.client import RemoteDisconnected
import hashlib
import ipaddress
import json
import math
import re
import socket
import ssl
from time import monotonic
from urllib.parse import urlsplit

import requests
from urllib3 import exceptions as urllib3_errors

from dispatch_manager import DispatchError


MAX_JSON_BYTES = 64 * 1024
TIMEOUT = (8, 30)
UPLOAD_TIMEOUT = (60, 60)
API_VERSIONS = {
    "SYNO.API.Auth": 3,
    "SYNO.FileStation.List": 2,
    "SYNO.FileStation.Upload": 2,
    "SYNO.FileStation.Download": 2,
}
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
NAS_REDIRECT_ERROR = "NAS 入口已轉向；請重新確認網址，未跟隨或向轉向網址傳送帳密"


def safe_https_origin(value):
    """Allowlisted diagnostic origin only; never echo URL paths or credentials."""
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(not 33 <= ord(c) <= 126 for c in value)
            or "\\" in value):
        return ""
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or port == 0 or "%" in host):
            return ""
        if ":" in host:
            authority = "[" + str(ipaddress.IPv6Address(host)) + "]"
            raw_authority = "[" + host + "]"
        else:
            if (len(host) > 253 or not all(re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in host.split("."))):
                return ""
            if re.fullmatch(r"[0-9.]+", host):
                ipaddress.IPv4Address(host)
            authority = host
            raw_authority = host
        port_suffix = f":{port}" if port is not None else ""
        if url.netloc.lower() != raw_authority + port_suffix:
            return ""
        return "https://" + authority + port_suffix
    except (ValueError, UnicodeError):
        return ""


_SHAPE_MISSING = object()
_SHAPE_TYPES = frozenset({"missing", "null", "bool", "int", "float", "string",
                          "array", "object", "other", "unavailable"})


def _json_shape_type(value):
    """Return fixed JSON type labels only; never a class name or remote value."""
    if value is _SHAPE_MISSING:
        return "missing"
    return {type(None): "null", bool: "bool", int: "int", float: "float",
            str: "string", list: "array", dict: "object"}.get(type(value), "other")


@dataclass(frozen=True)
class NasStatResponseShape:
    """Value-free, fixed-field diagnostics for one exact-path getinfo failure."""
    data_type: str = "unavailable"
    files_type: str = "unavailable"
    files_cardinality: str = "not_array"
    file_type: str = "unavailable"
    path_type: str = "unavailable"
    path_exact_match: bool = None
    isdir_type: str = "unavailable"
    isdir_value: bool = None
    additional_type: str = "unavailable"
    size_type: str = "unavailable"
    file_code: int = None
    file_error_type: str = "unavailable"
    file_error_code: int = None

    def __post_init__(self):
        # Construction is also fail-closed: callers cannot inject text into a
        # diagnostic by instantiating this typed container directly.
        for name in ("data_type", "files_type", "file_type", "path_type",
                     "isdir_type", "additional_type", "size_type", "file_error_type"):
            value = getattr(self, name)
            if type(value) is not str or value not in _SHAPE_TYPES:
                object.__setattr__(self, name, "unavailable")
        if (type(self.files_cardinality) is not str or self.files_cardinality not in
                {"not_array", "empty", "one", "many"}):
            object.__setattr__(self, "files_cardinality", "not_array")
        for name in ("path_exact_match", "isdir_value"):
            if type(getattr(self, name)) is not bool:
                object.__setattr__(self, name, None)
        for name in ("file_code", "file_error_code"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 999999:
                object.__setattr__(self, name, None)

    def safe_summary(self):
        fields = [f"data={self.data_type}",
                  f"files={self.files_type}/{self.files_cardinality}",
                  f"file={self.file_type}", f"path={self.path_type}"]
        if self.path_exact_match is not None:
            fields.append("path_exact=" + ("true" if self.path_exact_match else "false"))
        fields.append(f"isdir={self.isdir_type}")
        if self.isdir_value is not None:
            fields.append("isdir_value=" + ("true" if self.isdir_value else "false"))
        fields.extend((f"additional={self.additional_type}", f"size={self.size_type}",
                       f"file.error={self.file_error_type}"))
        if self.file_code is not None:
            fields.append(f"file.code={self.file_code}")
        if self.file_error_code is not None:
            fields.append(f"file.error.code={self.file_error_code}")
        return "stat shape：" + ", ".join(fields)


def _stat_response_shape(data, expected_path):
    """Observe only fixed fields; no iteration over remote keys or file values."""
    files = data.get("files", _SHAPE_MISSING) if type(data) is dict else _SHAPE_MISSING
    card = ("empty" if not files else "one" if len(files) == 1 else "many") if type(files) is list else "not_array"
    fields = {"data_type": _json_shape_type(data), "files_type": _json_shape_type(files),
              "files_cardinality": card}
    if type(files) is not list or len(files) != 1:
        return NasStatResponseShape(**fields)
    file = files[0]
    fields["file_type"] = _json_shape_type(file)
    if type(file) is not dict:
        return NasStatResponseShape(**fields)
    path, isdir, additional, error = (file.get(name, _SHAPE_MISSING)
                                     for name in ("path", "isdir", "additional", "error"))
    fields.update(path_type=_json_shape_type(path),
                  path_exact_match=(path == expected_path) if type(path) is str else None,
                  isdir_type=_json_shape_type(isdir), isdir_value=isdir,
                  additional_type=_json_shape_type(additional),
                  size_type=_json_shape_type(additional.get("size", _SHAPE_MISSING)) if type(additional) is dict else "unavailable",
                  file_code=file.get("code"), file_error_type=_json_shape_type(error),
                  file_error_code=error.get("code") if type(error) is dict else None)
    return NasStatResponseShape(**fields)


def safe_transport_cause(exc):
    """Fixed native categories only; never stringify errors or follow payloads."""
    categories = (
        (RemoteDisconnected, "remote_disconnected"),
        (TimeoutError, "socket_timeout"),
        (ConnectionResetError, "connection_reset"),
        (ConnectionAbortedError, "connection_aborted"),
        (BrokenPipeError, "broken_pipe"),
        (ConnectionRefusedError, "connection_error"),
        (socket.gaierror, "dns_error"),
        (ssl.SSLError, "tls_error"),
        (urllib3_errors.ReadTimeoutError, "read_timeout"),
        (getattr(urllib3_errors, "NameResolutionError", None), "dns_error"),
        (urllib3_errors.NewConnectionError, "connection_error"),
        (urllib3_errors.ConnectTimeoutError, "connect_timeout"),
        (urllib3_errors.SSLError, "tls_error"),
        (urllib3_errors.ProtocolError, "protocol_error"),
        (requests.exceptions.ConnectTimeout, "connect_timeout"),
        (requests.exceptions.ReadTimeout, "read_timeout"),
        (requests.exceptions.SSLError, "tls_error"),
        (requests.exceptions.Timeout, "timeout"),
        (requests.exceptions.ConnectionError, "connection_error"),
        (requests.exceptions.RequestException, "request_error"),
    )
    pending, seen, best = [exc], set(), (len(categories), "unknown")
    def exception_mro(value):
        mro = type.__dict__["__mro__"].__get__(type(value))
        return mro if any(base is BaseException for base in mro) else ()
    while pending and len(seen) < 16:
        current = pending.pop(0)
        mro = exception_mro(current)
        if not mro or id(current) in seen:
            continue
        seen.add(id(current))
        # Class identity/MRO is inspected without __class__, user attributes or
        # exception __str__/__repr__. Wrapped ProtocolError args may carry the
        # native socket error; unrelated strings/containers are never visited.
        for rank, (known, label) in enumerate(categories):
            if any(base is known for base in mro) and rank < best[0]:
                best = (rank, label)
                break
        for field in ("__cause__", "__context__"):
            child = BaseException.__dict__[field].__get__(current)
            if exception_mro(child):
                pending.append(child)
        args = BaseException.__dict__["args"].__get__(current)
        if type(args) is tuple:
            pending.extend(child for child in args[:16] if exception_mro(child))
    return best[1]


def _transport_elapsed_ms(started):
    elapsed = monotonic() - started
    if (type(elapsed) not in (int, float)
            or type(elapsed) is float and not math.isfinite(elapsed)):
        return None
    elapsed = max(0, min(NasDiagnosticError.MAX_ELAPSED_MS / 1000, elapsed))
    return int(elapsed * 1000)


class NasDiagnosticError(DispatchError):
    """Only allowlisted categories and bounded numeric codes may reach upload UI."""
    LABELS = {
        "redirect": "入口轉向", "http": "HTTP 回應失敗",
        "connect_timeout": "連線逾時", "read_timeout": "等待回應／讀取逾時",
        "timeout": "逾時（未能細分階段）", "tls": "TLS／憑證驗證失敗",
        "connection": "連線中斷／失敗", "request": "網路請求失敗",
        "response_length": "回應長度不合法", "response_size": "回應超過大小限制",
        "invalid_json": "回應不是有效 JSON", "api": "NAS API 回報失敗",
        "response_shape": "API 回應結構不完整",
    }
    MAX_ELAPSED_MS = 3_600_000
    TRANSPORT_PHASE_LABELS = {"request_or_headers": "送出請求／等待標頭",
                              "response_body": "讀取回應內容"}
    TRANSPORT_CAUSE_LABELS = {
        "socket_timeout": "底層 socket 逾時", "connection_reset": "連線被重設",
        "connection_aborted": "連線被中止", "broken_pipe": "傳送管線中斷",
        "remote_disconnected": "遠端關閉連線", "dns_error": "DNS 解析失敗",
        "tls_error": "底層 TLS 失敗", "protocol_error": "底層傳輸協定錯誤",
        "connect_timeout": "底層連線逾時", "read_timeout": "底層讀取逾時",
        "connection_error": "連線錯誤", "timeout": "未細分逾時",
        "request_error": "請求錯誤", "unknown": "底層原因未分類",
    }

    def __init__(self, message, *, kind, http_status=None, api_code=None,
                 source_origin="", redirect_origin="", stat_shape=None,
                 transport_phase=None, elapsed_ms=None, transport_cause=None):
        super().__init__(message)
        self.kind = kind if kind in self.LABELS else "unknown"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.api_code = api_code if type(api_code) is int and 0 <= api_code <= 999999 else None
        self.source_origin = safe_https_origin(source_origin)
        self.redirect_origin = safe_https_origin(redirect_origin) if self.kind == "redirect" else ""
        self.stat_shape = stat_shape if self.kind == "response_shape" and type(stat_shape) is NasStatResponseShape else None
        self.transport_phase = (transport_phase if type(transport_phase) is str
                                and transport_phase in self.TRANSPORT_PHASE_LABELS else None)
        self.elapsed_ms = (elapsed_ms if type(elapsed_ms) is int
                           and 0 <= elapsed_ms <= self.MAX_ELAPSED_MS else None)
        self.transport_cause = (transport_cause if type(transport_cause) is str
                                and transport_cause in self.TRANSPORT_CAUSE_LABELS else None)

    def safe_summary(self):
        fields = [self.LABELS.get(self.kind, "未分類錯誤")]
        if self.http_status is not None:
            fields.append(f"HTTP {self.http_status}")
        if self.api_code is not None:
            fields.append(f"API 代碼 {self.api_code}")
        if self.stat_shape is not None:
            fields.append(self.stat_shape.safe_summary())
        if self.transport_phase is not None:
            fields.append("階段：" + self.TRANSPORT_PHASE_LABELS[self.transport_phase])
        if self.elapsed_ms is not None:
            fields.append(f"耗時 {self.elapsed_ms} ms")
        if self.transport_cause is not None:
            fields.append(self.TRANSPORT_CAUSE_LABELS[self.transport_cause])
        return "；".join(fields)


@dataclass(frozen=True)
class NasConfig:
    base_url: str
    root: str
    library_id: str
    account: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.base_url, str):
            raise DispatchError("NAS 連線網址格式不合法")
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError:
            raise DispatchError("NAS 連線網址格式不合法") from None
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or url.path not in ("", "/") or port == 0
                or any(c.isspace() or ord(c) < 32 for c in self.base_url)
                or "\\" in self.base_url):
            raise DispatchError("NAS 只接受不含帳密、查詢參數的 HTTPS 主機網址")
        # Root is an explicit dedicated shared folder, never a broad home/root.
        if (not isinstance(self.root, str) or not re.fullmatch(r"/[^/\\\x00-\x1f]+", self.root)
                or self.root[1:].strip() != self.root[1:]
                or self.root[1:].casefold() in {".", "..", "home", "homes", "圖片區"}
                or "%" in self.root):
            raise DispatchError("請指定獨立圖庫共用資料夾，不可使用根目錄、home 或整個圖片區")
        if not isinstance(self.library_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", self.library_id):
            raise DispatchError("請設定固定且唯一的圖庫識別碼（8～80 個英數字、底線或連字號）")
        if (not isinstance(self.account, str) or not self.account.strip()
                or not isinstance(self.password, str) or not self.password):
            raise DispatchError("NAS 專用帳號／密碼尚未設定")
        if self.account.casefold() in {"admin", "root"}:
            raise DispatchError("請使用僅能存取圖庫的專用帳號，不使用管理員帳號")


class SynologyImageStore:
    """Explicit sessions and bounded responses; no automatic login retries."""

    def __init__(self, config, session_factory=requests.Session):
        self.config = config
        self._session_factory = session_factory
        self._session = None
        self._apis = None
        self._sid = None
        self._root_checked = False

    def __enter__(self):
        if self._session is not None:
            raise DispatchError("NAS 連線工作階段不可重複開啟")
        self._root_checked = False
        self._session = self._session_factory()
        # Prevent implicit .netrc credentials and ambient proxies from receiving
        # the dedicated account. TLS verification remains mandatory on all calls.
        self._session.trust_env = False
        try:
            self._discover()
            login = self._json_call("SYNO.API.Auth", "login", {
                "account": self.config.account, "passwd": self.config.password,
                "session": "FileStation", "format": "sid",
            }, authenticated=False)
            sid = login.get("sid")
            if not isinstance(sid, str) or not sid or len(sid) > 4096:
                raise DispatchError("NAS 登入回應不完整；未開始讀寫圖片")
            self._sid = sid
            return self
        except Exception:
            self._session.close()
            self._session = self._sid = self._apis = None
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self._sid:
                # Log out this SID only, not other users' browser sessions.
                self._json_call("SYNO.API.Auth", "logout", {"session": "FileStation"})
        except DispatchError:
            # A failed logout must not obscure verified bytes or the real error.
            pass
        finally:
            if self._session is not None:
                self._session.close()
            self._session = self._sid = self._apis = None

    def _request(self, path, data, *, files=None, limit=MAX_JSON_BYTES):
        if self._session is None:
            raise DispatchError("請先開啟 NAS 專用連線工作階段")
        # A discovery response is not authority to redirect credentials elsewhere.
        if not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.cgi", path):
            raise DispatchError("NAS 回傳不安全的 API 路徑，已停止連線")
        response = None
        phase, started = "request_or_headers", monotonic()
        def failure(message, **details):
            return NasDiagnosticError(message, transport_phase=phase,
                                      elapsed_ms=_transport_elapsed_ms(started), **details)
        try:
            response = self._session.post(
                self.config.base_url.rstrip("/") + "/webapi/" + path,
                data=data, files=files, timeout=UPLOAD_TIMEOUT if files is not None else TIMEOUT,
                allow_redirects=False,
                verify=True, stream=True,
            )
            if 300 <= response.status_code < 400:
                raise failure(NAS_REDIRECT_ERROR, kind="redirect", http_status=response.status_code,
                                         source_origin=self.config.base_url,
                                         redirect_origin=response.headers.get("Location"))
            if response.status_code != 200:
                raise failure(f"NAS 讀寫失敗（HTTP {response.status_code}）；未視為缺圖",
                                         kind="http", http_status=response.status_code)
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError:
                    raise failure("NAS 回應長度不合法", kind="response_length") from None
                if length < 0 or length > limit:
                    raise failure("NAS 回應超過允許大小，已停止讀取", kind="response_size")
            chunks, size = [], 0
            phase = "response_body"
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise failure("NAS 回應超過允許大小，已停止讀取", kind="response_size")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", "").lower()
        except requests.RequestException as exc:
            # Requests exceptions can contain URLs, proxy details or credentials.
            if isinstance(exc, requests.exceptions.ConnectTimeout):
                kind = "connect_timeout"
            elif isinstance(exc, requests.exceptions.ReadTimeout):
                kind = "read_timeout"
            elif isinstance(exc, requests.exceptions.Timeout):
                kind = "timeout"
            elif isinstance(exc, requests.exceptions.SSLError):
                kind = "tls"
            elif isinstance(exc, requests.exceptions.ConnectionError):
                kind = "connection"
            else:
                kind = "request"
            label = NasDiagnosticError.LABELS[kind]
            raise failure(f"NAS {label}；請確認連線，勿關閉憑證驗證", kind=kind,
                          transport_cause=safe_transport_cause(exc)) from None
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _decode(raw):
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise NasDiagnosticError("NAS 未回傳有效 API 資料，可能為登入或轉接頁", kind="invalid_json") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            # Never echo remote error bodies, which may include private paths.
            code = result.get("error", {}).get("code") if isinstance(result, dict) and isinstance(result.get("error"), dict) else None
            safe_code = code if type(code) is int and 0 <= code <= 999999 else None
            raise NasDiagnosticError(f"NAS API 未完成（錯誤碼 {safe_code if safe_code is not None else '未知'}）；未重試登入或寫入",
                                     kind="api", api_code=safe_code)
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise NasDiagnosticError("NAS API 回應結構不完整", kind="response_shape")
        return data

    def _discover(self):
        raw, _ = self._request("query.cgi", {
            "api": "SYNO.API.Info", "version": "1", "method": "query",
            "query": ",".join(API_VERSIONS),
        })
        apis = self._decode(raw)
        for name, version in API_VERSIONS.items():
            info = apis.get(name, {})
            if (not isinstance(info, dict)
                    or not isinstance(info.get("minVersion"), int)
                    or not isinstance(info.get("maxVersion"), int)
                    or not info["minVersion"] <= version <= info["maxVersion"]
                    or not isinstance(info.get("path"), str)
                    or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.cgi", info["path"])):
                raise DispatchError("NAS 缺少必要檔案 API 或回應路徑不安全；未傳送登入資料")
        self._apis = apis

    def _parameters(self, api, method, values, *, authenticated=True, multipart=False):
        if self._apis is None:
            raise DispatchError("NAS API 尚未確認")
        params = {"api": api, "version": str(API_VERSIONS[api]), "method": method}
        # Auth fields and multipart upload fields use ordinary form values. Some
        # newer File Station endpoints require JSON-encoded individual values.
        json_values = self._apis[api].get("requestFormat") == "JSON" and not multipart and api != "SYNO.API.Auth"
        params.update({k: json.dumps(v, ensure_ascii=False) if json_values or isinstance(v, (list, bool)) else str(v)
                       for k, v in values.items()})
        if authenticated:
            if not self._sid:
                raise DispatchError("NAS 專用工作階段尚未登入")
            params["_sid"] = self._sid
        return params

    def _json_call(self, api, method, values, *, authenticated=True, files=None):
        params = self._parameters(api, method, values, authenticated=authenticated, multipart=files is not None)
        raw, _ = self._request(self._apis[api]["path"], params, files=files)
        return self._decode(raw)

    def check_root(self):
        """Read-only check of the exact configured folder; never list all shares."""
        result = self._json_call("SYNO.FileStation.List", "getinfo", {"path": [self.config.root], "additional": ["perm"]})
        files = result.get("files")
        if (not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict)
                or files[0].get("path") != self.config.root or files[0].get("isdir") is not True):
            raise DispatchError("找不到指定 NAS 圖庫資料夾或沒有存取權限")
        self._root_checked = True
        return {"library_id": self.config.library_id, "root": self.config.root, "readable": True}

    def _metadata(self, asset):
        from dispatch_storage import asset_bytes
        return {"schema": 1, "storage": "synology", "library_id": self.config.library_id,
                "root": self.config.root, "sha256": asset["sha256"], "name": asset["name"],
                "mime": asset["mime"], "size": len(asset_bytes(asset))}

    def _path(self, metadata, *, thumbnail=False):
        from dispatch_storage import MAX_IMAGE_BYTES
        if (not isinstance(metadata, dict) or metadata.get("schema") != 1
                or metadata.get("storage") != "synology"
                or metadata.get("library_id") != self.config.library_id
                or metadata.get("root") != self.config.root
                or not isinstance(metadata.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
                or not isinstance(metadata.get("mime"), str) or metadata["mime"] not in EXTENSIONS
                or not isinstance(metadata.get("name"), str)
                or type(metadata.get("size")) is not int
                or not 0 < metadata["size"] <= MAX_IMAGE_BYTES):
            raise DispatchError("NAS 圖片索引不符或指向不同圖庫，停止讀寫")
        digest = metadata["sha256"]
        folder = "originals"
        if thumbnail:
            from image_thumbnails import THUMB_BYTES, THUMB_VERSION
            if metadata["mime"] != "image/webp" or metadata["size"] > THUMB_BYTES:
                raise DispatchError("NAS 縮圖索引格式或大小不符")
            folder = f"thumbnails/v{THUMB_VERSION}"
        return f"{self.config.root}/{folder}/{digest[:2]}/{digest}.{EXTENSIONS[metadata['mime']]}"

    def get_asset(self, metadata):
        return self._read_asset(metadata, self._path(metadata))

    def stat_asset(self, metadata):
        """Read one exact original's metadata; never downloads, uploads or retries.

        Only File Station API 408 is a missing file. HTTP 408, permission,
        redirects and malformed responses remain errors, never upload advice.
        Size agreement does not establish byte/SHA verification or completion.
        """
        path = self._path(metadata)
        receipt = {"sha256": metadata["sha256"], "exists": False, "size": None,
                   "expected_size": metadata["size"], "size_matches": False}
        try:
            result = self._json_call("SYNO.FileStation.List", "getinfo",
                                     {"path": [path], "additional": ["size"]})
        except NasDiagnosticError as exc:
            if exc.kind == "api" and exc.api_code == 408:
                return receipt
            raise
        files = result.get("files")
        # getinfo may return one per-file error inside an otherwise successful
        # response. Only the exact observed no-such-file shape is missing.
        if (type(files) is list and len(files) == 1 and type(files[0]) is dict
                and set(files[0]) == {"path", "code"}
                and type(files[0]["path"]) is str and files[0]["path"] == path
                and type(files[0]["code"]) is int and files[0]["code"] == 408):
            return receipt
        if (not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict)
                or "code" in files[0]
                or files[0].get("path") != path or files[0].get("isdir") is not False
                or not isinstance(files[0].get("additional"), dict)
                or type(files[0]["additional"].get("size")) is not int
                or files[0]["additional"]["size"] <= 0):
            raise NasDiagnosticError("NAS 原圖檔案資訊不完整；未當成缺圖或驗證完成",
                                     kind="response_shape", stat_shape=_stat_response_shape(result, path))
        size = files[0]["additional"]["size"]
        return {**receipt, "exists": True, "size": size,
                "size_matches": size == receipt["expected_size"]}

    def get_thumbnail(self, metadata):
        from image_thumbnails import check_thumbnail
        return check_thumbnail(self._read_asset(metadata, self._path(metadata, thumbnail=True)))

    def _read_asset(self, metadata, path):
        from dispatch_storage import validate_image
        params = self._parameters("SYNO.FileStation.Download", "download", {"path": [path], "mode": "download"})
        raw, content_type = self._request(self._apis["SYNO.FileStation.Download"]["path"], params, limit=metadata["size"])
        if "json" in content_type or "text/html" in content_type:
            raise DispatchError("NAS 未回傳商品原圖；未當成缺圖或使用其他圖片")
        if len(raw) != metadata["size"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise DispatchError("NAS 原圖大小／校驗碼不符，停止使用")
        asset = validate_image(raw, metadata["name"])
        if asset["mime"] != metadata["mime"]:
            raise DispatchError("NAS 原圖格式不符，停止使用")
        return asset

    def put_asset(self, asset):
        """Skip existing paths, then verify bytes. Never overwrite/delete files."""
        return self._put_asset(asset)

    def put_thumbnail(self, asset):
        from image_thumbnails import check_thumbnail
        return self._put_asset(check_thumbnail(asset), thumbnail=True)

    def _put_asset(self, asset, *, thumbnail=False):
        from dispatch_storage import asset_bytes, validate_image
        clean = validate_image(asset_bytes(asset), asset["name"])
        metadata = self._metadata(clean)
        path = self._path(metadata, thumbnail=thumbnail)
        folder, filename = path.rsplit("/", 1)
        if not self._root_checked:
            self.check_root()
        try:
            self._json_call("SYNO.FileStation.Upload", "upload", {
                "path": folder, "create_parents": True, "overwrite": False,
            }, files={"file": (filename, asset_bytes(clean), clean["mime"])})
        except DispatchError as exc:
            # The server may have accepted the image before the connection died.
            # Do not blindly retry writes or publish an unverified reference.
            detail = exc.safe_summary() if isinstance(exc, NasDiagnosticError) else "未分類錯誤"
            raise DispatchError(f"NAS 原圖保存結果待確認（上傳 API：{detail}）；未發布圖片索引，請只重試圖片核對") from None
        if thumbnail:
            self.get_thumbnail(metadata)
        else:
            self.get_asset(metadata)
        return metadata
