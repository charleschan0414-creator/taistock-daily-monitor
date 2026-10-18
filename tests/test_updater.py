import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


COMMIT = "a" * 40


def release_download(manifest, archive=b"", requested=None, commit=COMMIT):
    from updater import ARCHIVE_URL_TEMPLATE, COMMITS_URL, MANIFEST_URL_TEMPLATE

    def download(url, timeout):
        if requested is not None:
            requested.append(url)
        if url == COMMITS_URL:
            return json.dumps({"sha": commit}).encode("utf-8")
        if url == MANIFEST_URL_TEMPLATE.format(commit=commit):
            return json.dumps(manifest).encode("utf-8")
        if url == ARCHIVE_URL_TEMPLATE.format(commit=commit):
            return archive
        raise AssertionError(f"unexpected URL: {url}")

    return download


class UpdaterTests(unittest.TestCase):
    def test_manifest_and_archive_use_the_same_immutable_commit(self):
        from updater import COMMITS_URL, check_for_updates

        commit = "a" * 40
        content = b"print('new')\n"
        manifest = {
            "version": "2.0",
            "files": {"app.py": hashlib.sha256(content).hexdigest()},
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"repo-{commit}/app.py", content)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )
            requested = []

            def download(url, timeout):
                requested.append(url)
                if url == COMMITS_URL:
                    return json.dumps({"sha": commit}).encode()
                if url.endswith("/software_manifest.json"):
                    return json.dumps(manifest).encode()
                return archive.getvalue()

            result = check_for_updates(root=root, download=download)

        self.assertEqual(result.status, "updated")
        self.assertEqual(
            requested,
            [
                COMMITS_URL,
                f"https://raw.githubusercontent.com/charleschan0414-creator/"
                f"taistock-daily-monitor/{commit}/software_manifest.json",
                f"https://github.com/charleschan0414-creator/taistock-daily-monitor/"
                f"archive/{commit}.zip",
            ],
        )
        self.assertNotIn("refs/heads/main", " ".join(requested))

    def test_commit_sha_must_be_exactly_40_lowercase_hex_characters(self):
        from updater import COMMITS_URL, check_for_updates

        invalid_values = [
            "a" * 39,
            "a" * 41,
            "A" * 40,
            "g" * 40,
            "../refs/heads/main",
            "a" * 40 + "/main",
        ]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                requested = []

                def download(url, timeout):
                    requested.append(url)
                    return json.dumps({"sha": invalid}).encode()

                with self.assertRaisesRegex(ValueError, "commit SHA"):
                    check_for_updates(root=directory, download=download)
                self.assertEqual(requested, [COMMITS_URL])

    def test_same_version_does_not_download_archive(self):
        from updater import check_for_updates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"version": "1.0", "files": {}}
            (root / "software_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            requested = []

            result = check_for_updates(
                root=root, download=release_download(manifest, requested=requested)
            )

        self.assertEqual(result.status, "current")
        self.assertEqual(len(requested), 2)
        self.assertTrue(requested[1].endswith(f"/{COMMIT}/software_manifest.json"))

    def test_stale_older_commit_cannot_roll_back_installed_version(self):
        from updater import check_for_updates

        remote = {"version": "2026.10.16.2", "files": {"app.py": "0" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "2026.10.16.3", "files": {}}), encoding="utf-8"
            )
            requested = []

            result = check_for_updates(
                root=root, download=release_download(remote, requested=requested)
            )

        self.assertEqual(result.status, "current")
        self.assertEqual(result.version, "2026.10.16.3")
        self.assertEqual(len(requested), 2)

    def test_check_only_reports_update_without_downloading_archive(self):
        from updater import check_for_updates

        remote = {"version": "2.0", "files": {"app.py": "0" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "software_manifest.json").write_text(
                json.dumps({"version": "1.0", "files": {}}), encoding="utf-8"
            )
            requested = []

            result = check_for_updates(
                root=root,
                download=release_download(remote, requested=requested),
                check_only=True,
            )

        self.assertEqual(result.status, "available")
        self.assertEqual(result.version, "2.0")
        self.assertEqual(len(requested), 2)

    def test_changed_version_installs_only_verified_manifest_files(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            result = check_for_updates(root=root, download=download)

            self.assertEqual(result.status, "updated")
            self.assertEqual((root / "app.py").read_bytes(), new_app)
            self.assertEqual((root / "data" / "scanner.db").read_bytes(), b"user database")
            self.assertFalse((root / "not-listed.py").exists())
            saved = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], "2.0")

    def test_manifest_cannot_write_protected_user_state(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            with self.assertRaisesRegex(ValueError, "protected"):
                check_for_updates(root=root, download=download)
            self.assertEqual(current.read_bytes(), b"user data")

    def test_hash_mismatch_keeps_installed_file_and_version(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            with self.assertRaisesRegex(ValueError, "SHA256"):
                check_for_updates(root=root, download=download)
            self.assertEqual(app.read_bytes(), b"working version")
            saved = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], "1.0")

    def test_install_failure_rolls_back_every_changed_file(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            with self.assertRaisesRegex(OSError, "disk failure"):
                check_for_updates(root=root, download=download, replace=flaky_replace)
            self.assertEqual((root / "app.py").read_bytes(), b"old app")
            self.assertEqual((root / "taistock" / "core.py").read_bytes(), b"old core")

    def test_manifest_commit_failure_rolls_back_program_files(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

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
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            with self.assertRaisesRegex(ValueError, "unsafe"):
                check_for_updates(root=root, download=download)

    def test_running_updater_is_replaced_last(self):
        from updater import check_for_updates

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

            download = release_download(manifest, archive.getvalue())

            check_for_updates(root=root, download=download, replace=recording_replace)

        self.assertEqual(destinations[-2:], ["updater.py", "software_manifest.json"])

    def test_release_manifest_hashes_current_files_and_excludes_state(self):
        from build_software_manifest import git_blob_bytes
        from updater import _is_protected

        self.assertTrue(_is_protected(Path("task.xml")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "core.autocrlf", "true"], cwd=root, check=True
            )
            (root / "sample.bat").write_bytes(b"@echo off\r\necho test\r\n")

            content = git_blob_bytes(root, "sample.bat")

        self.assertEqual(content, b"@echo off\necho test\n")

        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "software_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "2026.10.19.2")
        self.assertIn("updater.py", manifest["files"])
        self.assertIn("taistock/strategy_freeze.py", manifest["files"])
        self.assertIn("taistock/market_history.py", manifest["files"])
        self.assertIn("run.bat", manifest["files"])
        self.assertIn("build_software_manifest.py", manifest["files"])
        for name, expected_hash in manifest["files"].items():
            self.assertFalse(_is_protected(Path(name)), name)
            self.assertEqual(
                hashlib.sha256(git_blob_bytes(root, name)).hexdigest(),
                expected_hash,
                name,
            )


if __name__ == "__main__":
    unittest.main()
