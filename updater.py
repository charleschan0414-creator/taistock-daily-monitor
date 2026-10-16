"""Safe updater for the fixed TaiStockScanner GitHub source."""

from dataclasses import dataclass
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile


COMMITS_URL = (
    "https://api.github.com/repos/charleschan0414-creator/"
    "taistock-daily-monitor/commits/main"
)
MANIFEST_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/charleschan0414-creator/"
    "taistock-daily-monitor/{commit}/software_manifest.json"
)
ARCHIVE_URL_TEMPLATE = (
    "https://github.com/charleschan0414-creator/taistock-daily-monitor/"
    "archive/{commit}.zip"
)
USER_AGENT = "TaiStockScanner-Updater/1.0"
PROTECTED_DIRS = {
    ".git",
    ".wrangler",
    "__pycache__",
    "backtest_cache",
    "cache",
    "data",
    "node_modules",
    "public_data",
}
PROTECTED_NAMES = {
    ".env",
    "python_path.txt",
    "scanner.db",
    "settings.json",
    "task.xml",
    "user_config.json",
}
SECRET_SUFFIXES = {".cer", ".crt", ".key", ".p12", ".pem", ".pfx"}


def _is_protected(path):
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = lowered_parts[-1] if lowered_parts else ""
    if any(part in PROTECTED_DIRS for part in lowered_parts):
        return True
    if name in PROTECTED_NAMES or Path(name).suffix.lower() in SECRET_SUFFIXES:
        return True
    return any(word in name for word in ("credential", "secret", "token", "cookie"))


@dataclass(frozen=True)
class UpdateResult:
    status: str
    version: str
    message: str


def _download(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _version_key(version):
    version = str(version)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is None:
        raise ValueError(f"invalid release version: {version}")
    return tuple(int(part) for part in version.split("."))


def check_for_updates(root=None, download=None, timeout=5, replace=None, check_only=False):
    root = Path(root or Path(__file__).resolve().parent)
    download = download or _download
    replace = replace or os.replace
    commit = str(json.loads(download(COMMITS_URL, timeout).decode("utf-8"))["sha"])
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("invalid GitHub commit SHA")
    manifest_url = MANIFEST_URL_TEMPLATE.format(commit=commit)
    archive_url = ARCHIVE_URL_TEMPLATE.format(commit=commit)
    remote = json.loads(download(manifest_url, timeout).decode("utf-8"))
    local_path = root / "software_manifest.json"
    local = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
    version = str(remote["version"])
    local_version = str(local.get("version", ""))
    if local_version == version:
        return UpdateResult("current", version, "已是最新版")
    if local_version and _version_key(version) < _version_key(local_version):
        return UpdateResult("current", local_version, "已安裝較新版本")
    if check_only:
        return UpdateResult("available", version, f"有新版本 {version}")

    files = remote.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest files must be a non-empty object")
    archive_bytes = download(archive_url, timeout)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle, tempfile.TemporaryDirectory(
        prefix="taistock-update-", dir=root
    ) as staging_directory:
        staging = Path(staging_directory)
        names = bundle.namelist()
        for relative_name, expected_hash in files.items():
            path = PurePosixPath(relative_name)
            if (
                not isinstance(relative_name, str)
                or not relative_name
                or "\\" in relative_name
                or ":" in relative_name
                or path.is_absolute()
                or ".." in path.parts
                or "." in path.parts
            ):
                raise ValueError(f"unsafe manifest path: {relative_name}")
            if _is_protected(path):
                raise ValueError(f"protected path in manifest: {relative_name}")
            matches = [name for name in names if PurePosixPath(name).parts[1:] == path.parts]
            if len(matches) != 1:
                raise ValueError(f"archive file missing or ambiguous: {relative_name}")
            content = bundle.read(matches[0])
            if hashlib.sha256(content).hexdigest() != str(expected_hash).lower():
                raise ValueError(f"SHA256 mismatch: {relative_name}")
            destination = staging.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

        install_order = sorted(files, key=lambda name: (name == "updater.py", name))
        backups = staging / ".backups"
        existed = {}
        installed = []
        for relative_name in install_order:
            path = PurePosixPath(relative_name)
            destination = root.joinpath(*path.parts)
            existed[relative_name] = destination.exists()
            if destination.exists():
                backup = backups.joinpath(*path.parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(destination.read_bytes())

        try:
            for relative_name in install_order:
                path = PurePosixPath(relative_name)
                source = staging.joinpath(*path.parts)
                destination = root.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                replace(source, destination)
                installed.append(relative_name)
            manifest_temp = root / ".software_manifest.json.tmp"
            manifest_temp.write_text(
                json.dumps(remote, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            replace(manifest_temp, local_path)
        except Exception:
            for relative_name in reversed(installed):
                path = PurePosixPath(relative_name)
                destination = root.joinpath(*path.parts)
                if existed[relative_name]:
                    replace(backups.joinpath(*path.parts), destination)
                elif destination.exists():
                    destination.unlink()
            raise

    return UpdateResult("updated", version, f"已更新至 {version}")


def main(argv=None, runner=None):
    parser = argparse.ArgumentParser(description="TaiStockScanner 安全自動更新器")
    parser.add_argument("--check-only", action="store_true", help="只檢查，不下載或安裝")
    args = parser.parse_args(argv)
    runner = runner or check_for_updates
    try:
        result = runner(check_only=args.check_only)
    except Exception as exc:
        print(f"[WARN] 更新檢查失敗：{exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
