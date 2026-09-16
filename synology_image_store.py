"""Opt-in, content-addressed NAS originals. No sharing, deletes or permission APIs.

This adapter is not enabled by merely importing it. Credentials belong in a
server-side secret store, never in product rows, browser URLs or this module.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from urllib.parse import urlsplit

import requests

from dispatch_manager import DispatchError


MAX_JSON_BYTES = 64 * 1024
TIMEOUT = (8, 30)
API_VERSIONS = {
    "SYNO.API.Auth": 3,
    "SYNO.FileStation.List": 2,
    "SYNO.FileStation.Upload": 2,
    "SYNO.FileStation.Download": 2,
}
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
NAS_REDIRECT_ERROR = "NAS 入口已轉向；請重新確認網址，未跟隨或向轉向網址傳送帳密"


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
        try:
            response = self._session.post(
                self.config.base_url.rstrip("/") + "/webapi/" + path,
                data=data, files=files, timeout=TIMEOUT, allow_redirects=False,
                verify=True, stream=True,
            )
            if 300 <= response.status_code < 400:
                raise DispatchError(NAS_REDIRECT_ERROR)
            if response.status_code != 200:
                raise DispatchError(f"NAS 讀寫失敗（HTTP {response.status_code}）；未視為缺圖")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError:
                    raise DispatchError("NAS 回應長度不合法") from None
                if length < 0 or length > limit:
                    raise DispatchError("NAS 回應超過允許大小，已停止讀取")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise DispatchError("NAS 回應超過允許大小，已停止讀取")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", "").lower()
        except requests.RequestException:
            # Requests exceptions can contain URLs, proxy details or credentials.
            raise DispatchError("NAS 網路／憑證連線失敗；請確認連線，勿關閉憑證驗證") from None
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _decode(raw):
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise DispatchError("NAS 未回傳有效 API 資料，可能為登入或轉接頁") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            # Never echo remote error bodies, which may include private paths.
            code = result.get("error", {}).get("code") if isinstance(result, dict) and isinstance(result.get("error"), dict) else None
            safe_code = str(code) if isinstance(code, int) and not isinstance(code, bool) else "未知"
            raise DispatchError(f"NAS API 未完成（錯誤碼 {safe_code}）；未重試登入或寫入")
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise DispatchError("NAS API 回應結構不完整")
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
        except DispatchError:
            # The server may have accepted the image before the connection died.
            # Do not blindly retry writes or publish an unverified reference.
            raise DispatchError("NAS 原圖保存結果待確認；未發布圖片索引，請只重試圖片核對") from None
        if thumbnail:
            self.get_thumbnail(metadata)
        else:
            self.get_asset(metadata)
        return metadata
