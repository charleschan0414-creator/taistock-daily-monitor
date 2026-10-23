import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_script(name):
    return (ROOT / name).read_text(encoding="utf-8")


class WindowsInstallerTests(unittest.TestCase):
    def test_install_is_single_repair_entrypoint_and_validates_python(self):
        script = read_script("install.bat")
        lowered = script.lower()
        self.assertIn('if /i "%~1"=="--repair"', lowered)
        self.assertIn("py -3.11", lowered)
        self.assertIn("python", lowered)
        self.assertIn("import tkinter", lowered)
        self.assertIn("sys.executable", lowered)
        self.assertIn("python_path.txt", lowered)
        self.assertRegex(lowered, r'exit /b [1-9]')

    def test_install_dry_run_gate_precedes_all_side_effects(self):
        script = read_script("install.bat")
        lowered = script.lower()
        gate = lowered.index('if /i "%~1"=="--dry-run"')
        self.assertGreater(lowered.index("python_path.txt"), gate)
        self.assertGreater(lowered.index("schtasks"), gate)
        self.assertGreater(lowered.index(".lnk"), gate)
        before_gate = lowered[:gate]
        self.assertNotRegex(before_gate, r"(^|\n)\s*>+")
        self.assertNotIn("/create", before_gate)
        self.assertIn("taistockscannerdaily", before_gate)
        self.assertIn("06:30", before_gate)
        self.assertIn("taistockscannersoftwareupdate", before_gate)
        self.assertIn("every 2 hours", lowered[:gate])
        self.assertIn("data", lowered[:gate])
        self.assertIn("public_data", lowered[:gate])
        self.assertIn("backups", lowered[:gate])
        self.assertIn("diagnostics", lowered[:gate])
        self.assertIn("user_settings", lowered[:gate])

    def test_install_creates_daily_and_two_hour_update_tasks(self):
        script = read_script("install.bat")
        lowered = script.lower()
        self.assertIn("taistockscannerdaily", lowered)
        self.assertIn("run.bat", lowered)
        self.assertIn("--update --notify", lowered)
        self.assertIn("/sc daily", lowered)
        self.assertIn("/st 06:30", lowered)
        self.assertIn("taistockscannersoftwareupdate", lowered)
        self.assertIn("updater.py", lowered)
        self.assertIn("--check-only", lowered)
        self.assertIn("/sc hourly", lowered)
        self.assertIn("/mo 2", lowered)
        self.assertIn("/st 00:00", lowered)
        self.assertNotIn("$t.repetition.interval", lowered)
        self.assertIn("startwhenavailable", lowered)
        self.assertRegex(
            lowered,
            r"set-scheduledtask\s+-taskname\s+'taistockscannerdaily'\s+-settings\s+\(new-scheduledtasksettingsset\s+-startwhenavailable\)",
        )
        self.assertRegex(
            lowered,
            r"set-scheduledtask\s+-taskname\s+'taistockscannersoftwareupdate'\s+-settings\s+\(new-scheduledtasksettingsset\s+-startwhenavailable\)",
        )
        self.assertIn("interactivetoken", lowered)
        self.assertNotRegex(lowered, r"/rp\s+")

    def test_install_creates_both_shortcuts_with_safe_working_directory(self):
        script = read_script("install.bat")
        lowered = script.lower()
        self.assertIn("desktop", lowered)
        self.assertIn("start menu", lowered)
        self.assertIn("0x53f0,0x80a1,0x6bcf,0x65e5,0x89c0,0x5bdf", lowered)
        self.assertIn("wscript.shell", lowered)
        self.assertIn("targetpath", lowered)
        self.assertIn("workingdirectory", lowered)
        self.assertIn("run.bat", lowered)

    def test_uninstall_only_removes_tasks_and_shortcuts_and_preserves_data(self):
        script = read_script("uninstall.bat")
        lowered = script.lower()
        gate = lowered.index('if /i "%~1"=="--dry-run"')
        self.assertGreater(lowered.index("schtasks"), gate)
        self.assertGreater(lowered.index("powershell"), gate)
        self.assertIn("taistockscannerdaily", lowered)
        self.assertIn("taistockscannersoftwareupdate", lowered)
        for preserved in (
            "data", "public_data", "backups", "diagnostics", "user_settings", "python_path"
        ):
            self.assertIn(preserved, lowered)
        self.assertNotRegex(lowered, r"\b(?:rd|rmdir)\b")
        self.assertNotRegex(lowered, r"\bdel\s+[^\r\n]*(?:data|public_data|backups|diagnostics|user_settings|python_path)")
        self.assertNotIn("%~dp0*", lowered)

    def test_legacy_installer_delegates_to_single_entrypoint(self):
        script = read_script("install_daily_task.bat").lower()
        self.assertIn("install.bat", script)
        self.assertIn("%*", script)

    def test_release_manifest_includes_installers_with_current_hashes(self):
        from build_software_manifest import RELEASE_FILES, git_blob_bytes
        import hashlib

        self.assertIn("install.bat", RELEASE_FILES)
        self.assertIn("uninstall.bat", RELEASE_FILES)
        self.assertIn("taistock/broker_risk.py", RELEASE_FILES)
        self.assertIn("taistock/day_trading.py", RELEASE_FILES)
        manifest = json.loads((ROOT / "software_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(RELEASE_FILES), 45)
        self.assertEqual(len(manifest["files"]), len(RELEASE_FILES))
        for name in ("install.bat", "uninstall.bat", "install_daily_task.bat"):
            self.assertIn(name, manifest["files"])
            self.assertEqual(
                hashlib.sha256(git_blob_bytes(ROOT, name)).hexdigest(),
                manifest["files"][name],
            )

    def test_readme_documents_beginner_install_repair_and_safe_removal(self):
        readme = read_script("README.md")
        self.assertIn("雙擊 `install.bat`", readme)
        self.assertIn("install.bat --repair", readme)
        self.assertIn("uninstall.bat", readme)
        self.assertIn("TaiStockScannerSoftwareUpdate", readme)
        self.assertIn("每 2 小時", readme)
        self.assertIn("錯過", readme)
        self.assertIn("不需要密碼", readme)
        self.assertIn("公開雲端版不需要登入", readme)
        for preserved in ("data/", "public_data/", "backups/", "diagnostics/", "user_settings", "python_path"):
            self.assertIn(preserved, readme)


if __name__ == "__main__":
    unittest.main()
