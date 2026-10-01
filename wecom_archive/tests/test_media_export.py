"""Offline checks for readable image attachments and older ZIP refresh."""

import base64
from contextlib import closing, ExitStack, redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from wecom_archive import download as archive


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg=="
)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n"
    "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQF"
    "BgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/"
    "ACv/2Q=="
)


class MediaExportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            "BASE": base, "DATABASE": base / "messages.sqlite", "MEDIA_DIR": base / "media",
        }.items():
            self.stack.enter_context(patch.object(archive, name, value))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        archive.MEDIA_DIR.mkdir()
        self.output = base / "export.zip"

    def seed(self, attachments):
        message = {"msgid": "message-1", "msgtype": "mixed", "mixed": [
            {"sdkfileid": name} for name in attachments
        ]}
        # Duplicate references must resolve to one exported filename.
        if attachments:
            message["mixed"].append({"sdkfileid": next(iter(attachments))})
        self.body = json.dumps(message, ensure_ascii=False)
        with closing(archive._database()) as db, db:
            db.execute("INSERT INTO messages VALUES(?,?,?,?)", (1, "message-1", 2, self.body))
            for name, data in attachments.items():
                filename = name + ".bin"
                (archive.MEDIA_DIR / filename).write_bytes(data)
                db.execute("INSERT INTO media VALUES(?,?,?)", (name, filename, 1))

    def test_images_open_by_extension_and_csv_preserves_attachment_mapping(self):
        binary = b"RIFF\x00\x00\x00\x00WAVEaudio-data"
        self.seed({"picture-a": PNG, "picture-b": JPEG, "audio": binary})
        archive.export(self.output)
        with ZipFile(self.output) as zipped:
            self.assertIsNone(zipped.testzip())
            self.assertEqual(zipped.read("media/picture-a.png"), PNG)
            self.assertEqual(zipped.read("media/picture-b.jpg"), JPEG)
            self.assertEqual(zipped.read("media/audio.bin"), binary)
            csv_rows = list(csv.reader(io.StringIO(zipped.read("messages.csv").decode("utf-8-sig"))))
            self.assertEqual(csv_rows[0][-1], "附件檔案")
            self.assertEqual(csv_rows[1][-1].splitlines(),
                             ["media/audio.bin", "media/picture-a.png", "media/picture-b.jpg"])
            self.assertEqual(csv_rows[1][7], self.body)
            original = json.loads(zipped.read("messages.jsonl"))
            self.assertEqual(original["message"], json.loads(self.body))
        with closing(archive._database()) as db:
            self.assertEqual(db.execute("SELECT body FROM messages").fetchone()[0], self.body)
            self.assertEqual(db.execute("SELECT filename FROM media ORDER BY filename").fetchall(),
                             [("audio.bin",), ("picture-a.bin",), ("picture-b.bin",)])
        self.assertEqual((archive.MEDIA_DIR / "picture-a.bin").read_bytes(), PNG)
        self.assertEqual((archive.MEDIA_DIR / "picture-b.bin").read_bytes(), JPEG)

    def test_same_count_older_zip_is_refreshed_to_readable_images(self):
        self.seed({"picture": PNG})
        with ZipFile(self.output, "w") as zipped:
            zipped.writestr("manifest.json", json.dumps({
                "messages": 1, "last_seq": 1, "media_files": 1,
            }))
            zipped.writestr("media/picture.bin", PNG)
        self.assertFalse(archive._archive_current(self.output))
        archive.export(self.output)
        self.assertTrue(archive._archive_current(self.output))
        with ZipFile(self.output) as zipped:
            self.assertIn("media/picture.png", zipped.namelist())

    def test_missing_attachment_preserves_previous_zip(self):
        self.seed({"picture": PNG})
        archive.export(self.output)
        previous = self.output.read_bytes()
        (archive.MEDIA_DIR / "picture.bin").unlink()
        with self.assertRaisesRegex(archive.ArchiveError, "附件檔案遺失"):
            archive.export(self.output)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_pending_attachment_preserves_previous_zip(self):
        self.seed({"picture": PNG})
        archive.export(self.output)
        previous = self.output.read_bytes()
        with closing(archive._database()) as db, db:
            db.execute("UPDATE media SET ready=0")
        with self.assertRaisesRegex(archive.ArchiveError, "未下載"):
            archive.export(self.output)
        self.assertEqual(self.output.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
