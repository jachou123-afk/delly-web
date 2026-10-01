"""Offline checks for fresh-key verification and archive cursor safety."""

import base64
from contextlib import closing, ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from wecom_archive import download as archive


class FreshStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            "BASE": base, "PRIVATE_KEY": base / "archive_private_key.pem",
            "PUBLIC_KEY": base / "archive_public_key.pem", "DATABASE": base / "messages.sqlite",
            "FRESH_SETUP": base / "fresh-setup.json",
            "CREDENTIALS": base / "credentials.json", "MEDIA_DIR": base / "media",
        }.items():
            self.stack.enter_context(patch.object(archive, name, value))
        archive.PRIVATE_KEY.write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        self.stack.enter_context(patch.object(archive, "_credentials", return_value=("test", "test")))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.pages = {}
        owner = self

        class FakeSdk:
            def __init__(self, *_):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def get_chat_data(self, seq, limit):
                batch = owner.pages.get(seq, [])
                if isinstance(batch, Exception):
                    raise batch
                return batch

            def decrypt_data(self, random_key, message):
                if random_key != b"test-key":
                    raise archive.ArchiveError("解密失敗，無法解開測試訊息")
                return json.loads(message)

        self.stack.enter_context(patch.object(archive, "FinanceSdk", FakeSdk))

    def record(self, seq, version, *, key=None):
        encrypted = (key or self.key).public_key().encrypt(b"test-key", padding.PKCS1v15())
        return {
            "seq": seq, "publickey_ver": version,
            "encrypt_random_key": base64.b64encode(encrypted).decode("ascii"),
            "encrypt_chat_msg": json.dumps({"msgid": f"message-{seq}", "msgtype": "text",
                                             "text": {"content": "test"}}),
        }

    def state(self):
        with closing(archive._database()) as db:
            metadata = archive._metadata(db)
            messages = db.execute("SELECT seq FROM messages ORDER BY seq").fetchall()
        return metadata, messages

    def test_new_key_must_decrypt_before_skipped_cursor_is_committed(self):
        self.pages = {0: [self.record(1, 1, key=self.other_key)],
                      1: [self.record(2, 2)]}
        archive.sync(100, None, fresh_key_version=2)
        metadata, messages = self.state()
        self.assertEqual(messages, [(2,)])
        self.assertEqual(metadata["last_seq"], "2")
        self.assertEqual(metadata["skipped_old_messages"], "1")
        self.assertEqual(metadata["fresh_key_version"], "2")

    def test_only_old_messages_cannot_report_success_or_save_cursor(self):
        self.pages = {0: [self.record(1, 1)]}
        with self.assertRaisesRegex(archive.ArchiveError, "尚未驗證"):
            archive.sync(100, None, fresh_key_version=2)
        self.assertEqual(self.state(), ({}, []))

    def test_page_limit_does_not_save_unverified_skip(self):
        self.pages = {0: [self.record(1, 1)], 1: [self.record(2, 2)]}
        with self.assertRaisesRegex(archive.ArchiveError, "尚未驗證"):
            archive.sync(100, 1, fresh_key_version=2)
        self.assertEqual(self.state(), ({}, []))

    def test_api_failure_does_not_commit_pending_skip(self):
        self.pages = {0: [self.record(1, 1)], 1: archive.ArchiveError("test failure")}
        with self.assertRaisesRegex(archive.ArchiveError, "test failure"):
            archive.sync(100, None, fresh_key_version=2)
        self.assertEqual(self.state(), ({}, []))

    def test_wrong_same_version_key_rolls_back_entire_batch(self):
        self.pages = {0: [self.record(1, 1), self.record(2, 2),
                          self.record(3, 2, key=self.other_key)]}
        with self.assertRaisesRegex(archive.ArchiveError, "無法解開"):
            archive.sync(100, None, fresh_key_version=2)
        self.assertEqual(self.state(), ({}, []))

    def test_newer_version_rolls_back_entire_batch(self):
        self.pages = {0: [self.record(1, 1), self.record(2, 2), self.record(3, 3)]}
        with self.assertRaisesRegex(archive.ArchiveError, "較新的"):
            archive.sync(100, None, fresh_key_version=2)
        self.assertEqual(self.state(), ({}, []))

    def test_scheduled_sync_reuses_scope_and_rejects_future_key_change(self):
        self.pages = {0: [self.record(1, 2)]}
        archive.sync(100, None, fresh_key_version=2)
        self.pages = {1: [self.record(2, 1), self.record(3, 2)]}
        archive.sync(100, None, saved_credentials=True)
        metadata, messages = self.state()
        self.assertEqual(messages, [(1,), (3,)])
        self.assertEqual(metadata["skipped_old_messages"], "1")
        self.pages = {3: [self.record(4, 3)]}
        with self.assertRaisesRegex(archive.ArchiveError, "較新的"):
            archive.sync(100, None)
        self.assertEqual(self.state(), (metadata, messages))

    def test_scope_cannot_be_reassigned(self):
        self.pages = {0: [self.record(1, 2)]}
        archive.sync(100, None, fresh_key_version=2)
        with self.assertRaisesRegex(archive.ArchiveError, "不能改版本"):
            archive.sync(100, None, fresh_key_version=3)

    def test_replaced_private_key_is_rejected(self):
        self.pages = {0: [self.record(1, 2)]}
        archive.sync(100, None, fresh_key_version=2)
        archive.PRIVATE_KEY.write_bytes(self.other_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        with self.assertRaisesRegex(archive.ArchiveError, "私鑰.*不同"):
            archive.sync(100, None)

    def test_legacy_history_is_not_converted_to_fresh_scope(self):
        self.pages = {0: [self.record(1, 1)]}
        archive.sync(100, None)
        previous = self.state()
        with self.assertRaisesRegex(archive.ArchiveError, "空資料庫"):
            archive.sync(100, None, fresh_key_version=2)
        self.assertEqual(self.state(), previous)

    def test_legacy_mode_does_not_ignore_decryption_errors(self):
        self.pages = {0: [self.record(1, 1, key=self.other_key)]}
        with self.assertRaisesRegex(archive.ArchiveError, "無法解開"):
            archive.sync(100, None)
        self.assertEqual(self.state(), ({}, []))

    def test_export_discloses_old_message_omission_and_scan_cursor(self):
        self.pages = {0: [self.record(1, 1), self.record(2, 2)], 2: [self.record(3, 1)]}
        archive.sync(100, None, fresh_key_version=2)
        output = archive.BASE / "export.zip"
        archive.export(output)
        with ZipFile(output) as zipped:
            manifest = json.loads(zipped.read("manifest.json"))
        self.assertEqual(manifest["archive_scope"], "fresh_key_version_only")
        self.assertEqual(manifest["messages"], 1)
        self.assertEqual(manifest["last_seq"], 2)
        self.assertEqual(manifest["sync_cursor"], 3)
        self.assertEqual(manifest["skipped_old_messages"], 2)
        self.assertTrue(archive._archive_current(output))
        self.pages = {3: [self.record(4, 1)]}
        archive.sync(100, None)
        self.assertFalse(archive._archive_current(output))

    def test_prepare_fresh_refuses_existing_key_without_overwrite(self):
        before = archive.PRIVATE_KEY.read_bytes()
        with self.assertRaises(archive.ArchiveError):
            archive.prepare_fresh()
        self.assertEqual(archive.PRIVATE_KEY.read_bytes(), before)

    def test_prepare_fresh_creates_matching_pair_then_refuses_retry(self):
        archive.PRIVATE_KEY.unlink()
        archive.prepare_fresh()
        private = serialization.load_pem_private_key(archive.PRIVATE_KEY.read_bytes(), None)
        public = serialization.load_pem_public_key(archive.PUBLIC_KEY.read_bytes())
        self.assertEqual(private.public_key().public_numbers(), public.public_numbers())
        self.assertEqual(json.loads(archive.FRESH_SETUP.read_text())["publickey_fingerprint"],
                         archive._key_fingerprint(private))
        before = archive.PRIVATE_KEY.read_bytes()
        with self.assertRaises(archive.ArchiveError):
            archive.prepare_fresh()
        self.assertEqual(archive.PRIVATE_KEY.read_bytes(), before)

    def test_prepared_key_requires_explicit_first_version_without_advancing(self):
        archive.FRESH_SETUP.write_text(json.dumps(
            {"format": 1, "publickey_fingerprint": archive._key_fingerprint(self.key)}))
        with self.assertRaisesRegex(archive.ArchiveError, "首次執行"):
            archive.sync(100, None)
        self.assertEqual(self.state(), ({}, []))

    def test_prepare_fresh_refuses_existing_database_and_credentials(self):
        archive.PRIVATE_KEY.unlink()
        for existing in (archive.DATABASE, archive.CREDENTIALS):
            with self.subTest(existing=existing.name):
                existing.write_bytes(b"preserve")
                with self.assertRaises(archive.ArchiveError):
                    archive.prepare_fresh()
                self.assertFalse(archive.PRIVATE_KEY.exists())
                self.assertEqual(existing.read_bytes(), b"preserve")
                existing.unlink()


if __name__ == "__main__":
    unittest.main()
