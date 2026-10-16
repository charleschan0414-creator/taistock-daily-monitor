import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


class UpdaterTests(unittest.TestCase):
    def test_same_version_does_not_download_archive(self):
        from updater import check_for_updates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"version": "1.0", "files": {}}
            (root / "software_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            requested = []

            def download(url, timeout):
                requested.append(url)
                return json.dumps(manifest).encode("utf-8")

            result = check_for_updates(root=root, download=download)

        self.assertEqual(result.status, "current")
        self.assertEqual(len(requested), 1)

    def test_check_only_reports_update_without_downloading_archive(self):
        from updater import check_for_updates

        remote = {"version": "2.0", "files": {"app.py": "0" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )
            requested = []

            def download(url, timeout):
                requested.append(url)
                return json.dumps(remote).encode()

            result = check_for_updates(root=root, download=download, check_only=True)

        self.assertEqual(result.status, "available")
        self.assertEqual(result.version, "2.0")
        self.assertEqual(len(requested), 1)

    def test_changed_version_installs_only_verified_manifest_files(self):
        from updater import ARCHIVE_URL, check_for_updates

        new_app = b"print('new')\n"
        manifest = {
            "version": "2.0",
            "files": {"app.py": hashlib.sha256(new_app).hexdigest()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("repo-main/app.py", new_app)
            bundle.writestr("repo-main/data/scanner.db", b"hostile database")
            bundle.writestr("repo-main/not-listed.py", b"hostile code")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_bytes(b"print('old')\n")
            (root / "data").mkdir()
            (root / "data" / "scanner.db").write_bytes(b"user database")
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )

            def download(url, timeout):
                if url == ARCHIVE_URL:
                    return archive.getvalue()
                return json.dumps(manifest).encode("utf-8")

            result = check_for_updates(root=root, download=download)

            self.assertEqual(result.status, "updated")
            self.assertEqual((root / "app.py").read_bytes(), new_app)
            self.assertEqual((root / "data" / "scanner.db").read_bytes(), b"user database")
            self.assertFalse((root / "not-listed.py").exists())
            saved = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], "2.0")

    def test_manifest_cannot_write_protected_user_state(self):
        from updater import ARCHIVE_URL, check_for_updates

        protected = b"replacement"
        manifest = {
            "version": "2.0",
            "files": {"data/latest.json": hashlib.sha256(protected).hexdigest()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("repo-main/data/latest.json", protected)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            current = root / "data" / "latest.json"
            current.write_bytes(b"user data")
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            with self.assertRaisesRegex(ValueError, "protected"):
                check_for_updates(root=root, download=download)
            self.assertEqual(current.read_bytes(), b"user data")

    def test_hash_mismatch_keeps_installed_file_and_version(self):
        from updater import ARCHIVE_URL, check_for_updates

        manifest = {"version": "2.0", "files": {"app.py": "0" * 64}}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("repo-main/app.py", b"tampered")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app.py"
            app.write_bytes(b"working version")
            local_manifest = {"version": "1.0", "files": {}}
            (root / "software_manifest.json").write_text(
                json.dumps(local_manifest), encoding="utf-8"
            )

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            with self.assertRaisesRegex(ValueError, "SHA256"):
                check_for_updates(root=root, download=download)
            self.assertEqual(app.read_bytes(), b"working version")
            saved = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], "1.0")

    def test_install_failure_rolls_back_every_changed_file(self):
        from updater import ARCHIVE_URL, check_for_updates

        contents = {"app.py": b"new app", "taistock/core.py": b"new core"}
        manifest = {
            "version": "2.0",
            "files": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, value in contents.items():
                bundle.writestr(f"repo-main/{name}", value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "taistock").mkdir()
            (root / "app.py").write_bytes(b"old app")
            (root / "taistock" / "core.py").write_bytes(b"old core")
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )
            replace_calls = 0

            def flaky_replace(source, destination):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 2:
                    raise OSError("disk failure")
                Path(destination).write_bytes(Path(source).read_bytes())
                Path(source).unlink()

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            with self.assertRaisesRegex(OSError, "disk failure"):
                check_for_updates(root=root, download=download, replace=flaky_replace)
            self.assertEqual((root / "app.py").read_bytes(), b"old app")
            self.assertEqual((root / "taistock" / "core.py").read_bytes(), b"old core")

    def test_manifest_commit_failure_rolls_back_program_files(self):
        from updater import ARCHIVE_URL, check_for_updates

        content = b"new app"
        manifest = {
            "version": "2.0",
            "files": {"app.py": hashlib.sha256(content).hexdigest()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("repo-main/app.py", content)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_bytes(b"old app")
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )

            def fail_manifest_replace(source, destination):
                if Path(destination).name == "software_manifest.json":
                    raise OSError("manifest commit failure")
                Path(destination).write_bytes(Path(source).read_bytes())
                Path(source).unlink()

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            with self.assertRaisesRegex(OSError, "manifest commit failure"):
                check_for_updates(root=root, download=download, replace=fail_manifest_replace)
            self.assertEqual((root / "app.py").read_bytes(), b"old app")

    def test_launcher_checks_update_and_continues_after_failure(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "run.bat").read_text(encoding="utf-8")

        self.assertIn("updater.py", launcher)
        self.assertIn("--check-only", launcher)
        self.assertIn("app.py %*", launcher)
        self.assertNotIn("updater.py &&", launcher)
        self.assertNotIn("exit /b %errorlevel%", launcher)

    def test_cli_check_only_uses_non_installing_mode(self):
        from updater import UpdateResult, main

        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            return UpdateResult("current", "1.0", "current")

        exit_code = main(["--check-only"], runner=runner)

        self.assertEqual(exit_code, 0)
        self.assertTrue(calls[0]["check_only"])

    def test_windows_style_traversal_is_rejected(self):
        from updater import ARCHIVE_URL, check_for_updates

        content = b"escape"
        manifest = {
            "version": "2.0",
            "files": {"..\\outside.py": hashlib.sha256(content).hexdigest()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("repo-main/..\\outside.py", content)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            with self.assertRaisesRegex(ValueError, "unsafe"):
                check_for_updates(root=root, download=download)

    def test_running_updater_is_replaced_last(self):
        from updater import ARCHIVE_URL, check_for_updates

        contents = {"updater.py": b"new updater", "app.py": b"new app"}
        manifest = {
            "version": "2.0",
            "files": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, value in contents.items():
                bundle.writestr(f"repo-main/{name}", value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "updater.py").write_bytes(b"old updater")
            (root / "app.py").write_bytes(b"old app")
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )
            destinations = []

            def recording_replace(source, destination):
                destinations.append(Path(destination).name)
                Path(destination).write_bytes(Path(source).read_bytes())
                Path(source).unlink()

            def download(url, timeout):
                return archive.getvalue() if url == ARCHIVE_URL else json.dumps(manifest).encode()

            check_for_updates(root=root, download=download, replace=recording_replace)

        self.assertEqual(destinations[-2:], ["updater.py", "software_manifest.json"])

    def test_release_manifest_hashes_current_files_and_excludes_state(self):
        from updater import _is_protected

        self.assertTrue(_is_protected(Path("task.xml")))
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("updater.py", manifest["files"])
        self.assertIn("run.bat", manifest["files"])
        for name, expected_hash in manifest["files"].items():
            self.assertFalse(_is_protected(Path(name)), name)
            self.assertEqual(
                hashlib.sha256((root / name).read_bytes()).hexdigest(), expected_hash, name
            )


if __name__ == "__main__":
    unittest.main()
