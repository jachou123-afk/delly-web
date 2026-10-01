"""Download WeCom conversation archives with the official Windows C SDK."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import ctypes
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


BASE = Path(os.environ["LOCALAPPDATA"]) / "WeComArchive"
SDK_DIR = BASE / "sdk-v3"
PRIVATE_KEY = BASE / "archive_private_key.pem"
PUBLIC_KEY = BASE / "archive_public_key.pem"
FRESH_SETUP = BASE / "fresh-setup.json"
DATABASE = BASE / "messages.sqlite"
MEDIA_DIR = BASE / "media"
CREDENTIALS = BASE / "credentials.json"
DEFAULT_EXPORT = Path(__file__).resolve().parents[1] / "wecom_archive_data" / "wecom-conversations.zip"
TAIPEI = timezone(timedelta(hours=8))


class ArchiveError(Exception):
    pass


def prepare_fresh() -> None:
    """Prepare a new key pair without replacing existing archive files."""
    BASE.mkdir(parents=True, exist_ok=True)
    if any(path.exists() for path in (PRIVATE_KEY, PUBLIC_KEY, FRESH_SETUP, DATABASE, CREDENTIALS)):
        raise ArchiveError("已有金鑰、資料庫或憑證，未重新產生或覆蓋；請先核對現有設定")
    if MEDIA_DIR.exists() and any(MEDIA_DIR.iterdir()):
        raise ArchiveError("已有附件，未重新產生金鑰；請先核對現有存檔")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Exclusive creation keeps repeated preparation from replacing the private key.
    with PRIVATE_KEY.open("xb") as stream:
        stream.write(private_bytes)
    with PUBLIC_KEY.open("xb") as stream:
        stream.write(public_bytes)
    with FRESH_SETUP.open("x", encoding="utf-8") as stream:
        json.dump({"format": 1, "publickey_fingerprint": _key_fingerprint(private_key)}, stream)
    print(f"新金鑰已建立。管理端使用的公鑰檔：{PUBLIC_KEY}")
    print("尚未切換管理端公鑰或開始存檔；請保留這組私鑰")


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(connection.execute("SELECT name,value FROM metadata"))


def _set_metadata(connection: sqlite3.Connection, name: str, value) -> None:
    connection.execute(
        "INSERT INTO metadata(name,value) VALUES(?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, str(value)),
    )


def _key_fingerprint(private_key) -> str:
    return hashlib.sha256(private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )).hexdigest()


def _fresh_scope(connection: sqlite3.Connection, requested: int | None,
                 fingerprint: str) -> tuple[int | None, bool]:
    metadata = _metadata(connection)
    saved = metadata.get("fresh_key_version")
    if requested is not None and requested < 1:
        raise ArchiveError("新公鑰版本必須大於 0，並以管理端實際版本為準")
    if saved is not None:
        version = int(saved)
        if requested is not None and requested != version:
            raise ArchiveError("新存檔已固定公鑰版本，不能改版本後略過既有訊息")
        if metadata.get("fresh_key_fingerprint") != fingerprint:
            raise ArchiveError("目前私鑰與新存檔建立時不同，已停止同步")
        return version, True
    if requested is not None:
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        media = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        if messages or media or int(metadata.get("last_seq", "0")):
            raise ArchiveError("全新模式只適用空資料庫；既有歷史與接續序號未更動")
    return requested, False


def _scope_manifest(connection: sqlite3.Connection) -> dict:
    metadata = _metadata(connection)
    if "fresh_key_version" not in metadata:
        return {}
    return {
        "archive_scope": "fresh_key_version_only",
        "fresh_key_version": int(metadata["fresh_key_version"]),
        "scope_verified_at": metadata["scope_verified_at"],
        "skipped_old_messages": int(metadata.get("skipped_old_messages", "0")),
        "sync_cursor": int(metadata.get("last_seq", "0")),
    }


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Encrypt or decrypt for the current Windows user, without a shared password."""
    if os.name != "nt":
        raise ArchiveError("排程憑證僅支援 Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    method = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    method.restype = ctypes.c_int
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        action = "加密" if protect else "解開"
        raise ArchiveError(f"無法使用本機 Windows 帳號{action}排程憑證（{ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def configure(corp_id: str | None, *, secret_stdin: bool = False) -> None:
    corp_id = (corp_id or input("企業 ID：")).strip()
    secret = (sys.stdin.readline() if secret_stdin else getpass.getpass(
        "會話內容存檔 Secret（輸入時不顯示）："
    )).strip()
    if not corp_id or not secret.strip():
        raise ArchiveError("企業 ID 和 Secret 都不能空白")
    encrypted = base64.b64encode(_dpapi(secret.encode("utf-8"), protect=True)).decode("ascii")
    BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=BASE,
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump({"version": 1, "corp_id": corp_id, "secret_dpapi": encrypted}, stream)
    try:
        os.replace(temporary, CREDENTIALS)
    finally:
        temporary.unlink(missing_ok=True)
    print("排程憑證已加密保存於目前 Windows 帳號")


def _credentials(saved: bool) -> tuple[str, str]:
    if saved:
        if not CREDENTIALS.is_file():
            raise ArchiveError("尚未設定排程憑證，請先執行 configure")
        saved_data = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        if saved_data.get("version") != 1:
            raise ArchiveError("排程憑證格式不支援")
        return saved_data["corp_id"], _dpapi(
            base64.b64decode(saved_data["secret_dpapi"], validate=True), protect=False
        ).decode("utf-8")
    corp_id = input("企業 ID（我的企業 → 企業資訊）：").strip()
    secret = getpass.getpass("會話內容存檔 Secret（輸入時不顯示）：").strip()
    if not corp_id or not secret:
        raise ArchiveError("企業 ID 和會話內容存檔 Secret 都不能空白")
    return corp_id, secret


def _cstr(value: str) -> bytes:
    return value.encode("utf-8")


class FinanceSdk:
    """Small ownership-safe wrapper around the official SDK C ABI."""

    def __init__(self, corp_id: str, secret: str) -> None:
        dll = SDK_DIR / "WeWorkFinanceSdk.dll"
        if not dll.is_file():
            raise ArchiveError(f"找不到官方 SDK：{dll}")
        self._dll_path = os.add_dll_directory(str(SDK_DIR))
        try:
            self.lib = ctypes.CDLL(str(dll))
        except Exception:
            self._dll_path.close()
            raise
        self._bind()
        self.sdk = self.lib.NewSdk()
        if not self.sdk:
            self.close()
            raise ArchiveError("無法建立企業微信 SDK 物件")
        result = self.lib.Init(self.sdk, _cstr(corp_id), _cstr(secret))
        if result != 0:
            self.close()
            raise ArchiveError(f"SDK 初始化失敗（錯誤碼 {result}）；請核對企業 ID 與會話存檔 Secret")

    def _bind(self) -> None:
        lib = self.lib
        lib.NewSdk.restype = ctypes.c_void_p
        lib.DestroySdk.argtypes = [ctypes.c_void_p]
        lib.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.Init.restype = ctypes.c_int
        lib.NewSlice.restype = ctypes.c_void_p
        lib.FreeSlice.argtypes = [ctypes.c_void_p]
        lib.GetContentFromSlice.argtypes = [ctypes.c_void_p]
        lib.GetContentFromSlice.restype = ctypes.c_void_p
        lib.GetSliceLen.argtypes = [ctypes.c_void_p]
        lib.GetSliceLen.restype = ctypes.c_int
        lib.GetChatData.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p,
        ]
        lib.GetChatData.restype = ctypes.c_int
        lib.DecryptData.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
        lib.DecryptData.restype = ctypes.c_int
        lib.NewMediaData.restype = ctypes.c_void_p
        lib.FreeMediaData.argtypes = [ctypes.c_void_p]
        lib.GetMediaData.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p,
        ]
        lib.GetMediaData.restype = ctypes.c_int
        lib.GetData.argtypes = [ctypes.c_void_p]
        lib.GetData.restype = ctypes.c_void_p
        lib.GetDataLen.argtypes = [ctypes.c_void_p]
        lib.GetDataLen.restype = ctypes.c_int
        lib.GetOutIndexBuf.argtypes = [ctypes.c_void_p]
        lib.GetOutIndexBuf.restype = ctypes.c_void_p
        lib.GetIndexLen.argtypes = [ctypes.c_void_p]
        lib.GetIndexLen.restype = ctypes.c_int
        lib.IsMediaDataFinish.argtypes = [ctypes.c_void_p]
        lib.IsMediaDataFinish.restype = ctypes.c_int

    def _slice_bytes(self, pointer: int) -> bytes:
        size = self.lib.GetSliceLen(pointer)
        content = self.lib.GetContentFromSlice(pointer)
        if size < 0 or (size and not content):
            raise ArchiveError("官方 SDK 回傳無效資料")
        return ctypes.string_at(content, size) if size else b""

    def get_chat_data(self, seq: int, limit: int) -> list[dict]:
        output = self.lib.NewSlice()
        if not output:
            raise ArchiveError("無法配置 SDK 記憶體")
        try:
            result = self.lib.GetChatData(self.sdk, seq, limit, None, None, 15, output)
            if result != 0:
                raise ArchiveError(f"拉取對話失敗（SDK 錯誤碼 {result}）")
            response = json.loads(self._slice_bytes(output))
        finally:
            self.lib.FreeSlice(output)
        if response.get("errcode") != 0:
            raise ArchiveError(f"拉取對話失敗（平台錯誤碼 {response.get('errcode')}）")
        return response.get("chatdata", [])

    def decrypt_data(self, random_key: bytes, encrypted_message: str) -> dict:
        if b"\0" in random_key:
            raise ArchiveError("訊息金鑰含無效字元")
        output = self.lib.NewSlice()
        if not output:
            raise ArchiveError("無法配置 SDK 記憶體")
        try:
            result = self.lib.DecryptData(random_key, _cstr(encrypted_message), output)
            if result != 0:
                raise ArchiveError(f"解密訊息失敗（SDK 錯誤碼 {result}）")
            return json.loads(self._slice_bytes(output))
        finally:
            self.lib.FreeSlice(output)

    def media_chunks(self, sdkfileid: str):
        index = b""
        while True:
            output = self.lib.NewMediaData()
            if not output:
                raise ArchiveError("無法配置 SDK 媒體記憶體")
            try:
                result = self.lib.GetMediaData(
                    self.sdk, index, _cstr(sdkfileid), None, None, 15, output
                )
                if result != 0:
                    raise ArchiveError(f"下載附件失敗（SDK 錯誤碼 {result}）")
                size = self.lib.GetDataLen(output)
                pointer = self.lib.GetData(output)
                if size < 0 or (size and not pointer):
                    raise ArchiveError("官方 SDK 回傳無效附件")
                chunk = ctypes.string_at(pointer, size) if size else b""
                index_size = self.lib.GetIndexLen(output)
                index_pointer = self.lib.GetOutIndexBuf(output)
                if index_size < 0 or (index_size and not index_pointer):
                    raise ArchiveError("官方 SDK 回傳無效附件索引")
                next_index = ctypes.string_at(index_pointer, index_size) if index_size else b""
                finished = bool(self.lib.IsMediaDataFinish(output))
            finally:
                self.lib.FreeMediaData(output)
            yield chunk
            if finished:
                break
            if not next_index or next_index == index:
                raise ArchiveError("附件分片索引未前進，已停止下載")
            index = next_index

    def close(self) -> None:
        if getattr(self, "sdk", None):
            self.lib.DestroySdk(self.sdk)
            self.sdk = None
        if getattr(self, "_dll_path", None):
            self._dll_path.close()
            self._dll_path = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _database() -> sqlite3.Connection:
    BASE.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "seq INTEGER PRIMARY KEY, msgid TEXT, publickey_ver INTEGER, body TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS messages_msgid_unique "
        "ON messages(msgid) WHERE msgid IS NOT NULL"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS media ("
        "sdkfileid TEXT PRIMARY KEY, filename TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def _media_ids(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower().replace("_", "") == "sdkfileid" and isinstance(child, str) and child:
                yield child
            else:
                yield from _media_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _media_ids(child)


def _decrypt_random_key(record: dict, private_key) -> bytes:
    encrypted_key = base64.b64decode(record["encrypt_random_key"], validate=True)
    random_key = private_key.decrypt(encrypted_key, padding.PKCS1v15())
    return random_key


def _sync_media(sdk: FinanceSdk, connection: sqlite3.Connection) -> tuple[int, int]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    pending = connection.execute(
        "SELECT sdkfileid, filename FROM media WHERE ready=0 ORDER BY rowid"
    ).fetchall()
    succeeded = 0
    failed = 0
    for sdkfileid, filename in pending:
        destination = MEDIA_DIR / filename
        part = destination.with_name(destination.name + ".part")
        try:
            with part.open("wb") as stream:
                for chunk in sdk.media_chunks(sdkfileid):
                    stream.write(chunk)
            os.replace(part, destination)
            with connection:
                connection.execute(
                    "UPDATE media SET ready=1 WHERE sdkfileid=?", (sdkfileid,)
                )
            succeeded += 1
        except (ArchiveError, OSError):
            part.unlink(missing_ok=True)
            failed += 1
    return succeeded, failed


def sync(limit: int, max_pages: int | None, *, saved_credentials: bool = False,
         fresh_key_version: int | None = None) -> None:
    if not PRIVATE_KEY.is_file():
        raise ArchiveError(f"找不到私鑰：{PRIVATE_KEY}")
    private_key = serialization.load_pem_private_key(PRIVATE_KEY.read_bytes(), password=None)
    connection = _database()
    pages = 0
    added = 0
    try:
        fingerprint = _key_fingerprint(private_key)
        version, verified = _fresh_scope(connection, fresh_key_version, fingerprint)
        if FRESH_SETUP.is_file():
            prepared = json.loads(FRESH_SETUP.read_text(encoding="utf-8"))
            if prepared.get("format") != 1 or prepared.get("publickey_fingerprint") != fingerprint:
                raise ArchiveError("全新建置紀錄與目前私鑰不符，已停止同步")
            if version is None:
                raise ArchiveError("全新金鑰尚未驗證；首次執行請指定 --fresh-key-version 後台實際版本")
        corp_id, secret = _credentials(saved_credentials)
        seq = int(_metadata(connection).get("last_seq", "0"))
        pending_skipped = 0
        with FinanceSdk(corp_id, secret) as sdk:
            while max_pages is None or pages < max_pages:
                batch = sdk.get_chat_data(seq, limit)
                if not batch:
                    break
                decoded = []
                max_seq = seq
                skipped = 0
                for record in batch:
                    current_seq = int(record["seq"])
                    if current_seq <= seq:
                        raise ArchiveError("SDK 回傳的序號未前進，已停止以避免重複")
                    record_version = int(record["publickey_ver"])
                    if record_version < 1:
                        raise ArchiveError("訊息公鑰版本無效，已停止同步")
                    max_seq = max(max_seq, current_seq)
                    if version is not None:
                        if record_version < version:
                            skipped += 1
                            continue
                        if record_version > version:
                            raise ArchiveError("訊息使用較新的公鑰版本，已停止；請核對管理端與私鑰")
                    try:
                        random_key = _decrypt_random_key(record, private_key)
                    except ValueError as exc:
                        key_help = ("請核對新私鑰與管理端公鑰是否配對" if version is not None
                                    else "請保留該版本的原私鑰")
                        raise ArchiveError(
                            "目前私鑰無法解開公鑰版本 "
                            f"{record.get('publickey_ver')} 的訊息；{key_help}"
                        ) from exc
                    message = sdk.decrypt_data(random_key, record["encrypt_chat_msg"])
                    decoded.append((current_seq, record, message))
                pages += 1
                pending_skipped += skipped
                seq = max_seq
                if version is not None and not verified and not decoded:
                    # Scan old batches, but save no cursor until the new key is proven.
                    continue
                with connection:
                    for current_seq, record, message in decoded:
                        before = connection.total_changes
                        connection.execute(
                            "INSERT OR IGNORE INTO messages(seq,msgid,publickey_ver,body) "
                            "VALUES(?,?,?,?)",
                            (
                                current_seq,
                                message.get("msgid"),
                                int(record["publickey_ver"]),
                                json.dumps(message, ensure_ascii=False, separators=(",", ":")),
                            ),
                        )
                        added += connection.total_changes - before
                        for sdkfileid in set(_media_ids(message)):
                            filename = hashlib.sha256(_cstr(sdkfileid)).hexdigest() + ".bin"
                            connection.execute(
                                "INSERT OR IGNORE INTO media(sdkfileid,filename) VALUES(?,?)",
                                (sdkfileid, filename),
                            )
                    _set_metadata(connection, "last_seq", max_seq)
                    if version is not None:
                        if not verified:
                            _set_metadata(connection, "fresh_key_version", version)
                            _set_metadata(connection, "fresh_key_fingerprint", fingerprint)
                            _set_metadata(connection, "scope_verified_at",
                                          datetime.now(TAIPEI).isoformat())
                        previous = int(_metadata(connection).get("skipped_old_messages", "0"))
                        _set_metadata(connection, "skipped_old_messages", previous + pending_skipped)
                verified = version is not None
                pending_skipped = 0
                print(f"已保存 {added} 則訊息；接續序號 {max_seq}")
            if version is not None and not verified:
                raise ArchiveError(
                    f"尚未驗證公鑰版本 {version} 的新訊息，接續序號未保存；"
                    "請核對版本，並由使用者產生一則切換後的新對話再試"
                )
            media_ok, media_failed = _sync_media(sdk, connection)
        if version is not None:
            skipped_total = int(_metadata(connection).get("skipped_old_messages", "0"))
            print(f"存檔範圍：公鑰版本 {version}；略過舊版本 {skipped_total} 則，未匯入舊歷史")
        print(f"同步結束：新增 {added} 則訊息，附件成功 {media_ok}，待重試 {media_failed}")
    finally:
        connection.close()


def _safe_spreadsheet_text(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _timestamp(value) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).astimezone(TAIPEI).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def export(output: Path) -> None:
    if not DATABASE.is_file():
        raise ArchiveError("尚無本機對話資料；請先執行 sync")
    connection = _database()
    try:
        missing = connection.execute("SELECT COUNT(*) FROM media WHERE ready=0").fetchone()[0]
        if missing:
            raise ArchiveError(f"還有 {missing} 個附件未下載，請再次執行 sync 後匯出")
        records = connection.execute(
            "SELECT seq,publickey_ver,body FROM messages ORDER BY seq"
        ).fetchall()
        media = connection.execute("SELECT filename FROM media WHERE ready=1 ORDER BY filename").fetchall()
        scope = _scope_manifest(connection)
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        csv_text = io.StringIO(newline="")
        writer = csv.writer(csv_text)
        writer.writerow(["序號", "時間（台灣）", "發送者", "接收者", "群組ID", "類型", "內容", "原始JSON"])
        json_lines = []
        for seq, publickey_ver, body in records:
            message = json.loads(body)
            json_lines.append(
                json.dumps(
                    {"seq": seq, "publickey_ver": publickey_ver, "message": message},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            kind = str(message.get("msgtype", ""))
            payload = message.get(kind, {})
            content = payload.get("content", "") if isinstance(payload, dict) else ""
            if not content:
                content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            receivers = message.get("tolist", [])
            writer.writerow([
                seq,
                _timestamp(message.get("msgtime")),
                _safe_spreadsheet_text(str(message.get("from", ""))),
                _safe_spreadsheet_text(",".join(map(str, receivers)) if isinstance(receivers, list) else str(receivers)),
                _safe_spreadsheet_text(str(message.get("roomid", ""))),
                _safe_spreadsheet_text(kind),
                _safe_spreadsheet_text(str(content)),
                body,
            ])
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("messages.jsonl", "\n".join(json_lines) + ("\n" if json_lines else ""))
            archive.writestr("messages.csv", "\ufeff" + csv_text.getvalue())
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {"messages": len(records), "last_seq": records[-1][0] if records else 0,
                     "media_files": len(media), "exported_at": datetime.now(timezone.utc).isoformat(),
                     **scope},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for (filename,) in media:
                path = MEDIA_DIR / filename
                if not path.is_file():
                    raise ArchiveError(f"附件檔案遺失：{filename}")
                archive.write(path, "media/" + filename)
        os.replace(temporary, output)
        print(f"匯出完成：{output}；對話 {len(records)} 則、附件 {len(media)} 個")
    finally:
        temporary.unlink(missing_ok=True)


def _archive_current(output: Path) -> bool:
    if not output.is_file():
        return False
    connection = _database()
    try:
        messages, last_seq = connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(seq), 0) FROM messages"
        ).fetchone()
        media = connection.execute("SELECT COUNT(*) FROM media WHERE ready=1").fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM media WHERE ready=0").fetchone()[0]
        scope = _scope_manifest(connection)
    finally:
        connection.close()
    if pending:
        raise ArchiveError(f"仍有 {pending} 個附件待下載；保留前次 ZIP，不標示為最新")
    try:
        with ZipFile(output) as archive:
            old = json.loads(archive.read("manifest.json"))
    except (OSError, KeyError, ValueError, BadZipFile):
        return False
    return (old.get("messages"), old.get("last_seq"), old.get("media_files")) == (
        messages, last_seq, media
    ) and all(old.get(name) == value for name, value in scope.items())


def run(output: Path, *, fresh_key_version: int | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    status = output.with_name("wecom-sync-status.txt")
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    try:
        sync(100, None, saved_credentials=True, fresh_key_version=fresh_key_version)
        if _archive_current(output):
            print("資料沒有變動，沿用現有 ZIP")
        else:
            export(output)
        connection = _database()
        try:
            scope = _scope_manifest(connection)
        finally:
            connection.close()
        scope_text = (f"僅含公鑰版本 {scope['fresh_key_version']} 的新對話，未匯入舊歷史。\n"
                      if scope else "")
        status.write_text(f"{now}（台灣時間）同步成功。下載檔：{output.name}\n{scope_text}",
                          encoding="utf-8")
    except Exception as exc:
        status.write_text(
            f"{now}（台灣時間）同步失敗：{exc}\n現有 ZIP 可能較舊。\n", encoding="utf-8"
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="企業微信會話存檔下載與匯出")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare-fresh", help="在空存檔位置建立新金鑰，不覆蓋原檔")
    sync_parser = commands.add_parser("sync", help="拉取新對話和附件")
    sync_parser.add_argument("--limit", type=int, default=100)
    sync_parser.add_argument("--max-pages", type=int)
    sync_parser.add_argument("--saved-credentials", action="store_true")
    sync_parser.add_argument("--fresh-key-version", type=int,
                             help="全新存檔使用的管理端實際公鑰版本；舊版本只略過")
    export_parser = commands.add_parser("export", help="匯出完整 ZIP")
    export_parser.add_argument("--output", type=Path, required=True)
    configure_parser = commands.add_parser("configure", help="將 Secret 加密保存給本機排程使用")
    configure_parser.add_argument("--corp-id")
    configure_parser.add_argument("--secret-stdin", action="store_true", help=argparse.SUPPRESS)
    run_parser = commands.add_parser("run", help="使用加密憑證同步並更新固定 ZIP")
    run_parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT)
    run_parser.add_argument("--fresh-key-version", type=int,
                            help="首次全新存檔的管理端實際公鑰版本")
    args = parser.parse_args()
    try:
        if args.command == "sync":
            if not 1 <= args.limit <= 1000 or (args.max_pages is not None and args.max_pages < 1):
                raise ArchiveError("limit 必須為 1–1000，max-pages 必須大於 0")
            sync(args.limit, args.max_pages, saved_credentials=args.saved_credentials,
                 fresh_key_version=args.fresh_key_version)
        elif args.command == "prepare-fresh":
            prepare_fresh()
        elif args.command == "configure":
            configure(args.corp_id, secret_stdin=args.secret_stdin)
        elif args.command == "run":
            run(args.output, fresh_key_version=args.fresh_key_version)
        else:
            export(args.output)
        return 0
    except (ArchiveError, ValueError, KeyError, OSError, EOFError, binascii.Error, UnicodeError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
