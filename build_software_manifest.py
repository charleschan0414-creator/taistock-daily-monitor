"""Build the desktop updater manifest from Git-cleaned blob bytes."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


RELEASE_FILES = (
    "README.md",
    "app.py",
    "build_software_manifest.py",
    "daily_pipeline.py",
    "install.bat",
    "install_daily_task.bat",
    "mobile_server.py",
    "publish_public_data.py",
    "requirements.txt",
    "run.bat",
    "start_mobile.bat",
    "start_remote_mobile.bat",
    "strategy_campaign.json",
    "uninstall.bat",
    "uninstall_daily_task.bat",
    "updater.py",
    "taistock/__init__.py",
    "taistock/backup.py",
    "taistock/backtest.py",
    "taistock/core.py",
    "taistock/data.py",
    "taistock/global_market.py",
    "taistock/health.py",
    "taistock/market_history.py",
    "taistock/mobile.py",
    "taistock/models.py",
    "taistock/net.py",
    "taistock/notify.py",
    "taistock/portfolio.py",
    "taistock/realtime.py",
    "taistock/scanner.py",
    "taistock/storage.py",
    "taistock/strategy_campaign.py",
    "taistock/technical.py",
    "taistock/strategy_freeze.py",
    "taistock/strategy_optimize.py",
    "taistock/v2_optimize.py",
    "taistock/ui.py",
    "mobile/icon.svg",
    "mobile/index.html",
    "mobile/manifest.json",
    "mobile/service-worker.js",
)


def git_blob_bytes(root, relative_name):
    """Return bytes after Git clean filters, exactly as an archive stores them."""
    root = Path(root).resolve()
    relative_name = str(relative_name).replace("\\", "/")
    blob_id = subprocess.run(
        ["git", "hash-object", "-w", f"--path={relative_name}", "--", relative_name],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return subprocess.run(
        ["git", "cat-file", "blob", blob_id],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def build_manifest(root, version):
    root = Path(root).resolve()
    files = {}
    for relative_name in RELEASE_FILES:
        content = git_blob_bytes(root, relative_name)
        files[relative_name] = hashlib.sha256(content).hexdigest()
    return {"version": str(version), "files": files}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate software_manifest.json from Git archive-equivalent bytes"
    )
    parser.add_argument("version", help="release version, for example 2026.10.16.3")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.root, args.version)
    destination = args.root / "software_manifest.json"
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {destination} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
