import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from build_software_manifest import RELEASE_FILES
from taistock.backup import BACKUP_WHITELIST, create_backup, restore_backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def make_database(self, relative="data/scanner.db"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target)
        try:
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('kept')")
            connection.commit()
        finally:
            connection.close()
        return target

    def write_archive(self, name, files, *, manifest_files=None, created_at=None, extras=None):
        archive_path = self.root / name
        metadata = ({
            relative: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for relative, content in files.items()
        } if manifest_files is None else manifest_files)
        manifest = {
            "schema_version": 1,
            "created_at": created_at or (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(),
            "app_version": "test",
            "files": metadata,
        }
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for relative, content in files.items():
                archive.writestr(relative, content)
            for info, content in extras or []:
                archive.writestr(info, content)
        return archive_path

    def test_backup_module_is_in_release_manifest(self):
        self.assertIn("taistock/backup.py", RELEASE_FILES)

    def test_create_backup_contains_only_existing_whitelist_with_hash_manifest(self):
        database = self.make_database()
        latest = self.root / "data/latest.json"
        latest.write_text('{"data_date":"2026-10-16"}', encoding="utf-8")
        (self.root / "data/backtest_cache").mkdir()
        (self.root / "data/backtest_cache/secret.json").write_text("no", encoding="utf-8")
        (self.root / "token.txt").write_text("secret", encoding="utf-8")
        destination = self.root / "out/backup.zip"

        result = create_backup(self.root, destination, app_version="2026.10.17.6")

        self.assertEqual(result["path"], destination.resolve())
        self.assertEqual(result["file_count"], 2)
        self.assertTrue(result["completed"])
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"manifest.json", "data/scanner.db", "data/latest.json"})
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["app_version"], "2026.10.17.6")
            self.assertTrue(manifest["created_at"].endswith("Z"))
            for relative, source in (("data/scanner.db", database), ("data/latest.json", latest)):
                metadata = manifest["files"][relative]
                content = source.read_bytes()
                self.assertEqual(metadata["size"], len(content))
                self.assertEqual(metadata["sha256"], hashlib.sha256(content).hexdigest())

    def test_create_backup_skips_missing_allowlisted_files(self):
        destination = self.root / "empty.zip"

        result = create_backup(self.root, destination)

        self.assertEqual(result["file_count"], 0)
        with zipfile.ZipFile(destination) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["files"], {})

    def test_create_backup_reads_app_version_from_release_manifest(self):
        (self.root / "software_manifest.json").write_text(
            '{"version":"2026.10.17.6","files":{}}', encoding="utf-8")
        destination = self.root / "versioned.zip"

        create_backup(self.root, destination)

        with zipfile.ZipFile(destination) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["app_version"], "2026.10.17.6")

    def test_create_backup_refuses_state_larger_than_restore_limits(self):
        (self.root / "data/latest.json").write_bytes(b"x")
        with patch("taistock.backup.MAX_FILE_SIZE", 0):
            with self.assertRaisesRegex(ValueError, "大小上限"):
                create_backup(self.root, self.root / "too-large.zip")
        self.assertFalse((self.root / "too-large.zip").exists())

    def test_create_backup_failure_preserves_destination_and_removes_temp(self):
        self.make_database()
        destination = self.root / "backup.zip"
        destination.write_bytes(b"old backup")
        real_open = zipfile.ZipFile.open

        def fail_payload(archive, name, mode="r", *args, **kwargs):
            if name == "data/scanner.db" and mode == "w":
                raise OSError("simulated write failure")
            return real_open(archive, name, mode, *args, **kwargs)

        with patch("taistock.backup.zipfile.ZipFile.open", new=fail_payload):
            with self.assertRaisesRegex(OSError, "simulated"):
                create_backup(self.root, destination)
        self.assertEqual(destination.read_bytes(), b"old backup")
        self.assertEqual(list(self.root.glob(".backup.zip.*.tmp")), [])

    def test_restore_verified_backup_recreates_files(self):
        database = self.make_database()
        settings = self.root / "user_settings.json"
        settings.write_text('{"theme":"dark"}', encoding="utf-8")
        archive = self.root / "backup.zip"
        create_backup(self.root, archive)
        database.unlink()
        settings.unlink()

        result = restore_backup(self.root, archive)

        self.assertTrue(result["completed"])
        self.assertEqual(result["file_count"], 2)
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "kept")
        finally:
            connection.close()
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"theme": "dark"})

    def test_restore_rejects_hash_tampering(self):
        content = b'{"data_date":"2026-10-16"}'
        metadata = {"data/latest.json": {"size": len(content), "sha256": hashlib.sha256(b"original").hexdigest()}}
        archive = self.write_archive("tampered.zip", {"data/latest.json": content}, manifest_files=metadata)
        with self.assertRaisesRegex(ValueError, "雜湊或大小"):
            restore_backup(self.root, archive)
        self.assertFalse((self.root / "data/latest.json").exists())

    def test_restore_rejects_traversal_and_absolute_or_backslash_paths(self):
        for index, unsafe in enumerate(("../escape.json", "/absolute.json", "data\\latest.json")):
            with self.subTest(path=unsafe):
                archive = self.write_archive(f"unsafe-{index}.zip", {unsafe: b"x"})
                with self.assertRaisesRegex(ValueError, "不安全"):
                    restore_backup(self.root, archive)
        self.assertFalse((self.root.parent / "escape.json").exists())

    def test_restore_rejects_non_allowlisted_files(self):
        archive = self.write_archive("forbidden.zip", {"data/backtest_cache/payload.json": b"secret"})
        with self.assertRaisesRegex(ValueError, "白名單"):
            restore_backup(self.root, archive)

    def test_restore_rejects_incomplete_manifest(self):
        metadata = {"data/latest.json": {"size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}}
        archive = self.write_archive("missing.zip", {}, manifest_files=metadata)
        with self.assertRaisesRegex(ValueError, "manifest"):
            restore_backup(self.root, archive)

    def test_restore_rejects_newer_target_mtime(self):
        archive = self.write_archive(
            "old.zip", {"data/latest.json": b'{"data_date":"2026-10-15"}'},
            created_at="2026-10-16T00:00:00Z")
        target = self.root / "data/latest.json"
        target.write_text('{"data_date":"new"}', encoding="utf-8")
        newer = datetime(2026, 10, 17, tzinfo=timezone.utc).timestamp()
        os.utime(target, (newer, newer))
        with self.assertRaisesRegex(ValueError, "較新"):
            restore_backup(self.root, archive)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"data_date": "new"})

    def test_restore_rejects_newer_daily_status_data_date(self):
        archive = self.write_archive(
            "older-day.zip", {"data/daily_status.json": b'{"data_date":"2026-10-15"}'})
        target = self.root / "data/daily_status.json"
        target.write_text('{"data_date":"2026-10-16"}', encoding="utf-8")
        old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(target, (old, old))
        with self.assertRaisesRegex(ValueError, "data_date"):
            restore_backup(self.root, archive)

    def test_restore_rejects_corrupt_sqlite(self):
        archive = self.write_archive("bad-db.zip", {"data/scanner.db": b"not sqlite"})
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            restore_backup(self.root, archive)
        self.assertFalse((self.root / "data/scanner.db").exists())

    def test_restore_rolls_back_all_targets_after_replace_failure(self):
        files = {"data/latest.json": b'{"value":"backup-1"}',
                 "data/daily_status.json": b'{"value":"backup-2"}'}
        archive = self.write_archive("rollback.zip", files)
        latest = self.root / "data/latest.json"
        status = self.root / "data/daily_status.json"
        latest.write_text('{"value":"current-1"}', encoding="utf-8")
        status.write_text('{"value":"current-2"}', encoding="utf-8")
        old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(latest, (old, old)); os.utime(status, (old, old))
        real_replace = os.replace
        failed = False

        def fail_second_install(source, destination):
            nonlocal failed
            if "staged" in str(source) and str(destination).endswith("daily_status.json") and not failed:
                failed = True
                raise OSError("simulated replace failure")
            return real_replace(source, destination)

        with patch("taistock.backup.os.replace", side_effect=fail_second_install):
            with self.assertRaisesRegex(OSError, "simulated"):
                restore_backup(self.root, archive)
        self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"value": "current-1"})
        self.assertEqual(json.loads(status.read_text(encoding="utf-8")), {"value": "current-2"})

    def test_restore_rejects_symlink_and_zip_bomb_limits(self):
        symlink = zipfile.ZipInfo("data/latest.json")
        symlink.create_system = 3
        symlink.external_attr = (0o120777 << 16)
        symlink_content = b"target"
        symlink_metadata = {"data/latest.json": {
            "size": len(symlink_content), "sha256": hashlib.sha256(symlink_content).hexdigest()}}
        archive = self.write_archive(
            "symlink.zip", {}, manifest_files=symlink_metadata, extras=[(symlink, symlink_content)])
        with self.assertRaisesRegex(ValueError, "symlink"):
            restore_backup(self.root, archive)

        archive = self.write_archive("oversized.zip", {"data/latest.json": b"x"})
        with patch("taistock.backup.MAX_FILE_SIZE", 0):
            with self.assertRaisesRegex(ValueError, "大小上限"):
                restore_backup(self.root, archive)


if __name__ == "__main__":
    unittest.main()
