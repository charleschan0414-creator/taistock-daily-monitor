import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsRunnerTests(unittest.TestCase):
    def test_all_windows_batch_files_use_crlf_for_cmd_compatibility(self):
        batch_files = sorted(ROOT.glob("*.bat"))
        self.assertTrue(batch_files, "No Windows batch files found")
        for path in batch_files:
            content = path.read_bytes()
            with self.subTest(name=path.name):
                self.assertNotIn(b"\n", content.replace(b"\r\n", b""),
                                 f"{path.name} contains LF-only line endings")
                self.assertIn(b"\r\n", content, f"{path.name} contains no CRLF line endings")

    @unittest.skipUnless(os.name == "nt", "Windows batch runner only")
    def test_runner_uses_saved_absolute_python_when_path_is_restricted(self):
        saved = ROOT / "python_path.txt"
        saved.write_text(sys.executable, encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
        completed = subprocess.run(
            [str(ROOT / "run.bat"), "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="mbcs",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
        self.assertIn("--update", completed.stdout)


if __name__ == "__main__":
    unittest.main()
