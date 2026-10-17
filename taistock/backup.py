"""Create and restore small, verified backups of user-owned state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath


BACKUP_WHITELIST = (
    "data/scanner.db",
    "data/strategy_history.jsonl",
    "data/strategy_registry.json",
    "data/global_market_history.jsonl",
    "data/market_regime.json",
    "data/latest.json",
    "data/daily_status.json",
    "user_settings.json",
)
MAX_TOTAL_SIZE = 100 * 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_FILE_COUNT = 20


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now():
    return datetime.now(timezone.utc)


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _release_version(root):
    try:
        version = json.loads((Path(root) / "software_manifest.json").read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return "unknown"
    return str(version)


def create_backup(root, destination=None, app_version=None):
    """Atomically create a ZIP containing only existing allowlisted state."""
    root = Path(root).resolve()
    if app_version is None:
        app_version = _release_version(root)
    now = _utc_now()
    if destination is None:
        destination = root / "backups" / f"TaiStockScanner-backup-{now:%Y%m%d-%H%M%S}.zip"
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    sources = []
    total_size = 0
    for relative in BACKUP_WHITELIST:
        source = root / relative
        if source.is_symlink():
            raise ValueError(f"備份來源不得為 symlink：{relative}")
        if source.is_file():
            size = source.stat().st_size
            if size > MAX_FILE_SIZE:
                raise ValueError(f"備份來源單檔超過大小上限：{relative}")
            total_size += size
            if total_size > MAX_TOTAL_SIZE:
                raise ValueError("備份來源總大小超過上限")
            sources.append((relative, source))
    files = {}
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            actual_total = 0
            for relative, source_path in sources:
                digest = hashlib.sha256()
                size = 0
                with source_path.open("rb") as source, archive.open(relative, "w") as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        actual_total += len(chunk)
                        if size > MAX_FILE_SIZE or actual_total > MAX_TOTAL_SIZE:
                            raise ValueError("備份來源在讀取時超過大小上限")
                        digest.update(chunk)
                        output.write(chunk)
                files[relative] = {"sha256": digest.hexdigest(), "size": size}
            manifest = {
                "schema_version": 1,
                "created_at": _iso_utc(now),
                "app_version": str(app_version),
                "files": files,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(temporary) as archive:
            bad_file = archive.testzip()
            if bad_file is not None:
                raise ValueError(f"備份 ZIP 驗證失敗：{bad_file}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": destination, "file_count": len(files), "completed": True}


def _parse_created_at(value):
    if not isinstance(value, str):
        raise ValueError("manifest created_at 無效")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("manifest created_at 必須含時區")
    return parsed.astimezone(timezone.utc)


def _safe_member_name(name):
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        return False
    path = PurePosixPath(name)
    return (
        str(path) == name
        and all(part not in ("", ".", "..") for part in path.parts)
        and not (path.parts and ":" in path.parts[0])
    )


def _validate_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_FILE_COUNT:
        raise ValueError("備份 ZIP 檔案數超過上限")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("備份 ZIP 含重複路徑")
    total_size = 0
    for info in infos:
        if not _safe_member_name(info.filename):
            raise ValueError(f"備份 ZIP 含不安全路徑：{info.filename}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"備份 ZIP 不得包含 symlink：{info.filename}")
        if info.is_dir():
            raise ValueError(f"備份 ZIP 不得包含目錄項目：{info.filename}")
        if info.file_size > MAX_FILE_SIZE:
            raise ValueError(f"備份 ZIP 單檔超過大小上限：{info.filename}")
        total_size += info.file_size
    if total_size > MAX_TOTAL_SIZE:
        raise ValueError("備份 ZIP 解壓總大小超過上限")
    if "manifest.json" not in names:
        raise ValueError("備份 ZIP 缺少 manifest.json")
    bad_file = archive.testzip()
    if bad_file is not None:
        raise ValueError(f"備份 ZIP CRC 驗證失敗：{bad_file}")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("備份 manifest 不是有效 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("備份 manifest 格式不支援")
    if not isinstance(manifest.get("app_version"), str) or not isinstance(manifest.get("files"), dict):
        raise ValueError("備份 manifest 不完整")
    _parse_created_at(manifest.get("created_at"))
    files = manifest["files"]
    for relative in files:
        if not _safe_member_name(relative):
            raise ValueError(f"備份 manifest 含不安全路徑：{relative}")
    if set(names) != {"manifest.json", *files}:
        raise ValueError("備份 manifest 與 ZIP 檔案清單不完整或不一致")
    for relative, metadata in files.items():
        if relative not in BACKUP_WHITELIST:
            raise ValueError(f"備份含非白名單檔案：{relative}")
        if not isinstance(metadata, dict) or set(metadata) != {"sha256", "size"}:
            raise ValueError(f"備份 manifest 檔案資料不完整：{relative}")
        digest = metadata["sha256"]
        size = metadata["size"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ValueError(f"備份 manifest 檔案資料無效：{relative}")
        if archive.getinfo(relative).file_size != size:
            raise ValueError(f"備份檔案雜湊或大小不符：{relative}")
    return manifest


def _data_date(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("data_date")
        return date.fromisoformat(value) if isinstance(value, str) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError):
        return None


def _target_path(root, relative):
    target = root.joinpath(*PurePosixPath(relative).parts)
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError(f"還原目標路徑離開程式資料夾：{relative}") from exc
    return target


def restore_backup(root, archive_path):
    """Verify a backup, then atomically replace only its allowlisted files."""
    root = Path(root).resolve()
    archive_path = Path(archive_path).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=root) as workspace_name:
        workspace = Path(workspace_name)
        staged = workspace / "staged"
        rollback = workspace / "rollback"
        staged.mkdir()
        rollback.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            manifest = _validate_archive(archive)
            created_at = _parse_created_at(manifest["created_at"])
            files = manifest["files"]
            for relative, metadata in files.items():
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                actual_size = 0
                with archive.open(relative) as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        actual_size += len(chunk)
                        if actual_size > MAX_FILE_SIZE:
                            raise ValueError(f"備份 ZIP 單檔超過大小上限：{relative}")
                        digest.update(chunk)
                        output.write(chunk)
                if actual_size != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
                    raise ValueError(f"備份檔案雜湊或大小不符：{relative}")
        database = staged / "data/scanner.db"
        if database.exists():
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            except sqlite3.DatabaseError as exc:
                raise ValueError("SQLite integrity_check 失敗") from exc
            finally:
                connection.close()
            if not result or result[0] != "ok":
                raise ValueError("SQLite integrity_check 失敗")
        for relative in files:
            target = _target_path(root, relative)
            if target.exists() and datetime.fromtimestamp(target.stat().st_mtime, timezone.utc) > created_at:
                raise ValueError(f"目標檔案較新（晚於備份），拒絕覆蓋：{relative}")
        daily_relative = "data/daily_status.json"
        daily_target = _target_path(root, daily_relative)
        if daily_relative in files and daily_target.exists():
            current_date = _data_date(daily_target)
            backup_date = _data_date(staged / daily_relative)
            if current_date and (not backup_date or current_date > backup_date):
                raise ValueError("目前 daily_status data_date 較備份新，拒絕覆蓋")
        replaced = []
        originals = []
        try:
            for relative in files:
                target = _target_path(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    saved = rollback / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, saved)
                    originals.append(relative)
                os.replace(staged / relative, target)
                replaced.append(relative)
        except Exception:
            for relative in reversed(replaced):
                _target_path(root, relative).unlink(missing_ok=True)
            for relative in reversed(originals):
                saved = rollback / relative
                if saved.exists():
                    os.replace(saved, _target_path(root, relative))
            raise
    return {"path": archive_path, "file_count": len(files), "completed": True}
