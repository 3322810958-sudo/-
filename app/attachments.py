from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import UploadFile

from .config import SUPPORTED_EXTENSIONS, TMP_DIR, UPLOAD_DIR
from .database import audit, current_season_id, enqueue_sync_event, get_device_id, new_id, transaction, utc_now


def attachment_path(stored_name: str) -> Path:
    safe = Path(stored_name).name
    return UPLOAD_DIR / safe[:2] / safe


def find_blob(sha256: str) -> Path | None:
    prefix = UPLOAD_DIR / sha256[:2]
    if not prefix.exists():
        return None
    matches = list(prefix.glob(f"{sha256}.*")) + list(prefix.glob(sha256))
    return matches[0] if matches else None


def _save_stream(stream: BinaryIO, original_name: str) -> tuple[Path, str, int, str]:
    suffix = Path(original_name).suffix.lower()[:10]
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_upload_", suffix=suffix, dir=TMP_DIR)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
        checksum = digest.hexdigest()
        stored_name = checksum + suffix
        destination = attachment_path(stored_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            Path(temp_name).unlink(missing_ok=True)
        else:
            os.replace(temp_name, destination)
        return destination, stored_name, size, checksum
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


async def save_upload(upload: UploadFile, user: dict[str, Any]) -> dict[str, Any]:
    original_name = Path(upload.filename or "attachment").name
    destination, stored_name, size, checksum = _save_stream(upload.file, original_name)
    mime_type = upload.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    return register_attachment(original_name, stored_name, size, checksum, mime_type, user)


def save_file(path: Path, original_name: str, user: dict[str, Any]) -> dict[str, Any]:
    with path.open("rb") as stream:
        _, stored_name, size, checksum = _save_stream(stream, original_name)
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    return register_attachment(original_name, stored_name, size, checksum, mime_type, user)


def register_attachment(
    original_name: str,
    stored_name: str,
    size: int,
    checksum: str,
    mime_type: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    with transaction() as conn:
        season_id = current_season_id(conn)
        existing = conn.execute(
            "SELECT * FROM attachments WHERE sha256=? AND season_id=? AND deleted_at IS NULL", (checksum, season_id)
        ).fetchone()
        if existing:
            return dict(existing)
        now = utc_now()
        row = {
            "id": new_id("attachment"), "season_id": season_id,
            "original_name": original_name[:255], "stored_name": stored_name,
            "mime_type": mime_type[:120], "size_bytes": size, "sha256": checksum,
            "uploaded_by": user["id"], "created_at": now, "updated_at": now,
            "version": 1, "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(
            f"INSERT INTO attachments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        enqueue_sync_event(conn, "attachments", row["id"], "upsert", row, checksum)
        audit(conn, user["id"], "upload", "attachment", row["id"], {"name": original_name, "size": size})
        return row


def extract_zip(upload_path: Path, user: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    with zipfile.ZipFile(upload_path) as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        max_entries = max(1, int(os.environ.get("YXRT_ZIP_MAX_ENTRIES", "10000")))
        max_total = max(1024 * 1024, int(os.environ.get("YXRT_ZIP_MAX_UNCOMPRESSED_BYTES", str(50 * 1024**3))))
        if len(entries) > max_entries:
            raise ValueError(f"压缩包文件数量超过本机安全上限 {max_entries}")
        if sum(max(0, int(info.file_size)) for info in entries) > max_total:
            raise ValueError("压缩包解压后体积超过本机安全上限，可分批导入或调整配置")
        for info in entries:
            if info.is_dir():
                continue
            filename = Path(info.filename.replace("\\", "/")).name
            extension = Path(filename).suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                skipped.append({"file_name": filename, "reason": "不是支持的发票文件格式"})
                continue
            if info.compress_size > 0 and info.file_size / info.compress_size > 2000:
                skipped.append({"file_name": filename, "reason": "压缩比异常，已按安全规则跳过"})
                continue
            if info.flag_bits & 0x1:
                skipped.append({"file_name": filename, "reason": "加密压缩文件无法自动读取"})
                continue
            fd, temp_name = tempfile.mkstemp(prefix="yxrt_zip_", suffix=extension, dir=TMP_DIR)
            os.close(fd)
            try:
                with archive.open(info) as source, open(temp_name, "wb") as target:
                    shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
                imported.append(save_file(Path(temp_name), filename, user))
            except Exception as exc:
                skipped.append({"file_name": filename, "reason": str(exc)})
            finally:
                Path(temp_name).unlink(missing_ok=True)
    return imported, skipped
