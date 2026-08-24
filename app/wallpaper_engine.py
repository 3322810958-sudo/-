from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from .config import APPEARANCE_IMAGE_EXTENSIONS, APPEARANCE_VIDEO_EXTENSIONS


WALLPAPER_APP_ID = "431960"
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.RLock()


def _registry_steam_paths() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    candidates: list[Path] = []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key_name, value_name in locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
            if value:
                candidates.append(Path(str(value)))
        except OSError:
            continue
    return candidates


def steam_library_roots() -> list[Path]:
    candidates = _registry_steam_paths()
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Steam")
    candidates.extend([Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")])

    roots: list[Path] = []
    for steam_root in candidates:
        if not steam_root.exists():
            continue
        roots.append(steam_root)
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        try:
            content = library_file.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        for raw in re.findall(r'"path"\s+"([^"]+)"', content):
            value = raw.replace("\\\\", "\\")
            roots.append(Path(value))

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _safe_asset(root: Path, relative: Any) -> Path | None:
    value = str(relative or "").replace("\\", "/").strip().lstrip("/")
    if not value:
        return None
    try:
        candidate = (root / value).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _kind(path: Path | None) -> str:
    if not path:
        return ""
    extension = path.suffix.lower()
    if extension in APPEARANCE_VIDEO_EXTENSIONS:
        return "video"
    if extension in APPEARANCE_IMAGE_EXTENSIONS:
        return "image"
    return ""


def _project_roots() -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    for library in steam_library_roots():
        workshop = library / "steamapps" / "workshop" / "content" / WALLPAPER_APP_ID
        if workshop.is_dir():
            roots.extend((item, "Steam 创意工坊") for item in workshop.iterdir() if item.is_dir())
        my_projects = library / "steamapps" / "common" / "wallpaper_engine" / "projects" / "myprojects"
        if my_projects.is_dir():
            roots.extend((item, "Wallpaper Engine 我的壁纸") for item in my_projects.iterdir() if item.is_dir())
    return roots


def _read_project(root: Path, source: str) -> dict[str, Any] | None:
    project_file = root / "project.json"
    if not project_file.is_file():
        return None
    try:
        project = json.loads(project_file.read_text("utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(project, dict):
        return None

    project_type = str(project.get("type") or "unknown").lower()
    primary = _safe_asset(root, project.get("file"))
    preview = _safe_asset(root, project.get("preview"))
    if not preview:
        for name in ("preview.jpg", "preview.png", "preview.gif", "preview.webp"):
            candidate = root / name
            if candidate.is_file():
                preview = candidate
                break

    primary_kind = _kind(primary)
    preview_kind = _kind(preview)
    playback = primary if primary_kind else preview if preview_kind else None
    playback_kind = _kind(playback)
    if not playback or not playback_kind:
        return None

    item_id = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8", errors="ignore")).hexdigest()[:24]
    title = str(project.get("title") or root.name).strip()[:160] or root.name
    fallback = not bool(primary_kind)
    type_labels = {"video": "视频壁纸", "scene": "场景壁纸", "web": "网页壁纸", "application": "应用壁纸"}
    return {
        "id": item_id,
        "title": title,
        "source": source,
        "workshop_id": root.name if root.name.isdigit() else "",
        "project_type": project_type,
        "type_label": type_labels.get(project_type, "图片壁纸" if playback_kind == "image" else "视频壁纸"),
        "playback_kind": playback_kind,
        "animated": playback_kind == "video" or playback.suffix.lower() == ".gif",
        "uses_preview": fallback,
        "import_note": "当前类型使用静态预览图" if fallback else "可直接在软件内播放",
        "root": root,
        "preview_path": preview or playback,
        "playback_path": playback,
    }


def scan_wallpapers(*, force: bool = False) -> dict[str, Any]:
    with _CACHE_LOCK:
        if _CACHE and not force:
            return _public_result()
        _CACHE.clear()
        for root, source in _project_roots():
            item = _read_project(root, source)
            if item:
                _CACHE[item["id"]] = item
        return _public_result()


def _public_result() -> dict[str, Any]:
    items = []
    for item in sorted(_CACHE.values(), key=lambda value: (value["source"], value["title"].casefold())):
        public = {key: value for key, value in item.items() if key not in {"root", "preview_path", "playback_path"}}
        public["preview_url"] = f"/api/admin/wallpaper-engine/{item['id']}/preview"
        items.append(public)
    return {
        "detected": bool(steam_library_roots()),
        "count": len(items),
        "items": items,
        "message": "已读取 Wallpaper Engine 创意工坊" if items else "未找到可导入的 Wallpaper Engine 壁纸",
    }


def wallpaper_item(item_id: str) -> dict[str, Any] | None:
    safe_id = re.sub(r"[^a-f0-9]", "", str(item_id).lower())[:24]
    with _CACHE_LOCK:
        item = _CACHE.get(safe_id)
        if item:
            return item
    scan_wallpapers(force=True)
    with _CACHE_LOCK:
        return _CACHE.get(safe_id)
