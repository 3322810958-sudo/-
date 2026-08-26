from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Mapping


if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    APP_DIR = BUNDLE_DIR / "app"
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = APP_DIR.parent


def _default_runtime_pointer(environment: Mapping[str, str] | None = None) -> Path | None:
    values = environment if environment is not None else os.environ
    base = str(values.get("LOCALAPPDATA") or values.get("APPDATA") or "").strip()
    return Path(base).resolve() / "YanxiangExpenseV2" / "runtime-home.txt" if base else None


def _has_business_database(home: Path) -> bool:
    try:
        return (home / "data" / "yanxiang_expense.db").is_file()
    except OSError:
        return False


def _database_score(home: Path) -> tuple[int, int, int, int, int, float] | None:
    database = home / "data" / "yanxiang_expense.db"
    if not database.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2)
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "members", "invoices"}.issubset(tables):
            return None
        non_demo = int(conn.execute("SELECT COUNT(*) FROM invoices WHERE COALESCE(is_demo,0)=0 AND deleted_at IS NULL").fetchone()[0])
        custom_members = int(conn.execute(
            "SELECT COUNT(*) FROM members WHERE deleted_at IS NULL AND name NOT GLOB '成员[0-9][0-9]'"
        ).fetchone()[0])
        custom_users = int(conn.execute(
            "SELECT COUNT(*) FROM users WHERE deleted_at IS NULL AND username NOT IN ('admin','viewer','member01','member02','member03','member04','member05','member06','member07','member08')"
        ).fetchone()[0])
        total_members = int(conn.execute("SELECT COUNT(*) FROM members WHERE deleted_at IS NULL").fetchone()[0])
        total_invoices = int(conn.execute("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL").fetchone()[0])
        customized = int(bool(non_demo or custom_members or custom_users or total_members != 8))
        return customized, non_demo, custom_members, custom_users, total_invoices, database.stat().st_mtime
    except (OSError, sqlite3.Error):
        return None
    finally:
        if "conn" in locals():
            conn.close()


def _copy_runtime_state(source: Path, destination: Path) -> bool:
    """Copy portable data into a stable Windows data directory atomically."""
    if source.resolve() == destination.resolve():
        return _has_business_database(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="runtime-migrate-", dir=destination.parent))
    try:
        source_data = source / "data"
        target_data = temporary / "data"

        def ignore_database(path: str, names: list[str]) -> set[str]:
            if Path(path).resolve() != source_data.resolve():
                return set()
            return {name for name in names if name in {"yanxiang_expense.db", "yanxiang_expense.db-wal", "yanxiang_expense.db-shm"}}

        if source_data.is_dir():
            shutil.copytree(source_data, target_data, dirs_exist_ok=True, ignore=ignore_database)
        target_data.mkdir(parents=True, exist_ok=True)
        source_db = sqlite3.connect(f"{(source_data / 'yanxiang_expense.db').as_uri()}?mode=ro", uri=True, timeout=30)
        target_db = sqlite3.connect(str(target_data / "yanxiang_expense.db"))
        try:
            source_db.backup(target_db)
        finally:
            target_db.close()
            source_db.close()
        for name in ("uploads", "models", "backups"):
            item = source / name
            if item.is_dir():
                shutil.copytree(item, temporary / name, dirs_exist_ok=True)
        if (source / ".env").is_file():
            shutil.copy2(source / ".env", temporary / ".env")
        if _database_score(temporary) is None:
            raise RuntimeError("迁移后的数据库校验失败")
        if destination.exists():
            incomplete = destination.with_name(f"{destination.name}-incomplete-{os.getpid()}")
            destination.replace(incomplete)
        temporary.replace(destination)
        return True
    except (OSError, sqlite3.Error, RuntimeError):
        shutil.rmtree(temporary, ignore_errors=True)
        return False


def _nearby_runtime_homes(program_dir: Path) -> list[Path]:
    """Find an older portable installation next to a newly extracted folder."""
    candidates: dict[str, Path] = {}
    try:
        parent = program_dir.parent
        patterns = ("data/yanxiang_expense.db", "*/data/yanxiang_expense.db", "*/*/data/yanxiang_expense.db")
        for pattern in patterns:
            for database in parent.glob(pattern):
                home = database.parent.parent.resolve()
                if home != program_dir and _has_business_database(home):
                    candidates[str(home).casefold()] = home
    except OSError:
        return []
    return sorted(
        candidates.values(),
        key=lambda home: (home / "data" / "yanxiang_expense.db").stat().st_mtime,
        reverse=True,
    )


def resolve_runtime_home(
    program_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    packaged_windows: bool | None = None,
    pointer_path: Path | None = None,
) -> Path:
    """Resolve the persistent data home independently from the program folder."""
    values = environment if environment is not None else os.environ
    explicit = str(values.get("YXRT_HOME") or "").strip()
    if explicit:
        return Path(explicit).resolve()
    packaged = bool(getattr(sys, "frozen", False) and os.name == "nt") if packaged_windows is None else packaged_windows
    current = program_dir.resolve()
    if not packaged:
        return current
    marker = pointer_path if pointer_path is not None else _default_runtime_pointer(values)
    stable = (marker.parent / "runtime").resolve() if marker is not None else current
    if _database_score(stable) is not None:
        return stable
    remembered: Path | None = None
    if marker and marker.is_file():
        try:
            candidate = Path(marker.read_text(encoding="utf-8-sig").strip()).resolve()
            if _database_score(candidate) is not None:
                remembered = candidate
        except (OSError, ValueError):
            pass
    current_score = _database_score(current)
    source: Path | None
    if remembered is not None:
        source = remembered
    elif current_score is not None:
        # Any valid database beside the executable is the user's explicit
        # working copy. Never replace it merely because a sibling backup has
        # more rows; nearby discovery is only a fallback when current is absent.
        source = current
    else:
        candidates = [current, *_nearby_runtime_homes(current)]
        unique = {str(item.resolve()).casefold(): item.resolve() for item in candidates}
        scored = [(score, item) for item in unique.values() if (score := _database_score(item)) is not None]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        source = scored[0][1] if scored else None
    if source is not None and stable != current and _copy_runtime_state(source, stable):
        return stable
    return source or stable


def remember_runtime_home(home: Path, pointer_path: Path | None = None) -> None:
    if not (getattr(sys, "frozen", False) and os.name == "nt"):
        return
    marker = pointer_path if pointer_path is not None else _default_runtime_pointer()
    if marker is None:
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(str(home.resolve()), encoding="utf-8")
        temporary.replace(marker)
    except OSError:
        pass


PROGRAM_DIR = PROJECT_DIR
RUNTIME_HOME = resolve_runtime_home(PROJECT_DIR)
remember_runtime_home(RUNTIME_HOME)
DATA_DIR = Path(os.environ.get("YXRT_DATA_DIR", RUNTIME_HOME / "data")).resolve()
UPLOAD_DIR = Path(os.environ.get("YXRT_UPLOAD_DIR", RUNTIME_HOME / "uploads")).resolve()
MODEL_DIR = Path(os.environ.get("YXRT_MODEL_DIR", RUNTIME_HOME / "models")).resolve()
TMP_DIR = Path(os.environ.get("YXRT_TMP_DIR", RUNTIME_HOME / "tmp")).resolve()
DB_PATH = Path(os.environ.get("YXRT_DB_PATH", DATA_DIR / "yanxiang_expense.db")).resolve()

HOST = os.environ.get("YXRT_HOST", "127.0.0.1")
PORT = int(os.environ.get("YXRT_PORT", "8765"))
APP_MODE = os.environ.get("YXRT_MODE", "desktop").strip().lower()
COOKIE_SECURE = os.environ.get("YXRT_COOKIE_SECURE", "0") == "1"
SESSION_HOURS = int(os.environ.get("YXRT_SESSION_HOURS", "12"))
SYNC_INTERVAL_SECONDS = max(15, int(os.environ.get("YXRT_SYNC_INTERVAL", "60")))
CPU_COUNT = max(1, os.cpu_count() or 4)
DEFAULT_OCR_WORKERS = 1 if CPU_COUNT <= 4 else (2 if CPU_COUNT <= 12 else 3)
CONFIGURED_OCR_WORKERS = int(os.environ.get("YXRT_OCR_WORKERS", "0"))
OCR_WORKERS = DEFAULT_OCR_WORKERS if CONFIGURED_OCR_WORKERS <= 0 else max(1, min(4, CONFIGURED_OCR_WORKERS))
DEFAULT_OCR_CPU_THREADS = max(2, CPU_COUNT // OCR_WORKERS)
CONFIGURED_OCR_CPU_THREADS = int(os.environ.get("YXRT_OCR_CPU_THREADS", "0"))
OCR_CPU_THREADS = max(2, min(10, DEFAULT_OCR_CPU_THREADS if CONFIGURED_OCR_CPU_THREADS <= 0 else CONFIGURED_OCR_CPU_THREADS))
OCR_DETECTION_MAX_SIDE = max(960, min(4000, int(os.environ.get("YXRT_OCR_MAX_SIDE", "2200"))))
OCR_TIMEOUT_SECONDS = max(30, min(300, int(os.environ.get("YXRT_OCR_TIMEOUT", "90"))))
DEVICE_LABEL = os.environ.get("YXRT_DEVICE_LABEL", "Windows 本地端" if APP_MODE == "desktop" else "云端服务器")

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".ofd", ".txt"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_DOCUMENT_EXTENSIONS
APPEARANCE_IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | {".gif"}
APPEARANCE_VIDEO_EXTENSIONS = {".mp4", ".webm", ".m4v"}
APPEARANCE_MEDIA_EXTENSIONS = APPEARANCE_IMAGE_EXTENSIONS | APPEARANCE_VIDEO_EXTENSIONS

for directory in (DATA_DIR, UPLOAD_DIR, MODEL_DIR, TMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)
