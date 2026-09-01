from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shutil
import uuid
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener


class IntegrationError(ValueError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_secret(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise IntegrationError("当前系统不支持 Windows 本机凭据加密")
    input_blob, keepalive = _blob(value.encode("utf-8"))
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(ctypes.byref(input_blob), "YXRT", None, None, None, 0, ctypes.byref(output_blob)):
        raise IntegrationError("API 密钥本机加密失败")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del keepalive


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise IntegrationError("当前系统不支持 Windows 本机凭据解密")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise IntegrationError("保存的 API 密钥格式已损坏") from exc
    input_blob, keepalive = _blob(raw)
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)):
        raise IntegrationError("API 密钥无法在当前 Windows 账号下解密")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del keepalive


def normalize_ai_connectors(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise IntegrationError("AI 接插件列表格式不正确")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    valid_kinds = {"ollama", "localai", "openai_compatible", "custom"}
    valid_scopes = {"local", "domestic", "foreign"}
    for index, item in enumerate(raw[:20]):
        if not isinstance(item, dict):
            continue
        connector_id = "".join(ch for ch in str(item.get("id") or f"ai_{index}") if ch.isalnum() or ch in "_-")[:80]
        if not connector_id or connector_id in seen:
            raise IntegrationError("AI 接插件标识重复或不正确")
        kind = str(item.get("kind") or "ollama")
        scope = str(item.get("scope") or "local")
        if kind not in valid_kinds or scope not in valid_scopes:
            raise IntegrationError("AI 接插件类型不正确")
        base_url = str(item.get("base_url") or "").strip().rstrip("/")[:1000]
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise IntegrationError("AI 服务地址必须是有效的 HTTP/HTTPS 地址且不能包含账号密码")
        result.append({
            "id": connector_id,
            "name": str(item.get("name") or f"AI 接插件 {index + 1}").strip()[:100],
            "kind": kind,
            "scope": scope,
            "base_url": base_url,
            "model": str(item.get("model") or "").strip()[:200],
            "enabled": bool(item.get("enabled", False)),
            "priority": max(1, min(999, int(item.get("priority") or index + 1))),
        })
        seen.add(connector_id)
    return sorted(result, key=lambda item: (item["priority"], item["name"]))


def normalize_nas_config(raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    protocol = str(item.get("protocol") or "local")
    if protocol not in {"local", "smb", "webdav"}:
        raise IntegrationError("NAS 连接方式不正确")
    location = str(item.get("location") or "").strip()[:1000]
    if protocol == "webdav" and location:
        parsed = urlparse(location)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise IntegrationError("WebDAV 地址格式不正确")
    return {
        "enabled": bool(item.get("enabled", False)),
        "protocol": protocol,
        "location": location,
        "username": str(item.get("username") or "").strip()[:200],
    }


def test_ai_connection(connector: dict[str, Any], secret: str = "") -> dict[str, Any]:
    base_url = connector.get("base_url") or ""
    if not base_url:
        raise IntegrationError("请先填写 AI 服务地址")
    kind = connector.get("kind")
    endpoint = f"{base_url}/api/tags" if kind == "ollama" else f"{base_url}/v1/models"
    headers = {"Accept": "application/json", "User-Agent": "YXRT-Money-App/2.4.0"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    try:
        response = build_opener().open(Request(endpoint, headers=headers), timeout=5)
        sample = response.read(4096)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise IntegrationError("AI 服务已连接，但密钥验证失败") from exc
        raise IntegrationError(f"AI 服务返回错误：HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(f"无法连接 AI 服务：{exc}") from exc
    try:
        payload = json.loads(sample.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        payload = {}
    model_count = len(payload.get("models", [])) if isinstance(payload, dict) and isinstance(payload.get("models"), list) else 0
    return {"ok": True, "message": f"连接成功{f'，发现 {model_count} 个模型' if model_count else ''}", "model_count": model_count}


def test_nas_connection(config: dict[str, Any], password: str = "") -> dict[str, Any]:
    location = config.get("location") or ""
    if not location:
        raise IntegrationError("请先填写 NAS 地址或共享目录")
    if config.get("protocol") in {"local", "smb"}:
        path = Path(location)
        if not path.is_dir():
            raise IntegrationError("无法访问该目录，请检查路径、网络和 Windows 权限")
        return {"ok": True, "message": "目录连接成功，可由管理员手动上传完整备份和恢复"}
    token = base64.b64encode(f"{config.get('username', '')}:{password}".encode("utf-8")).decode("ascii")
    headers = {"User-Agent": "YXRT-Money-App/2.4.0"}
    if config.get("username") or password:
        headers["Authorization"] = f"Basic {token}"
    try:
        response = build_opener().open(Request(location, method="OPTIONS", headers=headers), timeout=5)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise IntegrationError("WebDAV 已连接，但账号或密码验证失败") from exc
        raise IntegrationError(f"WebDAV 返回错误：HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(f"无法连接 WebDAV：{exc}") from exc
    return {"ok": True, "message": f"WebDAV 连接成功（HTTP {response.status}）；手动完整备份暂请使用映射盘或 SMB 共享"}


def _nas_directory(config: dict[str, Any], *, create: bool = False) -> Path:
    if config.get("protocol") not in {"local", "smb"}:
        raise IntegrationError("当前手动完整备份请使用本机/映射盘或 Windows SMB 共享")
    location = str(config.get("location") or "").strip()
    if not location:
        raise IntegrationError("请先填写 NAS 共享目录")
    path = Path(location).expanduser()
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IntegrationError(f"无法创建或访问 NAS 目录：{exc}") from exc
    if not path.is_dir():
        raise IntegrationError("无法访问 NAS 目录，请检查共享盘映射、网络和 Windows 权限")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def store_nas_backup(config: dict[str, Any], source_path: Path, target_name: str) -> dict[str, Any]:
    directory = _nas_directory(config, create=True)
    safe_name = Path(str(target_name)).name
    if safe_name != str(target_name) or not safe_name.startswith("燕翔车队") or Path(safe_name).suffix.lower() != ".zip":
        raise IntegrationError("NAS 备份文件名不正确")
    target = directory / safe_name
    partial = directory / f".{safe_name}.{uuid.uuid4().hex}.partial"
    try:
        with source_path.open("rb") as source, partial.open("xb") as output:
            shutil.copyfileobj(source, output, length=4 * 1024 * 1024)
            output.flush()
            try:
                os.fsync(output.fileno())
            except OSError:
                pass
        if partial.stat().st_size != source_path.stat().st_size or _sha256_file(partial) != _sha256_file(source_path):
            raise IntegrationError("NAS 备份上传后校验失败，未替换正式文件")
        partial.replace(target)
    except (OSError, IntegrationError) as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, IntegrationError):
            raise
        raise IntegrationError(f"NAS 备份写入失败：{exc}") from exc
    stat = target.stat()
    return {
        "filename": target.name,
        "size": stat.st_size,
        "sha256": _sha256_file(target),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def list_nas_backups(config: dict[str, Any]) -> list[dict[str, Any]]:
    directory = _nas_directory(config)
    items: list[dict[str, Any]] = []
    try:
        candidates = sorted(
            (path for path in directory.iterdir() if path.is_file() and path.name.startswith("燕翔车队") and path.suffix.lower() == ".zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:100]
        for path in candidates:
            stat = path.stat()
            items.append({
                "filename": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            })
    except OSError as exc:
        raise IntegrationError(f"无法读取 NAS 备份列表：{exc}") from exc
    return items


def resolve_nas_backup(config: dict[str, Any], filename: str) -> Path:
    safe_name = Path(str(filename)).name
    if safe_name != str(filename) or not safe_name.startswith("燕翔车队") or Path(safe_name).suffix.lower() != ".zip":
        raise IntegrationError("NAS 备份文件名不正确")
    path = _nas_directory(config) / safe_name
    if not path.is_file():
        raise IntegrationError("所选 NAS 备份不存在或暂时无法访问")
    return path
