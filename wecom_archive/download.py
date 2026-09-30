"""Download WeCom conversation archives with the official Windows C SDK."""

from __future__ import annotations

import argparse
import base64
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
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding


BASE = Path(os.environ["LOCALAPPDATA"]) / "WeComArchive"
SDK_DIR = BASE / "sdk-v3"
PRIVATE_KEY = BASE / "archive_private_key.pem"
DATABASE = BASE / "messages.sqlite"
MEDIA_DIR = BASE / "media"
TAIPEI = timezone(timedelta(hours=8))


class ArchiveError(Exception):
    pass


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


def sync(limit: int, max_pages: int | None) -> None:
    if not PRIVATE_KEY.is_file():
        raise ArchiveError(f"找不到私鑰：{PRIVATE_KEY}")
    private_key = serialization.load_pem_private_key(PRIVATE_KEY.read_bytes(), password=None)
    corp_id = input("企業 ID（我的企業 → 企業資訊）：").strip()
    secret = getpass.getpass("會話內容存檔 Secret（輸入時不顯示）：").strip()
    if not corp_id or not secret:
        raise ArchiveError("企業 ID 和會話內容存檔 Secret 都不能空白")
    connection = _database()
    pages = 0
    added = 0
    try:
        with FinanceSdk(corp_id, secret) as sdk:
            while max_pages is None or pages < max_pages:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE name='last_seq'"
                ).fetchone()
                seq = int(row[0]) if row else 0
                batch = sdk.get_chat_data(seq, limit)
                if not batch:
                    break
                decoded = []
                max_seq = seq
                for record in batch:
                    current_seq = int(record["seq"])
                    if current_seq <= seq:
                        raise ArchiveError("SDK 回傳的序號未前進，已停止以避免重複")
                    try:
                        random_key = _decrypt_random_key(record, private_key)
                    except ValueError as exc:
                        raise ArchiveError(
                            "目前私鑰無法解開公鑰版本 "
                            f"{record.get('publickey_ver')} 的訊息；請保留該版本的原私鑰"
                        ) from exc
                    message = sdk.decrypt_data(random_key, record["encrypt_chat_msg"])
                    decoded.append((current_seq, record, message))
                    max_seq = max(max_seq, current_seq)
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
                    connection.execute(
                        "INSERT INTO metadata(name,value) VALUES('last_seq',?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                        (str(max_seq),),
                    )
                pages += 1
                print(f"已保存 {added} 則訊息；接續序號 {max_seq}")
            media_ok, media_failed = _sync_media(sdk, connection)
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
                    {"messages": len(records), "media_files": len(media), "exported_at": datetime.now(timezone.utc).isoformat()},
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


def main() -> int:
    parser = argparse.ArgumentParser(description="企業微信會話存檔下載與匯出")
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync", help="拉取新對話和附件")
    sync_parser.add_argument("--limit", type=int, default=100)
    sync_parser.add_argument("--max-pages", type=int)
    export_parser = commands.add_parser("export", help="匯出完整 ZIP")
    export_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "sync":
            if not 1 <= args.limit <= 1000 or (args.max_pages is not None and args.max_pages < 1):
                raise ArchiveError("limit 必須為 1–1000，max-pages 必須大於 0")
            sync(args.limit, args.max_pages)
        else:
            export(args.output)
        return 0
    except (ArchiveError, ValueError, KeyError, OSError, EOFError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
