from __future__ import annotations

import asyncio
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .attachments import attachment_path, extract_zip, find_blob, save_file, save_upload
from .auth import (
    COOKIE_NAME,
    AuthContext,
    change_own_credentials,
    get_auth,
    login,
    logout,
    public_user,
    require_admin,
    require_csrf,
    require_write,
)
from .business import (
    BusinessError,
    PRODUCT_TYPES,
    batch_update_invoices,
    dashboard,
    delete_demo_data,
    delete_invoice,
    invoice_payload,
    list_invoices,
    record_settlement,
    save_invoice,
    settlement_summary,
    yuan,
)
from .classification import classify_invoice, load_rules, serialize_rules
from .config import (
    APPEARANCE_IMAGE_EXTENSIONS,
    APPEARANCE_MEDIA_EXTENSIONS,
    APPEARANCE_VIDEO_EXTENSIONS,
    APP_DIR,
    APP_MODE,
    DB_PATH,
    PROGRAM_DIR,
    RUNTIME_HOME,
    SUPPORTED_EXTENSIONS,
    SYNC_INTERVAL_SECONDS,
    TMP_DIR,
    UPLOAD_DIR,
)
from .database import (
    DB_LOCK,
    SYNC_TABLES,
    audit,
    connect,
    create_snapshot,
    current_season,
    current_season_id,
    enqueue_sync_event,
    fetch_all,
    get_device_id,
    init_db,
    new_id,
    restore_snapshot,
    row_dict,
    set_setting,
    setting,
    transaction,
    utc_now,
)
from .feedback import deliver_feedback
from .integrations import (
    IntegrationError,
    normalize_ai_connectors,
    normalize_nas_config,
    protect_secret,
    test_ai_connection,
    test_nas_connection,
    unprotect_secret,
)
from .maintenance import clear_current_season_data
from .ocr_engine import create_ocr_job, get_ocr_job, parse_invoice_text, warmup_ocr
from .platform_features import (
    OFFICE_EXCEL,
    OFFICE_POWERPOINT,
    OFFICE_WORD,
    TEXT_EXTENSIONS,
    convert_office_to_pdf,
    open_local_file,
    text_to_pdf,
)
from .quality import list_issues, record_issue, resolve_issue, sync_ocr_issues
from .security import hash_password, token_hash, validate_new_password, validate_username, verify_password
from .sync_engine import SyncError, apply_events, events_after, perform_sync, sync_config, valid_sync_key
from .story_engine import extract_embedded_images, extract_story_text, story_asset_role
from .updater import check_for_update, get_update_job, list_patch_releases, schedule_update_install, start_update_download
from .wallpaper_engine import scan_wallpapers, wallpaper_item


STATIC_DIR = PROGRAM_DIR / "web" if (PROGRAM_DIR / "web" / "index.html").is_file() else APP_DIR / "static"
STOP_EVENT = threading.Event()
APPEARANCE_SETTING_KEYS = (
    "team_name", "background_image", "background_media_id", "background_media_kind",
    "background_overlay", "accent_color", "login_slideshow_enabled", "login_slides",
    "login_transition", "loading_cars", "login_panel_opacity", "sidebar_transparency", "topbar_transparency", "login_random_enabled",
    "global_background_random",
)

LOGIN_INFO_REQUIRED_TYPES = {"credits", "updates", "motto", "philosophy"}


def _default_login_info_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    creator_names = [str(row[0]) for row in conn.execute(
        "SELECT name FROM creators WHERE active=1 AND deleted_at IS NULL ORDER BY sort_order,name"
    ).fetchall()]
    credits = "创作者：" + ("、".join(creator_names) if creator_names else "燕翔车队软件组")
    credits += "\n鸣谢：PaddleOCR、pypdf、pywebview、Tabler 等开源项目"
    return [
        {"id": "credits", "type": "credits", "title": "创作者与鸣谢", "content": credits, "visible": True},
        {"id": "updates", "type": "updates", "title": "V2.3.7 本次更新", "content": "修复一键升级后卡在启动页；补丁同步匹配运行库；新增启动诊断日志，并完善无界面环境下的发布验证。", "visible": True},
        {"id": "season_total", "type": "total", "title": "当前赛季记款总金额", "content": "", "visible": True},
        {"id": "motto", "type": "motto", "title": "队训", "content": "脚踏实地、精益求精", "visible": True},
        {"id": "philosophy", "type": "philosophy", "title": "造车理念", "content": "品质、精致、极致", "visible": True},
    ]


def _normalize_login_info(conn: sqlite3.Connection, raw: Any) -> dict[str, Any]:
    defaults = _default_login_info_items(conn)
    default_by_type = {item["type"]: item for item in defaults}
    source = raw if isinstance(raw, dict) else {}
    try:
        interval = max(3, min(60, int(source.get("interval", 7))))
    except (TypeError, ValueError):
        interval = 7
    clean: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_required: set[str] = set()
    raw_items = source.get("items") if isinstance(source.get("items"), list) else []
    for index, item in enumerate(raw_items[:40]):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "custom")[:30]
        item_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(item.get("id") or f"custom_{index}"))[:80] or f"custom_{index}"
        if item_id in seen_ids or (item_type in LOGIN_INFO_REQUIRED_TYPES and item_type in seen_required):
            continue
        required = item_type in LOGIN_INFO_REQUIRED_TYPES
        clean.append({
            "id": item_id,
            "type": item_type,
            "title": str(item.get("title") or default_by_type.get(item_type, {}).get("title") or "自定义信息")[:100],
            "content": str(item.get("content") or default_by_type.get(item_type, {}).get("content") or "")[:3000],
            "visible": True if required else bool(item.get("visible", True)),
            "required": required,
        })
        seen_ids.add(item_id)
        if required:
            seen_required.add(item_type)
    for item in defaults:
        if item["type"] in LOGIN_INFO_REQUIRED_TYPES and item["type"] not in seen_required:
            clean.append({**item, "required": True})
        elif item["type"] == "total" and not any(value["type"] == "total" for value in clean):
            clean.append({**item, "required": False})
    season_id = current_season_id(conn)
    total = int(conn.execute(
        "SELECT COALESCE(SUM(total_amount_cents),0) FROM invoices WHERE season_id=? AND deleted_at IS NULL",
        (season_id,),
    ).fetchone()[0])
    season = current_season(conn)
    for item in clean:
        if item["type"] == "total":
            item["title"] = f"{season['name']}记款总金额"
            item["content"] = f"¥{yuan(total):,.2f}"
    return {"season_id": season_id, "season_name": season["name"], "interval": interval, "items": clean}


def _login_info_for_season(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        mapping = json.loads(setting(conn, "login_info_panels", "{}"))
    except json.JSONDecodeError:
        mapping = {}
    if not isinstance(mapping, dict):
        mapping = {}
    return _normalize_login_info(conn, mapping.get(current_season_id(conn), {}))

OPEN_SOURCE_REFERENCES = (
    {"name": "PaddleOCR", "url": "https://github.com/PaddlePaddle/PaddleOCR", "license": "Apache-2.0", "use": "本地 OCR 与置信度设计参考"},
    {"name": "pypdf", "url": "https://github.com/py-pdf/pypdf", "license": "BSD-3-Clause", "use": "PDF 页面合并"},
    {"name": "pywebview", "url": "https://github.com/r0x0r/pywebview", "license": "BSD-3-Clause", "use": "Windows 桌面容器兼容性"},
    {"name": "Tabler", "url": "https://github.com/tabler/tabler", "license": "MIT", "use": "问题图例与响应式操作区设计参考"},
    {"name": "Invoice-Manager", "url": "https://github.com/stone16/Invoice-Manager", "license": "MIT", "use": "中文发票异步处理设计参考"},
    {"name": "fp-reimbursement-system", "url": "https://github.com/zhangdexuan/fp-reimbursement-system", "license": "MIT", "use": "本地报销与附件 PDF 工作流参考"},
    {"name": "keepr", "url": "https://github.com/BlinkingSun/keepr", "license": "MIT", "use": "离线优先与问题标记参考"},
    {"name": "LibreOffice", "url": "https://www.libreoffice.org/", "license": "MPL-2.0", "use": "调用本机安装程序转换办公附件"},
    {"name": "HuggingPT UI Examples", "url": "https://huggingpt.com/ui-examples/", "license": "仅界面布局灵感", "use": "未复制网站媒体或代码"},
)


class SocketHub:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, socket: WebSocket) -> None:
        await socket.accept()
        self.connections.add(socket)

    def disconnect(self, socket: WebSocket) -> None:
        self.connections.discard(socket)

    async def notify(self, event: str = "data_changed") -> None:
        stale: list[WebSocket] = []
        for socket in list(self.connections):
            try:
                await socket.send_json({"event": event, "at": utc_now()})
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(socket)


hub = SocketHub()


def _sync_loop() -> None:
    while not STOP_EVENT.wait(SYNC_INTERVAL_SECONDS):
        try:
            if sync_config()["enabled"]:
                perform_sync()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if os.environ.get("YXRT_OCR_WARMUP", "0") == "1" and "PYTEST_CURRENT_TEST" not in os.environ:
        warmup_ocr()
    STOP_EVENT.clear()
    worker = threading.Thread(target=_sync_loop, name="yxrt-sync", daemon=True)
    worker.start()
    yield
    STOP_EVENT.set()


app = FastAPI(
    title="燕翔车队经费管理系统",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    inline_attachment = request.url.path.startswith("/api/attachments/") and request.query_params.get("inline") == "1"
    response.headers["X-Frame-Options"] = "SAMEORIGIN" if inline_attachment else "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:; font-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(BusinessError)
async def business_error_handler(_: Request, exc: BusinessError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/stories", response_class=HTMLResponse)
async def stories_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "stories.html", media_type="text/html; charset=utf-8")


def _story_payload(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    assets = [dict(asset) for asset in conn.execute(
        """SELECT sa.id,sa.asset_role,sa.caption,sa.sort_order,a.id AS attachment_id,
        a.original_name,a.mime_type,a.size_bytes
        FROM story_assets sa JOIN attachments a ON a.id=sa.attachment_id
        WHERE sa.story_id=? AND a.deleted_at IS NULL ORDER BY sa.sort_order,sa.created_at""",
        (item["id"],),
    ).fetchall()]
    for asset in assets:
        asset["url"] = f"/api/stories/assets/{asset['id']}"
        asset["download_url"] = f"/api/stories/assets/{asset['id']}?download=1"
    item["assets"] = assets
    item["published"] = bool(item.get("published"))
    return item


def _optional_story_admin(request: Request) -> AuthContext | None:
    try:
        auth = get_auth(request)
    except HTTPException:
        return None
    return auth if auth.user.get("role") == "admin" else None


@app.get("/api/stories")
async def stories_public(request: Request, season_id: str = "", include_drafts: bool = False) -> dict[str, Any]:
    admin = _optional_story_admin(request)
    with connect() as conn:
        clauses = ["s.deleted_at IS NULL"]
        params: list[Any] = []
        if not (admin and include_drafts):
            clauses.append("s.published=1")
        if season_id:
            clauses.append("s.season_id=?")
            params.append(season_id)
        rows = conn.execute(
            f"""SELECT s.*,se.name AS season_name FROM stories s JOIN seasons se ON se.id=s.season_id
            WHERE {' AND '.join(clauses)} ORDER BY s.published_date DESC,s.sort_order,s.created_at DESC""",
            params,
        ).fetchall()
        stories = [_story_payload(conn, row) for row in rows]
        seasons = [dict(row) for row in conn.execute(
            """SELECT DISTINCT se.id,se.name,se.sort_order FROM seasons se JOIN stories s ON s.season_id=se.id
            WHERE s.deleted_at IS NULL AND (s.published=1 OR ?) ORDER BY se.sort_order DESC,se.created_at DESC""",
            (1 if admin else 0,),
        ).fetchall()]
    return {
        "items": stories,
        "seasons": seasons,
        "can_edit": bool(admin),
        "csrf_token": admin.session["csrf_token"] if admin else "",
        "version": __version__,
    }


@app.get("/api/stories/assets/{asset_id}")
async def story_asset_content(asset_id: str, request: Request, download: bool = False) -> FileResponse:
    with connect() as conn:
        row = conn.execute(
            """SELECT a.*,s.published FROM story_assets sa JOIN stories s ON s.id=sa.story_id
            JOIN attachments a ON a.id=sa.attachment_id
            WHERE sa.id=? AND s.deleted_at IS NULL AND a.deleted_at IS NULL""",
            (asset_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="故事附件不存在")
    if not row["published"] and not _optional_story_admin(request):
        raise HTTPException(status_code=404, detail="故事附件不存在")
    path = attachment_path(row["stored_name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="故事附件文件不存在")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=row["mime_type"], filename=row["original_name"] if download else None,
        headers={"Content-Disposition": f'{disposition}; filename*=UTF-8\'\'{quote(str(row["original_name"]))}'},
    )


def _clean_story_input(payload: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()[:200]
    if not title:
        raise ValueError("请填写故事标题")
    season_id = str(payload.get("season_id") or current_season_id(conn))
    if not conn.execute("SELECT 1 FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)).fetchone():
        raise ValueError("所选赛季不存在")
    layout = str(payload.get("layout_style") or "editorial")
    if layout not in {"editorial", "timeline", "gallery", "technical"}:
        layout = "editorial"
    color = str(payload.get("accent_color") or "#27d3ff")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#27d3ff"
    return {
        "season_id": season_id,
        "title": title,
        "summary": str(payload.get("summary") or "").strip()[:5000],
        "body": str(payload.get("body") or "").strip()[:500000],
        "author_name": str(payload.get("author_name") or "").strip()[:100],
        "published_date": str(payload.get("published_date") or utc_now()[:10])[:10],
        "layout_style": layout,
        "accent_color": color,
        "published": 1 if payload.get("published") else 0,
        "sort_order": max(-9999, min(9999, int(payload.get("sort_order") or 0))),
    }


@app.post("/api/admin/stories")
async def story_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    try:
        with transaction() as conn:
            clean = _clean_story_input(payload, conn)
            create_snapshot(conn, auth.user["id"], "新增故事前", clean["title"])
            now = utc_now(); story_id = new_id("story")
            row = {
                "id": story_id, **clean, "created_by": auth.user["id"], "created_at": now,
                "updated_at": now, "version": 1, "device_id": get_device_id(conn), "deleted_at": None,
            }
            _insert_columns = list(row)
            conn.execute(
                f"INSERT INTO stories({','.join(_insert_columns)}) VALUES({','.join('?' for _ in _insert_columns)})",
                tuple(row[column] for column in _insert_columns),
            )
            audit(conn, auth.user["id"], "create", "story", story_id, {"title": clean["title"]})
        return {"ok": True, "id": story_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/stories/{story_id}")
async def story_update(story_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    try:
        with transaction() as conn:
            current = conn.execute("SELECT * FROM stories WHERE id=? AND deleted_at IS NULL", (story_id,)).fetchone()
            if not current:
                raise ValueError("故事不存在")
            clean = _clean_story_input(payload, conn)
            create_snapshot(conn, auth.user["id"], "编辑故事前", current["title"])
            assignments = ",".join(f"{key}=?" for key in clean)
            conn.execute(
                f"UPDATE stories SET {assignments},updated_at=?,version=version+1 WHERE id=?",
                (*clean.values(), utc_now(), story_id),
            )
            audit(conn, auth.user["id"], "update", "story", story_id, {"title": clean["title"]})
        return {"ok": True, "id": story_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/admin/stories/{story_id}")
async def story_delete(story_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with transaction() as conn:
        current = conn.execute("SELECT * FROM stories WHERE id=? AND deleted_at IS NULL", (story_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="故事不存在")
        create_snapshot(conn, auth.user["id"], "删除故事前", current["title"])
        conn.execute("UPDATE stories SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?", (utc_now(), utc_now(), story_id))
        audit(conn, auth.user["id"], "delete", "story", story_id, {"title": current["title"]})
    return {"ok": True}


@app.post("/api/admin/stories/{story_id}/assets")
async def story_assets_upload(
    story_id: str, request: Request, files: list[UploadFile] = File(...), extract_content: bool = Form(True)
) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM stories WHERE id=? AND deleted_at IS NULL", (story_id,)).fetchone():
            raise HTTPException(status_code=404, detail="故事不存在")
    saved: list[dict[str, Any]] = []
    extracted_sections: list[str] = []
    embedded: list[dict[str, Any]] = []
    for upload in files[:100]:
        attachment = await save_upload(upload, auth.user)
        path = attachment_path(attachment["stored_name"])
        text = await asyncio.to_thread(extract_story_text, path) if extract_content else ""
        if text:
            extracted_sections.append(f"【{attachment['original_name']}】\n{text}")
        temp_dir = Path(tempfile.mkdtemp(prefix="yxrt_story_media_", dir=TMP_DIR))
        try:
            for image_path in await asyncio.to_thread(extract_embedded_images, path, temp_dir):
                embedded.append(save_file(image_path, f"{Path(attachment['original_name']).stem}_{image_path.name}", auth.user))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        saved.append(attachment)
    with transaction() as conn:
        now = utc_now()
        has_cover = bool(conn.execute(
            "SELECT 1 FROM story_assets WHERE story_id=? AND asset_role='cover' LIMIT 1", (story_id,),
        ).fetchone())
        added_ids: set[str] = set()
        for index, attachment in enumerate([*saved, *embedded]):
            attachment_id = str(attachment["id"])
            if attachment_id in added_ids:
                continue
            role = story_asset_role(str(attachment["original_name"]))
            if role == "gallery" and not has_cover:
                role = "cover"; has_cover = True
            conn.execute(
                """INSERT OR IGNORE INTO story_assets(id,story_id,attachment_id,asset_role,caption,sort_order,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (new_id("story_asset"), story_id, attachment_id, role, "", index, now, now),
            )
            added_ids.add(attachment_id)
        if extracted_sections:
            current = conn.execute("SELECT body FROM stories WHERE id=?", (story_id,)).fetchone()
            combined = (str(current["body"] or "").rstrip() + "\n\n" + "\n\n".join(extracted_sections)).strip()[:500000]
            conn.execute("UPDATE stories SET body=?,updated_at=?,version=version+1 WHERE id=?", (combined, now, story_id))
        audit(conn, auth.user["id"], "upload", "story", story_id, {"files": len(saved), "embedded_images": len(embedded)})
    return {"ok": True, "files": len(saved), "embedded_images": len(embedded), "extracted_text": bool(extracted_sections)}


@app.delete("/api/admin/stories/{story_id}/assets/{asset_id}")
async def story_asset_delete(story_id: str, asset_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with transaction() as conn:
        result = conn.execute("DELETE FROM story_assets WHERE id=? AND story_id=?", (asset_id, story_id))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="故事附件不存在")
        audit(conn, auth.user["id"], "delete", "story_asset", asset_id, {"story_id": story_id})
    return {"ok": True}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "mode": APP_MODE, "time": utc_now()}


@app.get("/api/update/check")
async def update_check(request: Request) -> dict[str, Any]:
    get_auth(request)
    try:
        return await asyncio.to_thread(check_for_update)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/update/releases")
async def update_releases(request: Request) -> dict[str, Any]:
    get_auth(request)
    try:
        return {"items": await asyncio.to_thread(list_patch_releases)}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/admin/update/download")
async def update_download(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    try:
        payload = await request.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    try:
        target_version = str(payload.get("target_version") or "")
        if target_version:
            return await asyncio.to_thread(start_update_download, auth.user["id"], target_version)
        return await asyncio.to_thread(start_update_download, auth.user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/update/jobs/{job_id}")
async def update_job_status(job_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    job = get_update_job(job_id)
    if not job or job.get("created_by") != auth.user["id"]:
        raise HTTPException(status_code=404, detail="更新任务不存在")
    return job


def _find_version_restore_point(version: str) -> Path | None:
    roots = [RUNTIME_HOME / "backups", DB_PATH.parent / "backups"]
    candidates: list[Path] = []
    for root in roots:
        if root.is_dir():
            candidates.extend(root.glob("*.zip"))
    for path in sorted({item.resolve() for item in candidates}, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with zipfile.ZipFile(path) as archive:
                if "manifest.json" not in archive.namelist():
                    continue
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if str(manifest.get("version") or "").lstrip("vV") == str(version).lstrip("vV"):
                return path
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
            continue
    return None


@app.post("/api/admin/update/jobs/{job_id}/install")
async def update_install(job_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    job = get_update_job(job_id)
    if not job or job.get("created_by") != auth.user["id"]:
        raise HTTPException(status_code=404, detail="更新任务不存在")
    backup_dir = RUNTIME_HOME / "backups"
    stamp = utc_now().replace(":", "-")[:19]
    version = re.sub(r"[^0-9A-Za-z._-]", "", str(job.get("latest_version") or "latest")) or "latest"
    restore_point = None
    if job.get("direction") == "rollback":
        restore_point = await asyncio.to_thread(_find_version_restore_point, version)
        if restore_point is None:
            raise HTTPException(
                status_code=400,
                detail=f"没有找到 V{version} 的完整数据恢复点。为避免发票与程序版本不一致，已停止回溯。",
            )
    backup_path = backup_dir / f"自动更新前完整备份_V{__version__}_to_V{version}_{stamp}.zip"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_create_backup_archive, backup_path)
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError) as exc:
        backup_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"自动完整备份失败，已停止更新：{exc}") from exc
    try:
        if restore_point:
            result = schedule_update_install(
                job_id, auth.user["id"], restore_archive=restore_point, safety_backup=backup_path,
            )
        else:
            result = schedule_update_install(job_id, auth.user["id"])
        result["backup_path"] = str(backup_path)
        result["message"] = (
            f"已保存当前完整备份，并将程序与业务数据回溯到 V{version}，软件将自动重启"
            if restore_point else "完整备份已自动保存，更新安装程序已启动，软件将自动重启"
        )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _media_kind(name: str, mime_type: str = "") -> str:
    extension = Path(name).suffix.lower()
    if extension in APPEARANCE_VIDEO_EXTENSIONS or str(mime_type).startswith("video/"):
        return "video"
    return "image"


def _appearance_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in APPEARANCE_SETTING_KEYS)
    values = {row["key"]: row["value"] for row in conn.execute(
        f"SELECT key,value FROM app_settings WHERE key IN ({placeholders})", APPEARANCE_SETTING_KEYS
    ).fetchall()}
    values.setdefault("team_name", "燕翔车队 Racing Team")
    values.setdefault("background_image", "")
    values.setdefault("background_media_id", "")
    values.setdefault("background_media_kind", "image")
    values.setdefault("background_overlay", "0.82")
    values.setdefault("accent_color", "#27d3ff")
    values.setdefault("login_slideshow_enabled", "1")
    values.setdefault("login_transition", "fade")
    values.setdefault("loading_cars", "[]")
    values.setdefault("login_panel_opacity", "0.78")
    values.setdefault("sidebar_transparency", "0.22")
    values.setdefault("topbar_transparency", "0.22")
    values.setdefault("login_random_enabled", "0")
    values.setdefault("global_background_random", "0")

    background_id = str(values.get("background_media_id") or "")
    values["background_media_url"] = f"/api/public/media/{background_id}" if background_id else ""
    raw_slides = values.get("login_slides") or "[]"
    try:
        parsed = json.loads(raw_slides) if isinstance(raw_slides, str) else raw_slides
    except json.JSONDecodeError:
        parsed = []
    slides: list[dict[str, Any]] = []
    if isinstance(parsed, list):
        for index, raw in enumerate(parsed):
            if not isinstance(raw, dict):
                continue
            attachment_id = str(raw.get("attachment_id") or "")
            attachment = conn.execute(
                "SELECT id,original_name,mime_type FROM attachments WHERE id=? AND deleted_at IS NULL",
                (attachment_id,),
            ).fetchone()
            if not attachment:
                continue
            try:
                duration = max(2, min(600, int(raw.get("duration", 8))))
            except (TypeError, ValueError):
                duration = 8
            slides.append({
                "id": str(raw.get("id") or f"slide_{index}_{attachment_id}")[:100],
                "attachment_id": attachment_id,
                "title": str(raw.get("title") or attachment["original_name"])[:160],
                "kind": _media_kind(attachment["original_name"], attachment["mime_type"]),
                "duration": duration,
                "url": f"/api/public/media/{attachment_id}",
                "private_url": f"/api/attachments/{attachment_id}/content",
            })
    values["login_slides"] = slides
    values["login_slideshow_enabled"] = str(values["login_slideshow_enabled"]) == "1"
    values["login_random_enabled"] = str(values["login_random_enabled"]) == "1"
    values["global_background_random"] = str(values["global_background_random"]) == "1"
    try:
        values["login_panel_opacity"] = max(0.35, min(1.0, float(values["login_panel_opacity"])))
    except (TypeError, ValueError):
        values["login_panel_opacity"] = 0.78
    for key in ("sidebar_transparency", "topbar_transparency"):
        try:
            values[key] = max(0.0, min(1.0, float(values[key])))
        except (TypeError, ValueError):
            values[key] = 0.22
    raw_cars = values.get("loading_cars") or "[]"
    try:
        parsed_cars = json.loads(raw_cars) if isinstance(raw_cars, str) else raw_cars
    except json.JSONDecodeError:
        parsed_cars = []
    loading_cars: list[dict[str, Any]] = []
    if isinstance(parsed_cars, list):
        for index, raw in enumerate(parsed_cars[:12]):
            if not isinstance(raw, dict):
                continue
            attachment_id = str(raw.get("attachment_id") or "")
            attachment = conn.execute(
                "SELECT id,original_name,mime_type FROM attachments WHERE id=? AND deleted_at IS NULL",
                (attachment_id,),
            ).fetchone()
            if not attachment or _media_kind(attachment["original_name"], attachment["mime_type"]) != "image":
                continue
            loading_cars.append({
                "id": str(raw.get("id") or f"loader_{index}_{attachment_id}")[:100],
                "attachment_id": attachment_id,
                "title": str(raw.get("title") or attachment["original_name"])[:160],
                "url": f"/api/public/media/{attachment_id}",
                "private_url": f"/api/attachments/{attachment_id}/content",
            })
    if not loading_cars:
        loading_cars = [
            {"id": "default_formula_1", "attachment_id": "", "title": "方程式赛车一", "url": "/static/assets/loading-car-formula-1.png", "private_url": "/static/assets/loading-car-formula-1.png"},
            {"id": "default_formula_2", "attachment_id": "", "title": "方程式赛车二", "url": "/static/assets/loading-car-formula-2.png", "private_url": "/static/assets/loading-car-formula-2.png"},
        ]
    values["loading_cars"] = loading_cars
    values["login_info"] = _login_info_for_season(conn)
    return values


def _invoice_defaults(conn: sqlite3.Connection) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "category_id": "",
        "payer_member_id": "",
        "funding_source_id": "",
        "burden_type": "team_aa",
        "split_member_ids": [],
        "batch_note": "",
        "burden_labels": {
            "team_aa": "全队 AA",
            "specified_split": "指定成员",
            "self_paid": "个人承担",
        },
    }
    try:
        saved = json.loads(setting(conn, "invoice_defaults", "{}"))
    except json.JSONDecodeError:
        saved = {}
    if not isinstance(saved, dict):
        return defaults
    for key in ("category_id", "payer_member_id", "funding_source_id", "batch_note"):
        if key in saved:
            defaults[key] = str(saved.get(key) or "")
    burden = str(saved.get("burden_type") or "")
    if burden in {"team_aa", "specified_split", "self_paid"}:
        defaults["burden_type"] = burden
    split_ids = saved.get("split_member_ids")
    if isinstance(split_ids, list):
        defaults["split_member_ids"] = [str(value) for value in split_ids]
    labels = saved.get("burden_labels")
    if isinstance(labels, dict):
        for key in defaults["burden_labels"]:
            value = str(labels.get(key) or "").strip()
            if value:
                defaults["burden_labels"][key] = value[:30]
    return defaults


def _configured_public_media_ids(conn: sqlite3.Connection) -> set[str]:
    settings = _appearance_settings(conn)
    ids = {str(settings.get("background_media_id") or "")}
    ids.update(str(slide.get("attachment_id") or "") for slide in settings.get("login_slides", []))
    ids.update(str(car.get("attachment_id") or "") for car in settings.get("loading_cars", []))
    return {value for value in ids if value}


@app.get("/api/public/appearance")
async def public_appearance() -> dict[str, Any]:
    with connect() as conn:
        settings = _appearance_settings(conn)
    return {"version": __version__, "settings": settings}


@app.get("/api/public/references")
async def public_references() -> dict[str, Any]:
    return {
        "items": list(OPEN_SOURCE_REFERENCES),
        "notice": "引用内容仅用于燕翔车队内部发票报销与非商业技术学习；图片、音乐未使用来源不明素材。",
    }


@app.get("/api/public/media/{attachment_id}")
async def public_appearance_media(attachment_id: str) -> FileResponse:
    with connect() as conn:
        if attachment_id not in _configured_public_media_ids(conn):
            raise HTTPException(status_code=404, detail="该界面媒体未启用")
        attachment = conn.execute(
            "SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL", (attachment_id,)
        ).fetchone()
    if not attachment:
        raise HTTPException(status_code=404, detail="界面媒体不存在")
    path = attachment_path(attachment["stored_name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="界面媒体文件不存在")
    return FileResponse(path, media_type=attachment["mime_type"], headers={"Content-Disposition": "inline"})


@app.post("/api/auth/login")
async def auth_login(payload: dict[str, Any], response: Response) -> dict[str, Any]:
    return login(str(payload.get("username") or ""), str(payload.get("password") or ""), response)


@app.get("/api/auth/recovery-status")
async def auth_recovery_status() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """SELECT enabled,username_hint,locked_until FROM admin_recovery
            WHERE enabled=1 ORDER BY created_at LIMIT 1"""
        ).fetchone()
    return {
        "enabled": bool(row and row["enabled"]),
        "username_hint": str(row["username_hint"] if row else "原始管理员账号"),
        "locked_until": str(row["locked_until"] or "") if row else "",
    }


@app.post("/api/auth/recover-admin")
async def auth_recover_admin(payload: dict[str, Any]) -> dict[str, Any]:
    original_username = str(payload.get("original_username") or "").strip().lower()
    original_password = str(payload.get("original_password") or "")
    try:
        new_username = validate_username(str(payload.get("new_username") or ""))
        validate_new_password(str(payload.get("new_password") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with transaction() as conn:
        recovery = conn.execute(
            "SELECT * FROM admin_recovery WHERE enabled=1 ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not recovery:
            raise HTTPException(status_code=403, detail="管理员未启用原始凭据找回")
        if recovery["locked_until"]:
            try:
                locked = datetime.fromisoformat(str(recovery["locked_until"]).replace("Z", "+00:00"))
            except ValueError:
                locked = datetime.now(UTC)
            if locked > datetime.now(UTC):
                raise HTTPException(status_code=429, detail="验证失败次数过多，请稍后再试")
        valid = (
            token_hash(original_username) == recovery["original_username_hash"]
            and verify_password(original_password, recovery["original_password_hash"])
        )
        if not valid:
            attempts = int(recovery["failed_attempts"] or 0) + 1
            locked_until = None
            if attempts >= 5:
                locked_until = (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                attempts = 0
            conn.execute(
                "UPDATE admin_recovery SET failed_attempts=?,locked_until=?,updated_at=? WHERE user_id=?",
                (attempts, locked_until, utc_now(), recovery["user_id"]),
            )
            audit(conn, recovery["user_id"], "recovery_failed", "user", recovery["user_id"], {})
            raise HTTPException(status_code=401, detail="原始管理员账号或密码不正确")
        duplicate = conn.execute(
            "SELECT id FROM users WHERE username=? COLLATE NOCASE AND id<>? AND deleted_at IS NULL",
            (new_username, recovery["user_id"]),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="新账号已经被使用")
        conn.execute(
            """UPDATE users SET username=?,password_hash=?,must_change_password=0,updated_at=?,version=version+1
            WHERE id=?""",
            (new_username, hash_password(str(payload["new_password"])), utc_now(), recovery["user_id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id=?", (recovery["user_id"],))
        conn.execute(
            "UPDATE admin_recovery SET failed_attempts=0,locked_until=NULL,updated_at=? WHERE user_id=?",
            (utc_now(), recovery["user_id"]),
        )
        audit(conn, recovery["user_id"], "recovery_success", "user", recovery["user_id"], {"username": new_username})
    return {"ok": True, "message": "管理员账号与密码已重置，请使用新凭据登录"}


@app.put("/api/admin/recovery")
async def admin_recovery_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    current_password = str(payload.get("current_password") or "")
    enabled = bool(payload.get("enabled", True))
    with transaction() as conn:
        current = conn.execute("SELECT password_hash FROM users WHERE id=?", (auth.user["id"],)).fetchone()
        if not current or not verify_password(current_password, current["password_hash"]):
            raise HTTPException(status_code=400, detail="当前管理员密码不正确")
        existing = conn.execute("SELECT * FROM admin_recovery WHERE user_id=?", (auth.user["id"],)).fetchone()
        original_username = str(payload.get("original_username") or "").strip().lower()
        original_password = str(payload.get("original_password") or "")
        if enabled and (not existing or original_username or original_password):
            try:
                original_username = validate_username(original_username)
                validate_new_password(original_password)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"原始恢复凭据：{exc}") from exc
            now = utc_now()
            conn.execute(
                """INSERT INTO admin_recovery(
                user_id,original_username_hash,original_password_hash,username_hint,enabled,failed_attempts,locked_until,created_at,updated_at
                ) VALUES(?,?,?,?,1,0,NULL,?,?)
                ON CONFLICT(user_id) DO UPDATE SET original_username_hash=excluded.original_username_hash,
                original_password_hash=excluded.original_password_hash,username_hint=excluded.username_hint,
                enabled=1,failed_attempts=0,locked_until=NULL,updated_at=excluded.updated_at""",
                (
                    auth.user["id"], token_hash(original_username), hash_password(original_password),
                    f"{original_username[:1]}***（管理员自定义恢复账号）", now, now,
                ),
            )
        elif existing:
            conn.execute(
                "UPDATE admin_recovery SET enabled=?,failed_attempts=0,locked_until=NULL,updated_at=? WHERE user_id=?",
                (1 if enabled else 0, utc_now(), auth.user["id"]),
            )
        else:
            raise HTTPException(status_code=400, detail="启用找回时需要设置原始恢复账号和密码")
        audit(conn, auth.user["id"], "update", "admin_recovery", auth.user["id"], {"enabled": enabled})
    return {"ok": True, "enabled": enabled}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    logout(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    return {"user": public_user(auth.user), "csrf_token": auth.session["csrf_token"]}


@app.post("/api/auth/change-credentials")
async def auth_change_credentials(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    try:
        user = change_own_credentials(
            auth,
            str(payload.get("current_password") or ""),
            str(payload.get("username") or auth.user["username"]),
            str(payload.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "user": user}


@app.post("/api/feedback")
async def feedback_submit(
    request: Request,
    reporter_name: str = Form(""),
    season: str = Form(""),
    department: str = Form(""),
    contact: str = Form(""),
    description: str = Form(""),
    consent_public_attachment: bool = Form(False),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    report_id = new_id("feedback")
    now = utc_now()
    safe_owner = _safe_export_stem(str(auth.user.get("username") or "用户"), "用户")
    archive_name = f"{safe_owner}_问题反馈_{report_id[-8:]}.zip"
    with transaction() as conn:
        conn.execute(
            """INSERT INTO feedback_reports(
            id,user_id,season_id,reporter_name,department,contact,description,status,delivery_method,
            delivery_reference,last_error,archive_name,consent_public_attachment,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,'queued','local_queue','','',?,?,?,?)""",
            (
                report_id, auth.user["id"], current_season_id(conn), str(reporter_name or "")[:100],
                str(department or "")[:100], str(contact or "")[:200], str(description or "")[:10000],
                archive_name, 1 if consent_public_attachment else 0, now, now,
            ),
        )
        audit(conn, auth.user["id"], "create", "feedback", report_id, {"season": str(season or "")[:40]})
    saved_count = 0
    for upload in files[:30]:
        try:
            attachment = await save_upload(upload, auth.user)
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO feedback_attachments(id,report_id,attachment_id,original_name,created_at)
                    VALUES(?,?,?,?,?)""",
                    (new_id("feedback_file"), report_id, attachment["id"], attachment["original_name"], utc_now()),
                )
            saved_count += 1
        except Exception:
            continue
    result = await asyncio.to_thread(deliver_feedback, report_id)
    result["attachment_count"] = saved_count
    if result["status"] == "queued":
        result["notice"] = "发送通道未配置或暂时失败，反馈压缩包已安全保留在本机待发送队列。"
    return result


def _reference_data(conn: sqlite3.Connection) -> dict[str, Any]:
    season = current_season(conn)
    members = [dict(row) for row in conn.execute(
        """SELECT * FROM members WHERE season_id=? AND deleted_at IS NULL
        ORDER BY active DESC,sort_order,name""", (season["id"],)
    ).fetchall()]
    for member in members:
        avatar_id = str(member.get("avatar_attachment_id") or "")
        member["avatar_url"] = f"/api/attachments/{avatar_id}/content?inline=1" if avatar_id else ""
    categories = [dict(row) for row in conn.execute(
        "SELECT * FROM categories WHERE deleted_at IS NULL ORDER BY active DESC,sort_order,name"
    ).fetchall()]
    sources = [dict(row) for row in conn.execute(
        "SELECT * FROM funding_sources WHERE deleted_at IS NULL ORDER BY active DESC,sort_order,name"
    ).fetchall()]
    departments = [dict(row) for row in conn.execute(
        "SELECT * FROM departments WHERE deleted_at IS NULL ORDER BY sort_order,name"
    ).fetchall()]
    creators = [dict(row) for row in conn.execute(
        """SELECT c.*,s.name AS origin_season_name,'全赛季通用' AS season_name
        FROM creators c JOIN seasons s ON s.id=c.season_id
        WHERE c.active=1 AND c.deleted_at IS NULL
        ORDER BY c.sort_order,c.department,c.name"""
    ).fetchall()]
    settings = _appearance_settings(conn)
    settings["invoice_defaults"] = _invoice_defaults(conn)
    return {
        "members": members, "categories": categories, "funding_sources": sources,
        "departments": departments, "creators": creators, "season": season, "settings": settings,
    }


@app.get("/api/bootstrap")
async def bootstrap(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with connect() as conn:
        data = _reference_data(conn)
        data.update({
            "user": public_user(auth.user),
            "csrf_token": auth.session["csrf_token"],
            "dashboard": dashboard(conn),
            "product_types": PRODUCT_TYPES,
            "sync": sync_config(),
            "version": __version__,
            "mode": APP_MODE,
            "quality_issues": list_issues(conn),
            "open_source_references": list(OPEN_SOURCE_REFERENCES),
        })
        preference = conn.execute("SELECT settings_json FROM user_preferences WHERE user_id=?", (auth.user["id"],)).fetchone()
        try:
            data["user_preferences"] = json.loads(preference["settings_json"]) if preference else {}
        except json.JSONDecodeError:
            data["user_preferences"] = {}
        return data


PREFERENCE_KEYS = {
    "theme", "shortcuts", "audio_mode", "audio_muted", "audio_volume", "audio_current_id",
    "background_random", "login_random", "compact_mode", "login_panel_opacity", "ui_scale",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"}


def _clean_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    clean = {key: payload[key] for key in PREFERENCE_KEYS if key in payload}
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ValueError("个人设置内容过大")
    return clean


def _user_media_items(conn: sqlite3.Connection, user_id: str, media_type: str = "audio") -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT m.*,a.original_name,a.stored_name,a.mime_type,a.size_bytes
        FROM user_media m JOIN attachments a ON a.id=m.attachment_id
        WHERE m.user_id=? AND m.media_type=? AND a.deleted_at IS NULL
        ORDER BY m.sort_order,m.created_at""",
        (user_id, media_type),
    ).fetchall()]


@app.get("/api/user/preferences")
async def user_preferences_get(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with connect() as conn:
        row = conn.execute("SELECT settings_json FROM user_preferences WHERE user_id=?", (auth.user["id"],)).fetchone()
        media = _user_media_items(conn, auth.user["id"])
    try:
        settings = json.loads(row["settings_json"]) if row else {}
    except json.JSONDecodeError:
        settings = {}
    for item in media:
        item["url"] = f"/api/attachments/{item['attachment_id']}/content?inline=1"
    return {"settings": settings, "audio": media}


@app.put("/api/user/preferences")
async def user_preferences_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    try:
        clean = _clean_preferences(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with transaction() as conn:
        now = utc_now()
        conn.execute(
            """INSERT INTO user_preferences(user_id,settings_json,version,updated_at) VALUES(?,?,1,?)
            ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,
            version=user_preferences.version+1,updated_at=excluded.updated_at""",
            (auth.user["id"], json.dumps(clean, ensure_ascii=False, separators=(",", ":")), now),
        )
        audit(conn, auth.user["id"], "update", "user_preferences", auth.user["id"], {"keys": sorted(clean)})
    return {"ok": True, "settings": clean}


@app.post("/api/user/audio")
async def user_audio_upload(request: Request, file: UploadFile = File(...), title: str = Form("")) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="音频支持 MP3、WAV、OGG、FLAC、M4A、AAC 或 WMA")
    attachment = await save_upload(file, auth.user)
    with transaction() as conn:
        now = utc_now()
        item = {
            "id": new_id("audio"), "user_id": auth.user["id"], "attachment_id": attachment["id"],
            "media_type": "audio", "title": str(title or attachment["original_name"])[:160],
            "sort_order": int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM user_media WHERE user_id=? AND media_type='audio'",
                (auth.user["id"],),
            ).fetchone()[0]),
            "active": 1, "created_at": now, "updated_at": now,
        }
        columns = list(item)
        conn.execute(
            f"INSERT INTO user_media({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(item[key] for key in columns),
        )
    item.update({"original_name": attachment["original_name"], "mime_type": attachment["mime_type"], "url": f"/api/attachments/{attachment['id']}/content?inline=1"})
    return {"ok": True, "item": item}


@app.delete("/api/user/audio/{media_id}")
async def user_audio_delete(media_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    with transaction() as conn:
        if not conn.execute("DELETE FROM user_media WHERE id=? AND user_id=?", (media_id, auth.user["id"])).rowcount:
            raise HTTPException(status_code=404, detail="音频不存在")
    return {"ok": True}


@app.get("/api/user/settings-package/export")
async def settings_package_export(
    request: Request, background_tasks: BackgroundTasks, include_media: bool = False
) -> FileResponse:
    auth = get_auth(request)
    with connect() as conn:
        preference = conn.execute("SELECT settings_json FROM user_preferences WHERE user_id=?", (auth.user["id"],)).fetchone()
        media = _user_media_items(conn, auth.user["id"])
    try:
        settings = json.loads(preference["settings_json"]) if preference else {}
    except json.JSONDecodeError:
        settings = {}
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_user_settings_", suffix=".zip", dir=TMP_DIR); os.close(fd)
    path = Path(temp_name)
    manifest = {
        "format": "YXRT_USER_SETTINGS_V1", "version": __version__, "exported_at": utc_now(),
        "owner": auth.user["username"], "settings": settings,
        "media": [{"id": item["id"], "title": item["title"], "file": f"media/{Path(item['original_name']).name}"} for item in media] if include_media else [],
        "excluded": ["密码", "会话", "发票数据", "成员隐私数据"],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("settings.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        if include_media:
            used: set[str] = set()
            for item in media:
                source = attachment_path(item["stored_name"])
                name = f"media/{Path(item['original_name']).name}"
                if source.is_file() and name.casefold() not in used:
                    used.add(name.casefold()); archive.write(source, arcname=name)
    background_tasks.add_task(path.unlink, missing_ok=True)
    filename = f"{_safe_export_stem(auth.user['username'], '用户')}软件设置.zip"
    return FileResponse(path, media_type="application/zip", filename=filename)


@app.post("/api/user/settings-package/import")
async def settings_package_import(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_user_settings_import_", suffix=".zip", dir=TMP_DIR); os.close(fd)
    path = Path(temp_name)
    try:
        with path.open("wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                target.write(chunk)
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("settings.json")
            if info.file_size > 512 * 1024:
                raise HTTPException(status_code=400, detail="设置包内容异常")
            manifest = json.loads(archive.read(info).decode("utf-8"))
            if manifest.get("format") != "YXRT_USER_SETTINGS_V1":
                raise HTTPException(status_code=400, detail="不是有效的软件设置包")
            clean = _clean_preferences(manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {})
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO user_preferences(user_id,settings_json,version,updated_at) VALUES(?,?,1,?)
                    ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,
                    version=user_preferences.version+1,updated_at=excluded.updated_at""",
                    (auth.user["id"], json.dumps(clean, ensure_ascii=False, separators=(",", ":")), utc_now()),
                )
            imported_media = 0
            media_meta = manifest.get("media") if isinstance(manifest.get("media"), list) else []
            for item in media_meta[:100]:
                member = str(item.get("file") or "") if isinstance(item, dict) else ""
                if not member.startswith("media/") or Path(member).suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    entry = archive.getinfo(member)
                    if entry.file_size > 500 * 1024 * 1024:
                        continue
                    fd_media, media_name = tempfile.mkstemp(prefix="yxrt_settings_media_", suffix=Path(member).suffix, dir=TMP_DIR); os.close(fd_media)
                    media_path = Path(media_name)
                    try:
                        with archive.open(entry) as source, media_path.open("wb") as target:
                            shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
                        attachment = save_file(media_path, Path(member).name, auth.user)
                        with transaction() as conn:
                            conn.execute(
                                """INSERT INTO user_media(id,user_id,attachment_id,media_type,title,sort_order,active,created_at,updated_at)
                                VALUES(?,?,?,'audio',?,?,1,?,?)""",
                                (new_id("audio"), auth.user["id"], attachment["id"], str(item.get("title") or Path(member).name)[:160], imported_media, utc_now(), utc_now()),
                            )
                        imported_media += 1
                    finally:
                        media_path.unlink(missing_ok=True)
                except (KeyError, OSError, sqlite3.Error):
                    continue
        return {"ok": True, "settings": clean, "imported_media": imported_media}
    except (zipfile.BadZipFile, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"设置包损坏或格式不正确：{exc}") from exc
    finally:
        path.unlink(missing_ok=True)


@app.get("/api/dashboard")
async def dashboard_endpoint(request: Request) -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        return dashboard(conn)


@app.get("/api/quality/issues")
async def quality_issues(request: Request, status: str = "open") -> dict[str, Any]:
    get_auth(request)
    if status not in {"open", "resolved"}:
        raise HTTPException(status_code=400, detail="问题状态不正确")
    with connect() as conn:
        items = list_issues(conn, status=status)
    return {"items": items, "count": len(items)}


@app.post("/api/quality/issues/{issue_id}/resolve")
async def quality_issue_resolve(issue_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    with transaction() as conn:
        if not resolve_issue(conn, issue_id):
            raise HTTPException(status_code=404, detail="问题不存在或已处理")
        audit(conn, auth.user["id"], "resolve", "quality_issue", issue_id, {})
    await hub.notify("quality_changed")
    return {"ok": True}


def _season_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    selected_id = current_season_id(conn)
    rows = [dict(row) for row in conn.execute(
        """SELECT s.*,
        (SELECT COUNT(*) FROM members m WHERE m.season_id=s.id AND m.deleted_at IS NULL) AS member_count,
        (SELECT COUNT(*) FROM invoices i WHERE i.season_id=s.id AND i.deleted_at IS NULL) AS invoice_count,
        (SELECT COUNT(*) FROM creators c WHERE c.active=1 AND c.deleted_at IS NULL) AS creator_count
        FROM seasons s WHERE s.deleted_at IS NULL ORDER BY s.sort_order DESC,s.created_at DESC"""
    ).fetchall()]
    for item in rows:
        item["is_current"] = item["id"] == selected_id
        item["is_open"] = bool(item["active"])
    return rows


@app.get("/api/seasons")
async def seasons_list(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with connect() as conn:
        items = _season_items(conn)
        if auth.user["role"] != "admin":
            items = [item for item in items if item["is_current"]]
    return {"items": items}


@app.post("/api/admin/seasons")
async def seasons_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    name = str(payload.get("name") or "").strip()
    if len(name) < 2 or len(name) > 40:
        raise HTTPException(status_code=400, detail="赛季名称需为 2 至 40 个字符")
    with transaction() as conn:
        if conn.execute(
            "SELECT 1 FROM seasons WHERE name=? COLLATE NOCASE AND deleted_at IS NULL", (name,)
        ).fetchone():
            raise HTTPException(status_code=409, detail="该赛季已经存在")
        create_snapshot(conn, auth.user["id"], "新增赛季前", name)
        now = utc_now()
        row = {
            "id": new_id("season"), "name": name, "active": 1,
            "sort_order": int(conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM seasons").fetchone()[0]),
            "created_at": now, "updated_at": now, "version": 1,
            "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(
            f"INSERT INTO seasons({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        enqueue_sync_event(conn, "seasons", row["id"], "upsert", row)
        audit(conn, auth.user["id"], "create", "season", row["id"], {"name": name})
    await hub.notify("season_list_changed")
    return row


@app.put("/api/admin/seasons/{season_id}")
async def seasons_update(season_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        current_row = conn.execute(
            "SELECT * FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)
        ).fetchone()
        if not current_row:
            raise HTTPException(status_code=404, detail="赛季不存在")
        item = dict(current_row)
        name = str(payload.get("name", item["name"]) or "").strip()
        if len(name) < 2 or len(name) > 40:
            raise HTTPException(status_code=400, detail="赛季名称需为 2 至 40 个字符")
        if conn.execute(
            "SELECT 1 FROM seasons WHERE name=? COLLATE NOCASE AND id<>? AND deleted_at IS NULL",
            (name, season_id),
        ).fetchone():
            raise HTTPException(status_code=409, detail="该赛季名称已经存在")
        active = int(bool(payload.get("active", item["active"])))
        if season_id == current_season_id(conn) and not active:
            raise HTTPException(status_code=400, detail="请先切换到其他赛季，再归档当前赛季")
        create_snapshot(conn, auth.user["id"], "修改赛季前", item["name"])
        item.update({
            "name": name, "active": active, "updated_at": utc_now(),
            "version": int(item["version"]) + 1, "device_id": get_device_id(conn),
        })
        columns = [key for key in item if key != "id"]
        conn.execute(
            f"UPDATE seasons SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
            tuple(item[key] for key in columns) + (season_id,),
        )
        enqueue_sync_event(conn, "seasons", season_id, "upsert", item)
        audit(conn, auth.user["id"], "update", "season", season_id, {"name": name, "active": bool(active)})
    await hub.notify("season_list_changed")
    return item


@app.post("/api/admin/seasons/{season_id}/switch")
async def seasons_switch(season_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        target = conn.execute(
            "SELECT * FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="赛季不存在")
        previous = current_season(conn)
        if previous["id"] != season_id:
            create_snapshot(conn, auth.user["id"], "切换赛季前", f"{previous['name']} → {target['name']}")
            set_setting(conn, "current_season_id", season_id)
            conn.execute(
                """DELETE FROM sessions WHERE user_id IN (
                SELECT u.id FROM users u LEFT JOIN members m ON m.id=u.member_id
                WHERE u.role='member' AND (m.season_id IS NULL OR m.season_id<>?)
                )""",
                (season_id,),
            )
            audit(conn, auth.user["id"], "switch", "season", season_id, {
                "from": previous["name"], "to": target["name"], "read_only": not bool(target["active"]),
            })
        item = dict(target)
        item["is_current"] = True
        item["is_open"] = bool(item["active"])
    await hub.notify("season_changed")
    return item


def _ensure_department(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    clean = str(name or "").strip()[:80]
    if not clean:
        return None
    existing = conn.execute(
        "SELECT * FROM departments WHERE name=? COLLATE NOCASE AND deleted_at IS NULL", (clean,)
    ).fetchone()
    if existing:
        return dict(existing)
    now = utc_now()
    row = {
        "id": new_id("department"), "name": clean,
        "sort_order": int(conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM departments").fetchone()[0]),
        "created_at": now, "updated_at": now, "version": 1,
        "device_id": get_device_id(conn), "deleted_at": None,
    }
    columns = list(row)
    conn.execute(
        f"INSERT INTO departments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )
    enqueue_sync_event(conn, "departments", row["id"], "upsert", row)
    return row


@app.post("/api/admin/departments")
async def departments_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="组别名称不能为空")
    with transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM departments WHERE name=? COLLATE NOCASE AND deleted_at IS NULL", (name,)
        ).fetchone()
        if existing:
            return dict(existing)
        item = _ensure_department(conn, name)
        audit(conn, auth.user["id"], "create", "department", item["id"] if item else None, {"name": name})
    await hub.notify("department_changed")
    return item or {}


def _creator_payload(conn: sqlite3.Connection, creator_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT c.*,s.name AS origin_season_name,'全赛季通用' AS season_name
        FROM creators c JOIN seasons s ON s.id=c.season_id
        WHERE c.id=? AND c.deleted_at IS NULL""", (creator_id,)
    ).fetchone()
    return dict(row) if row else None


@app.get("/api/creators")
async def creators_list(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with connect() as conn:
        active_clause = "" if auth.user["role"] == "admin" else "AND c.active=1"
        items = [dict(row) for row in conn.execute(
            f"""SELECT c.*,s.name AS origin_season_name,'全赛季通用' AS season_name
            FROM creators c JOIN seasons s ON s.id=c.season_id
            WHERE c.deleted_at IS NULL {active_clause}
            ORDER BY c.active DESC,c.sort_order,c.department,c.name"""
        ).fetchall()]
    return {"items": items}


@app.get("/api/public/creators")
async def creators_public() -> dict[str, Any]:
    with connect() as conn:
        items = [dict(row) for row in conn.execute(
            """SELECT c.name,c.department,c.role_title,c.note,c.sort_order,
            s.name AS origin_season_name,'全赛季通用' AS season_name
            FROM creators c JOIN seasons s ON s.id=c.season_id
            WHERE c.active=1 AND c.deleted_at IS NULL
            ORDER BY c.sort_order,c.department,c.name"""
        ).fetchall()]
    return {"season": "全赛季通用", "items": items}


@app.post("/api/admin/creators")
async def creators_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="创作者姓名不能为空")
    with transaction() as conn:
        season_id = current_season_id(conn)
        department = str(payload.get("department") or "").strip()[:80]
        _ensure_department(conn, department)
        now = utc_now()
        row = {
            "id": new_id("creator"), "season_id": season_id, "name": name[:80],
            "department": department, "role_title": str(payload.get("role_title") or "").strip()[:80],
            "note": str(payload.get("note") or "").strip()[:500],
            "active": int(bool(payload.get("active", True))),
            "sort_order": int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM creators"
            ).fetchone()[0]),
            "created_at": now, "updated_at": now, "version": 1,
            "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(
            f"INSERT INTO creators({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        enqueue_sync_event(conn, "creators", row["id"], "upsert", row)
        audit(conn, auth.user["id"], "create", "creator", row["id"], {"name": name})
        item = _creator_payload(conn, row["id"])
    await hub.notify("creator_changed")
    return item or row


@app.put("/api/admin/creators/{creator_id}")
async def creators_update(creator_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        current_row = conn.execute(
            "SELECT * FROM creators WHERE id=? AND deleted_at IS NULL", (creator_id,)
        ).fetchone()
        if not current_row:
            raise HTTPException(status_code=404, detail="创作者记录不存在")
        item = dict(current_row)
        name = str(payload.get("name", item["name"]) or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="创作者姓名不能为空")
        department = str(payload.get("department", item["department"]) or "").strip()[:80]
        _ensure_department(conn, department)
        item.update({
            "name": name[:80], "department": department,
            "role_title": str(payload.get("role_title", item["role_title"]) or "").strip()[:80],
            "note": str(payload.get("note", item["note"]) or "").strip()[:500],
            "active": int(bool(payload.get("active", item["active"]))),
            "sort_order": int(payload.get("sort_order", item["sort_order"])),
            "updated_at": utc_now(), "version": int(item["version"]) + 1, "device_id": get_device_id(conn),
        })
        columns = [key for key in item if key != "id"]
        conn.execute(
            f"UPDATE creators SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
            tuple(item[key] for key in columns) + (creator_id,),
        )
        enqueue_sync_event(conn, "creators", creator_id, "upsert", item)
        audit(conn, auth.user["id"], "update", "creator", creator_id, {"name": name})
        result = _creator_payload(conn, creator_id)
    await hub.notify("creator_changed")
    return result or item


@app.delete("/api/admin/creators/{creator_id}")
async def creators_delete(creator_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        current_row = conn.execute(
            "SELECT * FROM creators WHERE id=? AND deleted_at IS NULL", (creator_id,)
        ).fetchone()
        if not current_row:
            raise HTTPException(status_code=404, detail="创作者记录不存在")
        item = dict(current_row)
        item.update({
            "deleted_at": utc_now(), "updated_at": utc_now(),
            "version": int(item["version"]) + 1, "device_id": get_device_id(conn),
        })
        conn.execute(
            "UPDATE creators SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
            (item["deleted_at"], item["updated_at"], item["version"], item["device_id"], creator_id),
        )
        enqueue_sync_event(conn, "creators", creator_id, "delete", item)
        audit(conn, auth.user["id"], "delete", "creator", creator_id, {"name": item["name"]})
    await hub.notify("creator_changed")
    return {"ok": True}


@app.get("/api/invoices")
async def invoices_list(
    request: Request,
    search: str = "",
    status: str = "",
    category_id: str = "",
    source_id: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        items = list_invoices(
            conn, search=search, status=status, category_id=category_id,
            source_id=source_id, date_from=date_from, date_to=date_to, limit=limit,
        )
    return {"items": items, "count": len(items)}


@app.get("/api/invoices/{invoice_id}")
async def invoice_get(invoice_id: str, request: Request) -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        item = invoice_payload(conn, invoice_id)
    if not item or item.get("deleted_at"):
        raise HTTPException(status_code=404, detail="未找到该发票记录")
    return item


@app.post("/api/invoices")
async def invoice_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    item = save_invoice(payload, auth.user)
    if str(payload.get("ocr_status") or "") == "recognized":
        with transaction() as conn:
            sync_ocr_issues(conn, item["id"], {
                "ocr_confidence": payload.get("ocr_confidence", 0),
                "uncertain_fields": payload.get("uncertain_fields") or [],
                "field_confidences": payload.get("field_confidences") or {},
            }, user_id=auth.user["id"])
    await hub.notify()
    return item


@app.put("/api/invoices/{invoice_id}")
async def invoice_update(invoice_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    item = save_invoice(payload, auth.user, invoice_id)
    with transaction() as conn:
        conn.execute(
            """UPDATE invoice_quality_issues SET status='resolved',updated_at=?
            WHERE invoice_id=? AND status='open' AND issue_type IN ('low_confidence','ocr_failed','import_failed')""",
            (utc_now(), invoice_id),
        )
    await hub.notify()
    return item


@app.delete("/api/invoices/{invoice_id}")
async def invoice_delete(invoice_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    delete_invoice(invoice_id, auth.user)
    await hub.notify()
    return {"ok": True}


@app.post("/api/invoices/batch-action")
async def invoices_batch_action(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    result = batch_update_invoices(payload, auth.user)
    await hub.notify()
    return result


@app.get("/api/settlements/summary")
async def settlements_summary(request: Request) -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        season_id = current_season_id(conn)
        result = settlement_summary(conn)
        result["history"] = [dict(row) for row in conn.execute(
            """SELECT s.*,fm.name AS from_name,tm.name AS to_name,u.display_name AS created_by_name
            FROM settlements s JOIN members fm ON fm.id=s.from_member_id JOIN members tm ON tm.id=s.to_member_id
            LEFT JOIN users u ON u.id=s.created_by
            WHERE s.season_id=? AND s.deleted_at IS NULL ORDER BY s.created_at DESC LIMIT 200""",
            (season_id,),
        ).fetchall()]
        for item in result["history"]:
            item["amount"] = yuan(item.pop("amount_cents"))
        return result


@app.post("/api/settlements")
async def settlements_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    item = record_settlement(payload, auth.user)
    await hub.notify()
    return item


@app.delete("/api/settlements/{settlement_id}")
async def settlements_delete(settlement_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    with transaction() as conn:
        season_id = current_season_id(conn)
        row = conn.execute(
            "SELECT * FROM settlements WHERE id=? AND season_id=? AND deleted_at IS NULL", (settlement_id, season_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到该结算记录")
        create_snapshot(conn, auth.user["id"], "删除结算记录前", "成员AA结算")
        item = dict(row)
        item.update({"deleted_at": utc_now(), "updated_at": utc_now(), "version": int(item["version"]) + 1, "device_id": get_device_id(conn)})
        conn.execute("UPDATE settlements SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
                     (item["deleted_at"], item["updated_at"], item["version"], item["device_id"], settlement_id))
        enqueue_sync_event(conn, "settlements", settlement_id, "delete", item)
        audit(conn, auth.user["id"], "delete", "settlement", settlement_id, {})
    await hub.notify()
    return {"ok": True}


@app.get("/api/admin/users")
async def users_list(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    with connect() as conn:
        season_id = current_season_id(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT u.id,u.member_id,u.username,u.display_name,u.role,u.active,u.must_change_password,
            u.created_at,u.updated_at,u.version,m.name AS member_name,m.department
            FROM users u LEFT JOIN members m ON m.id=u.member_id
            WHERE u.deleted_at IS NULL AND (u.role<>'member' OR m.season_id=?)
            ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'member' THEN 1 ELSE 2 END,u.username""",
            (season_id,),
        ).fetchall()]
    return {"items": items}


@app.post("/api/admin/users")
async def users_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    require_write(auth)
    try:
        username = validate_username(str(payload.get("username") or ""))
        password = str(payload.get("password") or "Member@2026")
        validate_new_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    role = str(payload.get("role") or "member")
    if role not in {"admin", "member", "viewer"}:
        raise HTTPException(status_code=400, detail="账号角色不正确")
    with transaction() as conn:
        season_id = current_season_id(conn)
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE AND deleted_at IS NULL", (username,)).fetchone():
            raise HTTPException(status_code=409, detail="该账号已存在")
        member_id = str(payload.get("member_id") or "") or None
        if role == "member" and not member_id:
            raise HTTPException(status_code=400, detail="成员账号必须关联当前赛季成员")
        if member_id and not conn.execute(
            "SELECT 1 FROM members WHERE id=? AND season_id=? AND deleted_at IS NULL", (member_id, season_id)
        ).fetchone():
            raise HTTPException(status_code=400, detail="关联成员不存在")
        if role in {"admin", "viewer"}:
            member_id = None
        now = utc_now()
        row = {
            "id": new_id("user"), "member_id": member_id, "username": username,
            "display_name": str(payload.get("display_name") or username)[:80], "password_hash": hash_password(password),
            "role": role, "active": 1, "must_change_password": int(bool(payload.get("must_change_password", True))),
            "created_at": now, "updated_at": now, "version": 1, "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(f"INSERT INTO users({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                     tuple(row[column] for column in columns))
        enqueue_sync_event(conn, "users", row["id"], "upsert", row)
        audit(conn, auth.user["id"], "create", "user", row["id"], {"username": username, "role": role})
    await hub.notify()
    return {key: value for key, value in row.items() if key != "password_hash"}


@app.put("/api/admin/users/{user_id}")
async def users_update(user_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    require_write(auth)
    with transaction() as conn:
        season_id = current_season_id(conn)
        current_row = conn.execute(
            """SELECT u.* FROM users u LEFT JOIN members m ON m.id=u.member_id
            WHERE u.id=? AND u.deleted_at IS NULL AND (u.role<>'member' OR m.season_id=?)""",
            (user_id, season_id),
        ).fetchone()
        if not current_row:
            raise HTTPException(status_code=404, detail="账号不存在")
        current = dict(current_row)
        try:
            username = validate_username(str(payload.get("username") or current["username"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE AND id<>? AND deleted_at IS NULL", (username, user_id)).fetchone():
            raise HTTPException(status_code=409, detail="该账号已被使用")
        role = str(payload.get("role") or current["role"])
        if role not in {"admin", "member", "viewer"}:
            raise HTTPException(status_code=400, detail="账号角色不正确")
        if user_id == auth.user["id"] and (role != "admin" or not bool(payload.get("active", True))):
            raise HTTPException(status_code=400, detail="管理员不能停用或降低自己的权限")
        member_id = str(payload.get("member_id") or "") or None
        if role == "member" and not member_id:
            raise HTTPException(status_code=400, detail="成员账号必须关联当前赛季成员")
        if member_id and not conn.execute(
            "SELECT 1 FROM members WHERE id=? AND season_id=? AND deleted_at IS NULL", (member_id, season_id)
        ).fetchone():
            raise HTTPException(status_code=400, detail="关联成员不存在")
        if role in {"admin", "viewer"}:
            member_id = None
        now = utc_now()
        current.update({
            "member_id": member_id, "username": username,
            "display_name": str(payload.get("display_name") or current["display_name"])[:80],
            "role": role, "active": int(bool(payload.get("active", current["active"]))),
            "updated_at": now, "version": int(current["version"]) + 1, "device_id": get_device_id(conn),
        })
        if payload.get("password"):
            try:
                validate_new_password(str(payload["password"]))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            current["password_hash"] = hash_password(str(payload["password"]))
            current["must_change_password"] = int(bool(payload.get("must_change_password", True)))
        columns = [key for key in current if key != "id"]
        conn.execute(f"UPDATE users SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
                     tuple(current[key] for key in columns) + (user_id,))
        enqueue_sync_event(conn, "users", user_id, "upsert", current)
        audit(conn, auth.user["id"], "update", "user", user_id, {"username": username, "role": role})
    await hub.notify()
    return {key: value for key, value in current.items() if key != "password_hash"}


@app.post("/api/members")
async def members_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    require_write(auth)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="成员姓名不能为空")
    with transaction() as conn:
        season_id = current_season_id(conn)
        create_snapshot(conn, auth.user["id"], "新增成员前", name)
        now = utc_now()
        department = str(payload.get("department") or "").strip()[:80]
        _ensure_department(conn, department)
        row = {
            "id": new_id("member"), "season_id": season_id,
            "name": name[:60], "department": department,
            "student_id": str(payload.get("student_id") or "")[:40], "phone": str(payload.get("phone") or "")[:30],
            "email": str(payload.get("email") or "")[:120], "avatar_color": str(payload.get("avatar_color") or "#27d3ff")[:20],
            "active": 1, "sort_order": int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM members WHERE season_id=?", (season_id,)
            ).fetchone()[0]),
            "created_at": now, "updated_at": now, "version": 1, "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(f"INSERT INTO members({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                     tuple(row[column] for column in columns))
        enqueue_sync_event(conn, "members", row["id"], "upsert", row)
        audit(conn, auth.user["id"], "create", "member", row["id"], {"name": name})
    await hub.notify()
    return row


@app.put("/api/members/{member_id}")
async def members_update(member_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    require_write(auth)
    with transaction() as conn:
        season_id = current_season_id(conn)
        row = conn.execute(
            "SELECT * FROM members WHERE id=? AND season_id=? AND deleted_at IS NULL", (member_id, season_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="成员不存在")
        item = dict(row)
        name = str(payload.get("name") or item["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="成员姓名不能为空")
        create_snapshot(conn, auth.user["id"], "修改成员前", item["name"])
        department = str(payload.get("department", item["department"]) or "").strip()[:80]
        _ensure_department(conn, department)
        item.update({
            "name": name[:60], "department": department,
            "student_id": str(payload.get("student_id", item["student_id"]))[:40],
            "phone": str(payload.get("phone", item["phone"]))[:30], "email": str(payload.get("email", item["email"]))[:120],
            "avatar_color": str(payload.get("avatar_color", item["avatar_color"]))[:20],
            "active": int(bool(payload.get("active", item["active"]))), "sort_order": int(payload.get("sort_order", item["sort_order"])),
            "updated_at": utc_now(), "version": int(item["version"]) + 1, "device_id": get_device_id(conn),
        })
        columns = [key for key in item if key != "id"]
        conn.execute(f"UPDATE members SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
                     tuple(item[key] for key in columns) + (member_id,))
        conn.execute("UPDATE users SET display_name=?,updated_at=?,version=version+1,device_id=? WHERE member_id=?",
                     (item["name"], item["updated_at"], item["device_id"], member_id))
        enqueue_sync_event(conn, "members", member_id, "upsert", item)
        for user_row in conn.execute("SELECT * FROM users WHERE member_id=?", (member_id,)).fetchall():
            enqueue_sync_event(conn, "users", user_row["id"], "upsert", dict(user_row))
        audit(conn, auth.user["id"], "update", "member", member_id, {"name": item["name"]})
    await hub.notify()
    return item


@app.post("/api/members/{member_id}/avatar")
async def member_avatar_upload(member_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    if auth.user["role"] != "admin" and auth.user.get("member_id") != member_id:
        raise HTTPException(status_code=403, detail="只能修改自己的头像")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in APPEARANCE_IMAGE_EXTENSIONS or str(file.content_type or "").startswith("video/"):
        raise HTTPException(status_code=400, detail="头像仅支持 JPG、PNG、GIF 或 WEBP 图片")
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM members WHERE id=? AND season_id=? AND deleted_at IS NULL",
            (member_id, current_season_id(conn)),
        ).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="成员不存在")
    attachment = await save_upload(file, auth.user)
    with transaction() as conn:
        now = utc_now()
        conn.execute(
            "UPDATE members SET avatar_attachment_id=?,updated_at=?,version=version+1,device_id=? WHERE id=?",
            (attachment["id"], now, get_device_id(conn), member_id),
        )
        audit(conn, auth.user["id"], "update_avatar", "member", member_id, {"name": attachment["original_name"]})
    await hub.notify("member_changed")
    return {"ok": True, "attachment_id": attachment["id"], "avatar_url": f"/api/attachments/{attachment['id']}/content?inline=1"}


@app.delete("/api/members/{member_id}")
async def members_archive(member_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    require_write(auth)
    with transaction() as conn:
        season_id = current_season_id(conn)
        row = conn.execute(
            "SELECT * FROM members WHERE id=? AND season_id=? AND deleted_at IS NULL", (member_id, season_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="成员不存在")
        if conn.execute(
            "SELECT COUNT(*) FROM members WHERE season_id=? AND active=1 AND deleted_at IS NULL", (season_id,)
        ).fetchone()[0] <= 1:
            raise HTTPException(status_code=400, detail="至少需要保留一名有效成员")
        create_snapshot(conn, auth.user["id"], "停用成员前", row["name"])
        item = dict(row)
        item.update({"active": 0, "updated_at": utc_now(), "version": int(item["version"]) + 1, "device_id": get_device_id(conn)})
        conn.execute("UPDATE members SET active=0,updated_at=?,version=?,device_id=? WHERE id=?",
                     (item["updated_at"], item["version"], item["device_id"], member_id))
        conn.execute("UPDATE users SET active=0,updated_at=?,version=version+1,device_id=? WHERE member_id=?",
                     (item["updated_at"], item["device_id"], member_id))
        enqueue_sync_event(conn, "members", member_id, "upsert", item)
        for user_row in conn.execute("SELECT * FROM users WHERE member_id=?", (member_id,)).fetchall():
            enqueue_sync_event(conn, "users", user_row["id"], "upsert", dict(user_row))
        audit(conn, auth.user["id"], "archive", "member", member_id, {"name": row["name"]})
    await hub.notify()
    return {"ok": True}


async def _save_reference_item(table: str, payload: dict[str, Any], auth: AuthContext, item_id: str | None = None) -> dict[str, Any]:
    require_write(auth)
    if table not in {"categories", "funding_sources"}:
        raise HTTPException(status_code=400, detail="不支持的数据类型")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    with transaction() as conn:
        existing = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone() if item_id else None
        if conn.execute(f"SELECT 1 FROM {table} WHERE name=? COLLATE NOCASE AND id<>? AND deleted_at IS NULL", (name, item_id or "")).fetchone():
            raise HTTPException(status_code=409, detail="该名称已存在")
        now = utc_now()
        if existing:
            row = dict(existing)
            row.update({"name": name[:80], "color": str(payload.get("color") or row["color"])[:20],
                        "active": int(bool(payload.get("active", row["active"]))), "sort_order": int(payload.get("sort_order", row["sort_order"])),
                        "updated_at": now, "version": int(row["version"]) + 1, "device_id": get_device_id(conn), "deleted_at": None})
            if table == "funding_sources":
                row["source_type"] = str(payload.get("source_type") or row["source_type"])[:40]
        else:
            row = {"id": new_id("category" if table == "categories" else "source"), "name": name[:80],
                   "color": str(payload.get("color") or "#27d3ff")[:20], "active": 1,
                   "sort_order": int(conn.execute(f"SELECT COALESCE(MAX(sort_order),-1)+1 FROM {table}").fetchone()[0]),
                   "created_at": now, "updated_at": now, "version": 1, "device_id": get_device_id(conn), "deleted_at": None}
            if table == "funding_sources":
                row["source_type"] = str(payload.get("source_type") or "other")[:40]
        columns = list(row)
        conn.execute(f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                     tuple(row[column] for column in columns))
        enqueue_sync_event(conn, table, row["id"], "upsert", row)
        audit(conn, auth.user["id"], "update" if existing else "create", table, row["id"], {"name": name})
    await hub.notify()
    return row


@app.post("/api/categories")
async def categories_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    return await _save_reference_item("categories", payload, auth)


@app.put("/api/categories/{item_id}")
async def categories_update(item_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    return await _save_reference_item("categories", payload, auth, item_id)


@app.post("/api/funding-sources")
async def sources_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    return await _save_reference_item("funding_sources", payload, auth)


@app.put("/api/funding-sources/{item_id}")
async def sources_update(item_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    return await _save_reference_item("funding_sources", payload, auth, item_id)


@app.post("/api/attachments")
async def attachment_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    item = await save_upload(file, auth.user)
    return {"attachment": item}


@app.get("/api/attachments/{attachment_id}/content")
async def attachment_content(attachment_id: str, request: Request, inline: bool = False) -> FileResponse:
    auth = get_auth(request)
    with connect() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL", (attachment_id,)).fetchone()
        if row and row["season_id"] != current_season_id(conn) and attachment_id not in _configured_public_media_ids(conn):
            owns_media = conn.execute(
                "SELECT 1 FROM user_media WHERE attachment_id=? AND user_id=?",
                (attachment_id, auth.user["id"]),
            ).fetchone()
            avatar = conn.execute(
                """SELECT 1 FROM members m LEFT JOIN users u ON u.member_id=m.id
                WHERE m.avatar_attachment_id=? AND (u.id=? OR ?='admin')""",
                (attachment_id, auth.user["id"], auth.user["role"]),
            ).fetchone()
            if not owns_media and not avatar:
                row = None
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = attachment_path(row["stored_name"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="附件文件尚未同步到本机")
    if inline:
        safe_name = quote(str(row["original_name"]), safe="")
        return FileResponse(
            path,
            media_type=row["mime_type"],
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{safe_name}"},
        )
    return FileResponse(path, media_type=row["mime_type"], filename=row["original_name"])


@app.post("/api/attachments/{attachment_id}/open")
async def attachment_open_local(attachment_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    client_host = request.client.host if request.client else ""
    if APP_MODE != "desktop" or client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="仅桌面本机可以直接打开源文件")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM attachments WHERE id=? AND season_id=? AND deleted_at IS NULL",
            (attachment_id, current_season_id(conn)),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = attachment_path(row["stored_name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="附件文件尚未同步到本机")
    try:
        await asyncio.to_thread(open_local_file, path)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法使用电脑默认程序打开：{exc}") from exc
    return {"ok": True, "name": row["original_name"]}


@app.post("/api/invoices/{invoice_id}/supporting-attachments")
async def supporting_attachment_upload(
    invoice_id: str,
    request: Request,
    file: UploadFile = File(...),
    attachment_kind: str = Form("other"),
    label: str = Form(""),
) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    if attachment_kind not in {"shopping", "signature", "other"}:
        raise HTTPException(status_code=400, detail="证明附件类型不正确")
    with connect() as conn:
        invoice = conn.execute(
            "SELECT id FROM invoices WHERE id=? AND season_id=? AND deleted_at IS NULL",
            (invoice_id, current_season_id(conn)),
        ).fetchone()
    if not invoice:
        raise HTTPException(status_code=404, detail="发票不存在")
    attachment = await save_upload(file, auth.user)
    with transaction() as conn:
        now = utc_now()
        relation_id = new_id("support")
        existing = conn.execute(
            "SELECT id FROM invoice_supporting_attachments WHERE invoice_id=? AND attachment_id=?",
            (invoice_id, attachment["id"]),
        ).fetchone()
        if existing:
            relation_id = str(existing["id"])
            conn.execute(
                "UPDATE invoice_supporting_attachments SET attachment_kind=?,label=?,updated_at=? WHERE id=?",
                (attachment_kind, str(label or "")[:160], now, relation_id),
            )
        else:
            sort_order = int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM invoice_supporting_attachments WHERE invoice_id=?",
                (invoice_id,),
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO invoice_supporting_attachments(
                id,season_id,invoice_id,attachment_id,attachment_kind,label,sort_order,created_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    relation_id, current_season_id(conn), invoice_id, attachment["id"], attachment_kind,
                    str(label or "")[:160], sort_order, auth.user["id"], now, now,
                ),
            )
        audit(conn, auth.user["id"], "attach_supporting_file", "invoice", invoice_id, {"name": attachment["original_name"]})
    await hub.notify("invoice_changed")
    return {"ok": True, "relation_id": relation_id, "attachment": attachment}


@app.delete("/api/invoices/{invoice_id}/supporting-attachments/{relation_id}")
async def supporting_attachment_delete(invoice_id: str, relation_id: str, request: Request) -> dict[str, bool]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    with transaction() as conn:
        row = conn.execute(
            """SELECT r.id FROM invoice_supporting_attachments r JOIN invoices i ON i.id=r.invoice_id
            WHERE r.id=? AND r.invoice_id=? AND i.season_id=? AND i.deleted_at IS NULL""",
            (relation_id, invoice_id, current_season_id(conn)),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="证明附件不存在")
        conn.execute("DELETE FROM invoice_supporting_attachments WHERE id=?", (relation_id,))
        audit(conn, auth.user["id"], "remove_supporting_file", "invoice", invoice_id, {"relation_id": relation_id})
    return {"ok": True}


def _create_import_drafts(
    attachments: list[dict[str, Any]], user: dict[str, Any], *, category_id: str,
    payer_member_id: str, burden_type: str, funding_source_id: str,
    split_member_ids: list[str], note: str,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    with transaction() as conn:
        season_id = current_season_id(conn)
        active_ids = [row[0] for row in conn.execute(
            """SELECT id FROM members WHERE season_id=? AND active=1 AND deleted_at IS NULL
            ORDER BY sort_order,name""", (season_id,)
        ).fetchall()]
        if not active_ids:
            raise BusinessError("没有可用成员")
        payer_member_id = payer_member_id if payer_member_id in active_ids else (user.get("member_id") if user.get("member_id") in active_ids else active_ids[0])
        burden_type = burden_type if burden_type in {"team_aa", "self_paid", "specified_split"} else "team_aa"
        selected = [value for value in split_member_ids if value in active_ids]
        if burden_type == "team_aa": selected = active_ids
        if burden_type == "self_paid": selected = [payer_member_id]
        if not selected: selected = active_ids
        category_id = category_id if conn.execute("SELECT 1 FROM categories WHERE id=? AND deleted_at IS NULL", (category_id,)).fetchone() else None
        funding_source_id = funding_source_id if conn.execute("SELECT 1 FROM funding_sources WHERE id=? AND deleted_at IS NULL", (funding_source_id,)).fetchone() else None
        create_snapshot(conn, user["id"], "批量导入前", f"准备导入 {len(attachments)} 个附件")
        now = utc_now()
        device_id = get_device_id(conn)
        for attachment in attachments:
            if attachment.get("season_id") != season_id:
                raise BusinessError("导入附件不属于当前赛季")
            invoice_id = new_id("invoice")
            row = {
                "id": invoice_id, "season_id": season_id,
                "invoice_no": "", "vendor": "待 OCR 识别", "invoice_date": now[:10],
                "total_amount_cents": 0, "tax_amount_cents": 0, "category_id": category_id,
                "product_type": "其他", "payer_member_id": payer_member_id, "burden_type": burden_type,
                "reimbursement_status": "pending", "reimbursed_amount_cents": 0, "reimbursement_date": None,
                "funding_source_id": funding_source_id, "note": note[:2000], "attachment_id": attachment["id"],
                "ocr_text": "", "ocr_confidence": 0, "ocr_status": "queued", "is_demo": 0,
                "created_by": user["id"], "created_at": now, "updated_at": now, "version": 1,
                "device_id": device_id, "deleted_at": None,
            }
            columns = list(row)
            conn.execute(f"INSERT INTO invoices({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                         tuple(row[column] for column in columns))
            enqueue_sync_event(conn, "invoices", invoice_id, "upsert", row)
            for member_id in selected:
                split = {"id": new_id("split"), "season_id": season_id,
                         "invoice_id": invoice_id, "member_id": member_id, "share_cents": 0,
                         "paid_cents": 0, "status": "pending", "created_at": now, "updated_at": now,
                         "version": 1, "device_id": device_id, "deleted_at": None}
                split_columns = list(split)
                conn.execute(f"INSERT INTO invoice_splits({','.join(split_columns)}) VALUES({','.join('?' for _ in split_columns)})",
                             tuple(split[column] for column in split_columns))
                enqueue_sync_event(conn, "invoice_splits", split["id"], "upsert", split)
            audit(conn, user["id"], "batch_import", "invoice", invoice_id, {"attachment": attachment["original_name"]})
            drafts.append(row)
    return drafts


def _start_import_jobs(
    attachments: list[dict[str, Any]], drafts: list[dict[str, Any]], user: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    jobs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for attachment, draft in zip(attachments, drafts):
        try:
            jobs.append({
                "attachment_id": attachment["id"], "invoice_id": draft["id"],
                "job_id": create_ocr_job(attachment["id"], user["id"], draft["id"]),
            })
        except Exception as exc:
            message = f"识别队列创建失败：{str(exc)[:500]}"
            failures.append({"file_name": attachment.get("original_name", ""), "reason": message})
            with transaction() as conn:
                record_issue(
                    conn, "import_failed", message, invoice_id=draft["id"], severity="error",
                    details={"file_name": attachment.get("original_name", "")}, user_id=user["id"],
                )
    return jobs, failures


def _record_import_skips(skipped: list[dict[str, str]], user_id: str) -> None:
    if not skipped:
        return
    with transaction() as conn:
        for item in skipped[:500]:
            record_issue(
                conn, "import_failed", f"{item.get('file_name') or '文件'}：{item.get('reason') or '导入失败'}",
                severity="error", details=item, user_id=user_id,
            )


@app.post("/api/import/zip")
async def import_zip(
    request: Request,
    file: UploadFile = File(...),
    category_id: str = Form(""),
    payer_member_id: str = Form(""),
    burden_type: str = Form("team_aa"),
    funding_source_id: str = Form(""),
    split_member_ids: str = Form("[]"),
    note: str = Form(""),
) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="请选择 ZIP 压缩包")
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_batch_", suffix=".zip", dir=TMP_DIR)
    os.close(fd)
    try:
        with open(temp_name, "wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                target.write(chunk)
        attachments, skipped = await asyncio.to_thread(extract_zip, Path(temp_name), auth.user)
        try:
            selected_ids = json.loads(split_member_ids)
            if not isinstance(selected_ids, list): selected_ids = []
        except json.JSONDecodeError:
            selected_ids = []
        drafts = _create_import_drafts(
            attachments, auth.user, category_id=category_id, payer_member_id=payer_member_id,
            burden_type=burden_type, funding_source_id=funding_source_id,
            split_member_ids=[str(value) for value in selected_ids], note=note,
        )
        jobs, job_failures = _start_import_jobs(attachments, drafts, auth.user)
        skipped.extend(job_failures)
        _record_import_skips(skipped, auth.user["id"])
        await hub.notify()
        return {"attachments": attachments, "drafts": drafts, "jobs": jobs, "skipped": skipped, "count": len(attachments)}
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="ZIP 压缩包损坏或格式不正确") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        Path(temp_name).unlink(missing_ok=True)


@app.post("/api/import/files")
async def import_files(
    request: Request,
    files: list[UploadFile] = File(...),
    category_id: str = Form(""),
    payer_member_id: str = Form(""),
    burden_type: str = Form("team_aa"),
    funding_source_id: str = Form(""),
    split_member_ids: str = Form("[]"),
    note: str = Form(""),
) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    attachments: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for file in files:
        filename = Path(file.filename or "attachment").name
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped.append({"file_name": filename, "reason": "不是支持的发票文件格式"})
            continue
        try:
            attachments.append(await save_upload(file, auth.user))
        except Exception as exc:
            skipped.append({"file_name": filename, "reason": str(exc)[:300]})
    if not attachments:
        raise HTTPException(status_code=400, detail="没有可导入的发票文件")
    try:
        selected_ids = json.loads(split_member_ids)
        if not isinstance(selected_ids, list):
            selected_ids = []
    except json.JSONDecodeError:
        selected_ids = []
    drafts = _create_import_drafts(
        attachments, auth.user, category_id=category_id, payer_member_id=payer_member_id,
        burden_type=burden_type, funding_source_id=funding_source_id,
        split_member_ids=[str(value) for value in selected_ids], note=note,
    )
    jobs, job_failures = _start_import_jobs(attachments, drafts, auth.user)
    skipped.extend(job_failures)
    _record_import_skips(skipped, auth.user["id"])
    await hub.notify()
    return {"attachments": attachments, "drafts": drafts, "jobs": jobs, "skipped": skipped, "count": len(attachments)}


@app.post("/api/ocr/parse-text")
async def ocr_parse_text(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    result = parse_invoice_text(str(payload.get("text") or ""), 0.5)
    with connect() as conn:
        result.update(classify_invoice(
            conn,
            str(result.get("ocr_text") or ""),
            vendor=str(result.get("vendor") or ""),
            detected_product_type=str(result.get("product_type") or "其他"),
        ))
    return result


@app.post("/api/ocr/{attachment_id}")
async def ocr_start(attachment_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    try:
        job_id = create_ocr_job(attachment_id, auth.user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/ocr/jobs/{job_id}")
async def ocr_status(job_id: str, request: Request) -> dict[str, Any]:
    get_auth(request)
    job = get_ocr_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="OCR 任务不存在")
    return job


@app.get("/api/audit-logs")
async def audit_logs(request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    with connect() as conn:
        season_id = current_season_id(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT l.*,u.display_name AS user_name FROM audit_logs l LEFT JOIN users u ON u.id=l.user_id
            WHERE l.season_id=? ORDER BY l.created_at DESC LIMIT ?""", (season_id, limit)
        ).fetchall()]
    for item in items:
        try:
            item["detail"] = json.loads(item.pop("detail_json"))
        except json.JSONDecodeError:
            item["detail"] = {}
    return {"items": items}


@app.get("/api/admin/snapshots")
async def snapshots_list(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    with connect() as conn:
        season_id = current_season_id(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT s.id,s.label,s.reason,s.created_at,s.source_device_id,u.display_name AS created_by_name,
            length(s.state_gzip) AS size_bytes FROM snapshots s LEFT JOIN users u ON u.id=s.created_by
            WHERE s.season_id=? ORDER BY s.created_at DESC LIMIT 100""", (season_id,)
        ).fetchall()]
    return {"items": items}


@app.post("/api/admin/snapshots")
async def snapshots_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        snapshot_id = create_snapshot(conn, auth.user["id"], str(payload.get("label") or "管理员手动版本"), str(payload.get("reason") or ""))
        audit(conn, auth.user["id"], "create", "snapshot", snapshot_id, {})
    return {"ok": True, "id": snapshot_id}


@app.post("/api/admin/snapshots/{snapshot_id}/restore")
async def snapshots_restore(snapshot_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        with transaction() as conn:
            restored = restore_snapshot(conn, snapshot_id, auth.user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await hub.notify("restored")
    return {
        "ok": True,
        "message": (
            f"历史版本已恢复：{restored['invoices']} 张发票、{restored['members']} 名成员、"
            f"{restored['attachments']} 个附件、{restored['stories']} 篇故事；恢复前状态已自动保存"
        ),
        "restored": restored,
        "requires_relogin": True,
    }


@app.delete("/api/admin/demo-data")
async def demo_delete(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    count = delete_demo_data(auth.user)
    await hub.notify()
    return {"ok": True, "deleted_count": count}


@app.delete("/api/admin/current-season-data")
async def current_season_data_delete(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with connect() as conn:
        season = current_season(conn)
    safe_season = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(season["name"]))[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = RUNTIME_HOME / "backups" / f"清除{safe_season}数据前完整备份_{stamp}.zip"
    try:
        await asyncio.to_thread(_create_backup_archive, backup_path)
        result = await asyncio.to_thread(
            clear_current_season_data, auth.user["id"], auth.session["id"]
        )
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"清除失败，数据未完整修改：{exc}") from exc
    await hub.notify("season_cleared")
    result.update({
        "ok": True,
        "backup_path": str(backup_path),
        "message": f"{season['name']}数据已清除，当前管理员账号和公共查看账号已保留",
    })
    return result


@app.put("/api/admin/settings")
async def settings_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    allowed = {
        "team_name", "background_image", "background_media_id", "background_overlay", "accent_color",
        "login_slideshow_enabled", "login_slides", "login_transition", "loading_cars",
        "login_panel_opacity", "sidebar_transparency", "topbar_transparency", "login_random_enabled", "global_background_random",
    }
    with transaction() as conn:
        create_snapshot(conn, auth.user["id"], "界面设置修改前", "全队显示设置")
        for key in allowed:
            if key in payload:
                value: str
                if key == "background_overlay":
                    try:
                        value = str(max(0.2, min(0.98, float(payload[key]))))
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail="背景遮罩值不正确") from None
                elif key == "accent_color":
                    value = str(payload[key])
                    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                        raise HTTPException(status_code=400, detail="主题颜色格式不正确")
                elif key == "team_name":
                    value = str(payload[key]).strip()[:80] or "燕翔车队 Racing Team"
                elif key in {"login_slideshow_enabled", "login_random_enabled", "global_background_random"}:
                    value = "1" if bool(payload[key]) else "0"
                elif key == "login_panel_opacity":
                    try:
                        value = str(max(0.35, min(1.0, float(payload[key]))))
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail="登录面板透明度不正确") from None
                elif key in {"sidebar_transparency", "topbar_transparency"}:
                    try:
                        value = str(max(0.0, min(1.0, float(payload[key]))))
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail="导航透明度不正确") from None
                elif key == "login_transition":
                    value = str(payload[key])
                    if value not in {"fade", "slide"}:
                        raise HTTPException(status_code=400, detail="轮播切换方式不正确")
                elif key == "background_media_id":
                    value = str(payload[key] or "")
                    if value:
                        media = conn.execute(
                            "SELECT original_name,mime_type FROM attachments WHERE id=? AND deleted_at IS NULL", (value,)
                        ).fetchone()
                        if not media:
                            raise HTTPException(status_code=400, detail="所选背景媒体不存在")
                        set_setting(conn, "background_media_kind", _media_kind(media["original_name"], media["mime_type"]))
                        set_setting(conn, "background_image", "")
                elif key == "login_slides":
                    if not isinstance(payload[key], list):
                        raise HTTPException(status_code=400, detail="登录轮播列表格式不正确")
                    slides: list[dict[str, Any]] = []
                    for index, raw in enumerate(payload[key]):
                        if not isinstance(raw, dict):
                            continue
                        attachment_id = str(raw.get("attachment_id") or "")
                        attachment = conn.execute(
                            "SELECT original_name FROM attachments WHERE id=? AND deleted_at IS NULL", (attachment_id,)
                        ).fetchone()
                        if not attachment:
                            raise HTTPException(status_code=400, detail=f"第 {index + 1} 个轮播媒体不存在")
                        try:
                            duration = max(2, min(600, int(raw.get("duration", 8))))
                        except (TypeError, ValueError):
                            duration = 8
                        slides.append({
                            "id": str(raw.get("id") or new_id("slide"))[:100],
                            "attachment_id": attachment_id,
                            "title": str(raw.get("title") or attachment["original_name"])[:160],
                            "duration": duration,
                        })
                    value = json.dumps(slides, ensure_ascii=False, separators=(",", ":"))
                elif key == "loading_cars":
                    if not isinstance(payload[key], list):
                        raise HTTPException(status_code=400, detail="等待动画赛车列表格式不正确")
                    cars: list[dict[str, Any]] = []
                    for index, raw in enumerate(payload[key][:12]):
                        if not isinstance(raw, dict):
                            continue
                        attachment_id = str(raw.get("attachment_id") or "")
                        attachment = conn.execute(
                            "SELECT original_name,mime_type FROM attachments WHERE id=? AND deleted_at IS NULL",
                            (attachment_id,),
                        ).fetchone()
                        if not attachment or _media_kind(attachment["original_name"], attachment["mime_type"]) != "image":
                            raise HTTPException(status_code=400, detail=f"第 {index + 1} 张等待动画赛车图片不存在")
                        cars.append({
                            "id": str(raw.get("id") or new_id("loader"))[:100],
                            "attachment_id": attachment_id,
                            "title": str(raw.get("title") or attachment["original_name"])[:160],
                        })
                    value = json.dumps(cars, ensure_ascii=False, separators=(",", ":"))
                else:
                    value = str(payload[key] or "")
                set_setting(conn, key, value)
        audit(conn, auth.user["id"], "update", "settings", None, {"keys": sorted(set(payload) & allowed)})
        settings = _appearance_settings(conn)
    await hub.notify("settings_changed")
    return {"ok": True, "settings": settings}


@app.put("/api/admin/login-info")
async def login_info_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        try:
            mapping = json.loads(setting(conn, "login_info_panels", "{}"))
        except json.JSONDecodeError:
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        normalized = _normalize_login_info(conn, payload)
        mapping[current_season_id(conn)] = {
            "interval": normalized["interval"],
            "items": [{key: item[key] for key in ("id", "type", "title", "content", "visible")} for item in normalized["items"]],
        }
        encoded = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise HTTPException(status_code=400, detail="登录信息内容过大")
        create_snapshot(conn, auth.user["id"], "登录信息修改前", normalized["season_name"])
        set_setting(conn, "login_info_panels", encoded)
        audit(conn, auth.user["id"], "update", "login_info", normalized["season_id"], {"items": len(normalized["items"])})
        settings = _appearance_settings(conn)
    await hub.notify("settings_changed")
    return {"ok": True, "settings": settings}


def _integration_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        ai = normalize_ai_connectors(json.loads(setting(conn, "ai_connectors", "[]")))
    except (json.JSONDecodeError, IntegrationError, ValueError):
        ai = []
    for item in ai:
        item["has_secret"] = bool(setting(conn, f"ai_secret_{item['id']}", ""))
    try:
        nas = normalize_nas_config(json.loads(setting(conn, "nas_config", "{}")))
    except (json.JSONDecodeError, IntegrationError):
        nas = normalize_nas_config({})
    nas["has_secret"] = bool(setting(conn, "nas_secret", ""))
    return {"ai": ai, "nas": nas, "notice": "AI 暂不参与发票识别；NAS 仅预留连接配置，不执行同步。"}


@app.get("/api/admin/integrations")
async def integrations_get(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    with connect() as conn:
        return _integration_settings(conn)


@app.put("/api/admin/integrations/ai")
async def integrations_ai_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    raw_items = payload.get("items")
    try:
        clean = normalize_ai_connectors(raw_items)
    except (IntegrationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw_by_id = {str(item.get("id") or ""): item for item in raw_items if isinstance(item, dict)}
    with transaction() as conn:
        set_setting(conn, "ai_connectors", json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
        for item in clean:
            raw = raw_by_id.get(item["id"], {})
            if raw.get("clear_secret"):
                set_setting(conn, f"ai_secret_{item['id']}", "", sync=False)
            elif str(raw.get("api_key") or ""):
                try:
                    encrypted = protect_secret(str(raw["api_key"]))
                except IntegrationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                set_setting(conn, f"ai_secret_{item['id']}", encrypted, sync=False)
        audit(conn, auth.user["id"], "update", "ai_integrations", None, {"count": len(clean)})
        result = _integration_settings(conn)
    return {"ok": True, **result}


@app.post("/api/admin/integrations/ai/test")
async def integrations_ai_test(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        connector = normalize_ai_connectors([payload.get("connector") or {}])[0]
        secret = str((payload.get("connector") or {}).get("api_key") or "")
        if not secret:
            with connect() as conn:
                encrypted = setting(conn, f"ai_secret_{connector['id']}", "")
            secret = unprotect_secret(encrypted) if encrypted else ""
        return await asyncio.to_thread(test_ai_connection, connector, secret)
    except (IntegrationError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/integrations/nas")
async def integrations_nas_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        clean = normalize_nas_config(payload)
    except IntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with transaction() as conn:
        set_setting(conn, "nas_config", json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
        if payload.get("clear_secret"):
            set_setting(conn, "nas_secret", "", sync=False)
        elif str(payload.get("password") or ""):
            try:
                encrypted = protect_secret(str(payload["password"]))
            except IntegrationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            set_setting(conn, "nas_secret", encrypted, sync=False)
        audit(conn, auth.user["id"], "update", "nas_integration", None, {"protocol": clean["protocol"]})
        result = _integration_settings(conn)
    return {"ok": True, **result}


@app.post("/api/admin/integrations/nas/test")
async def integrations_nas_test(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        clean = normalize_nas_config(payload)
        password = str(payload.get("password") or "")
        if not password:
            with connect() as conn:
                encrypted = setting(conn, "nas_secret", "")
            password = unprotect_secret(encrypted) if encrypted else ""
        return await asyncio.to_thread(test_nas_connection, clean, password)
    except IntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/invoice-defaults")
async def invoice_defaults_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        season_id = current_season_id(conn)
        category_id = str(payload.get("category_id") or "")
        source_id = str(payload.get("funding_source_id") or "")
        payer_id = str(payload.get("payer_member_id") or "")
        if category_id and not conn.execute(
            "SELECT 1 FROM categories WHERE id=? AND active=1 AND deleted_at IS NULL", (category_id,)
        ).fetchone():
            raise HTTPException(status_code=400, detail="默认费用分类不存在或已停用")
        if source_id and not conn.execute(
            "SELECT 1 FROM funding_sources WHERE id=? AND active=1 AND deleted_at IS NULL", (source_id,)
        ).fetchone():
            raise HTTPException(status_code=400, detail="默认资金来源不存在或已停用")
        active_members = {str(row[0]) for row in conn.execute(
            "SELECT id FROM members WHERE season_id=? AND active=1 AND deleted_at IS NULL", (season_id,)
        ).fetchall()}
        if payer_id and payer_id not in active_members:
            raise HTTPException(status_code=400, detail="默认垫付成员不属于当前赛季")
        burden = str(payload.get("burden_type") or "team_aa")
        if burden not in {"team_aa", "specified_split", "self_paid"}:
            raise HTTPException(status_code=400, detail="默认承担方式不正确")
        raw_split_ids = payload.get("split_member_ids")
        split_ids = [str(value) for value in raw_split_ids] if isinstance(raw_split_ids, list) else []
        split_ids = [value for value in dict.fromkeys(split_ids) if value in active_members]
        labels = payload.get("burden_labels") if isinstance(payload.get("burden_labels"), dict) else {}
        defaults = {
            "category_id": category_id,
            "payer_member_id": payer_id,
            "funding_source_id": source_id,
            "burden_type": burden,
            "split_member_ids": split_ids,
            "batch_note": str(payload.get("batch_note") or "")[:500],
            "burden_labels": {
                "team_aa": str(labels.get("team_aa") or "全队 AA").strip()[:30] or "全队 AA",
                "specified_split": str(labels.get("specified_split") or "指定成员").strip()[:30] or "指定成员",
                "self_paid": str(labels.get("self_paid") or "个人承担").strip()[:30] or "个人承担",
            },
        }
        create_snapshot(conn, auth.user["id"], "录入默认值修改前", "发票录入与批量导入")
        set_setting(conn, "invoice_defaults", json.dumps(defaults, ensure_ascii=False, separators=(",", ":")))
        audit(conn, auth.user["id"], "update", "invoice_defaults", None, defaults)
    await hub.notify("invoice_defaults_changed")
    return {"ok": True, "defaults": defaults}


@app.post("/api/admin/settings/background")
async def settings_background(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in APPEARANCE_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="背景支持 JPG、PNG、GIF、WEBP、MP4 或 WEBM")
    attachment = await save_upload(file, auth.user)
    kind = _media_kind(attachment["original_name"], attachment["mime_type"])
    with transaction() as conn:
        set_setting(conn, "background_media_id", attachment["id"])
        set_setting(conn, "background_media_kind", kind)
        set_setting(conn, "background_image", "")
        audit(conn, auth.user["id"], "update", "background", attachment["id"], {"name": attachment["original_name"]})
        settings = _appearance_settings(conn)
    await hub.notify("settings_changed")
    return {"ok": True, "media": {
        "attachment_id": attachment["id"], "title": attachment["original_name"], "kind": kind,
        "private_url": f"/api/attachments/{attachment['id']}/content",
    }, "settings": settings}


@app.post("/api/admin/appearance/media")
async def appearance_media_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in APPEARANCE_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="界面媒体支持 JPG、PNG、GIF、WEBP、MP4 或 WEBM")
    attachment = await save_upload(file, auth.user)
    return {"ok": True, "media": {
        "id": new_id("slide"),
        "attachment_id": attachment["id"],
        "title": attachment["original_name"],
        "kind": _media_kind(attachment["original_name"], attachment["mime_type"]),
        "duration": 8,
        "private_url": f"/api/attachments/{attachment['id']}/content",
    }}


@app.get("/api/admin/wallpaper-engine")
async def wallpaper_engine_list(request: Request, refresh: bool = False) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    return await asyncio.to_thread(scan_wallpapers, force=refresh)


@app.get("/api/admin/wallpaper-engine/{item_id}/preview")
async def wallpaper_engine_preview(item_id: str, request: Request) -> FileResponse:
    auth = get_auth(request)
    require_admin(auth)
    item = await asyncio.to_thread(wallpaper_item, item_id)
    if not item or not Path(item["preview_path"]).is_file():
        raise HTTPException(status_code=404, detail="壁纸预览不存在")
    return FileResponse(item["preview_path"], headers={"Content-Disposition": "inline"})


@app.post("/api/admin/wallpaper-engine/{item_id}/import")
async def wallpaper_engine_import(item_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    item = await asyncio.to_thread(wallpaper_item, item_id)
    if not item or not Path(item["playback_path"]).is_file():
        raise HTTPException(status_code=404, detail="该 Wallpaper Engine 壁纸无法导入")
    path = Path(item["playback_path"])
    attachment = await asyncio.to_thread(save_file, path, f"{item['title'][:120]}{path.suffix.lower()}", auth.user)
    kind = _media_kind(attachment["original_name"], attachment["mime_type"])
    return {"ok": True, "media": {
        "id": new_id("slide"),
        "attachment_id": attachment["id"],
        "title": item["title"],
        "kind": kind,
        "duration": 8,
        "private_url": f"/api/attachments/{attachment['id']}/content",
        "uses_preview": item["uses_preview"],
    }}


@app.get("/api/admin/classification-rules")
async def classification_rules_get(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    with connect() as conn:
        return {"items": load_rules(conn)}


@app.put("/api/admin/classification-rules")
async def classification_rules_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    with transaction() as conn:
        if bool(payload.get("reset")):
            value = "[]"
        else:
            rules = payload.get("items")
            if not isinstance(rules, list):
                raise HTTPException(status_code=400, detail="分类规则格式不正确")
            valid_categories = {row[0] for row in conn.execute(
                "SELECT id FROM categories WHERE deleted_at IS NULL"
            ).fetchall()}
            for rule in rules:
                category_id = str(rule.get("category_id") or "") if isinstance(rule, dict) else ""
                if category_id and category_id not in valid_categories:
                    raise HTTPException(status_code=400, detail="分类规则引用了不存在的费用分类")
            value = serialize_rules(rules)
        create_snapshot(conn, auth.user["id"], "智能分类规则修改前", "离线识别规则")
        set_setting(conn, "classification_rules", value)
        audit(conn, auth.user["id"], "update", "classification_rules", None, {})
        items = load_rules(conn)
    await hub.notify("classification_rules_changed")
    return {"ok": True, "items": items}


@app.get("/api/admin/sync")
async def sync_get(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    return sync_config()


@app.put("/api/admin/sync")
async def sync_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    remote_url = str(payload.get("remote_url") or "").strip().rstrip("/")
    secret = str(payload.get("secret") or "").strip()
    enabled = bool(payload.get("enabled"))
    if enabled and (not remote_url or not (secret or sync_config()["secret_configured"])):
        raise HTTPException(status_code=400, detail="启用同步前需填写云端地址与同步密钥")
    with transaction() as conn:
        set_setting(conn, "remote_url", remote_url, sync=False)
        set_setting(conn, "sync_enabled", "1" if enabled else "0", sync=False)
        if secret:
            set_setting(conn, "sync_shared_secret", secret, sync=False)
        audit(conn, auth.user["id"], "update", "sync_settings", None, {"enabled": enabled, "remote_url": remote_url})
    return sync_config()


@app.post("/api/admin/sync/run")
async def sync_run(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        result = await asyncio.to_thread(perform_sync)
    except SyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await hub.notify("sync_complete")
    return result


@app.get("/api/reports/summary")
async def report_summary(request: Request, date_from: str = "", date_to: str = "") -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        clauses = ["i.deleted_at IS NULL", "i.season_id=?"]
        params: list[Any] = [current_season_id(conn)]
        if date_from:
            clauses.append("i.invoice_date>=?"); params.append(date_from)
        if date_to:
            clauses.append("i.invoice_date<=?"); params.append(date_to)
        where = " AND ".join(clauses)
        categories = [dict(row) for row in conn.execute(
            f"""SELECT COALESCE(c.name,'未分类') AS name,COALESCE(c.color,'#9aa7b7') AS color,
            COUNT(i.id) AS count,SUM(i.total_amount_cents) AS total_cents,
            SUM(i.reimbursed_amount_cents) AS reimbursed_cents,
            SUM(i.total_amount_cents-i.reimbursed_amount_cents) AS pending_cents
            FROM invoices i LEFT JOIN categories c ON c.id=i.category_id WHERE {where}
            GROUP BY i.category_id,c.name,c.color ORDER BY total_cents DESC""", params
        ).fetchall()]
        sources = [dict(row) for row in conn.execute(
            f"""SELECT COALESCE(f.name,'未选择') AS name,COALESCE(f.color,'#9aa7b7') AS color,
            COUNT(i.id) AS count,SUM(i.total_amount_cents) AS total_cents,
            SUM(i.reimbursed_amount_cents) AS reimbursed_cents,
            SUM(i.total_amount_cents-i.reimbursed_amount_cents) AS pending_cents
            FROM invoices i LEFT JOIN funding_sources f ON f.id=i.funding_source_id WHERE {where}
            GROUP BY i.funding_source_id,f.name,f.color ORDER BY total_cents DESC""", params
        ).fetchall()]
        payers = [dict(row) for row in conn.execute(
            f"""SELECT COALESCE(m.name,'未知成员') AS name,COALESCE(m.avatar_color,'#9aa7b7') AS color,
            COUNT(i.id) AS count,SUM(i.total_amount_cents) AS total_cents
            FROM invoices i LEFT JOIN members m ON m.id=i.payer_member_id WHERE {where}
            GROUP BY i.payer_member_id,m.name,m.avatar_color ORDER BY total_cents DESC""", params
        ).fetchall()]
    for group in (categories, sources):
        for item in group:
            item["total"] = yuan(item.pop("total_cents")); item["reimbursed"] = yuan(item.pop("reimbursed_cents")); item["pending"] = yuan(item.pop("pending_cents"))
    for item in payers:
        item["total"] = yuan(item.pop("total_cents"))
    return {"categories": categories, "sources": sources, "payers": payers}


def _csv_export_response(invoices: list[dict[str, Any]]) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["发票号码", "总金额", "分类", "承担方式", "资金来源", "成员"])
    burden_labels = {"team_aa": "全队AA", "self_paid": "个人承担", "specified_split": "指定成员分摊"}
    for item in invoices:
        splits = "、".join(
            f"{split['member_name']}（{float(split.get('share_amount') or 0):.2f}元）"
            for split in item.get("splits", [])
        )
        members = f"垫付：{item.get('payer_name') or '未设置'}；分摊：{splits or '未设置'}"
        writer.writerow([
            item["invoice_no"], f"{item['total_amount']:.2f}", item.get("category_name") or "未分类",
            burden_labels.get(item["burden_type"], item["burden_type"]), item.get("funding_source_name") or "未选择",
            members,
        ])
    payload = "\ufeff" + output.getvalue()
    total = round(sum(float(item.get("total_amount") or 0) for item in invoices), 2)
    return StreamingResponse(iter([payload.encode("utf-8")]), media_type="text/csv; charset=utf-8",
                             headers={
                                 "Content-Disposition": f"attachment; filename*=UTF-8''yanxiang-expenses-{utc_now()[:10]}.csv",
                                 "X-Export-Count": str(len(invoices)),
                                 "X-Export-Total": f"{total:.2f}",
                             })


def _filtered_export_invoices(conn: sqlite3.Connection, filters: dict[str, Any]) -> list[dict[str, Any]]:
    items = list_invoices(
        conn,
        search=str(filters.get("search") or ""),
        status=str(filters.get("status") or ""),
        category_id=str(filters.get("category_id") or ""),
        source_id=str(filters.get("source_id") or ""),
        date_from=str(filters.get("date_from") or ""),
        date_to=str(filters.get("date_to") or ""),
        limit=100000,
    )
    raw_ids = filters.get("ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [value for value in raw_ids.split(",") if value]
    if isinstance(raw_ids, list) and raw_ids:
        selected = {str(value) for value in raw_ids}
        items = [item for item in items if item["id"] in selected]
    return items


@app.get("/api/export/csv")
async def export_csv(
    request: Request, search: str = "", status: str = "", category_id: str = "",
    source_id: str = "", date_from: str = "", date_to: str = "", ids: str = "",
) -> StreamingResponse:
    get_auth(request)
    with connect() as conn:
        invoices = _filtered_export_invoices(conn, {
            "search": search, "status": status, "category_id": category_id,
            "source_id": source_id, "date_from": date_from, "date_to": date_to, "ids": ids,
        })
    return _csv_export_response(invoices)


@app.post("/api/export/csv")
async def export_csv_selected(payload: dict[str, Any], request: Request) -> StreamingResponse:
    auth = get_auth(request)
    require_csrf(request, auth)
    with connect() as conn:
        invoices = _filtered_export_invoices(conn, payload)
    return _csv_export_response(invoices)


def _safe_export_stem(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip(" ._")
    return (cleaned[:90] or fallback).strip()


def _lossless_image_pdf(path: Path) -> bytes:
    import zlib
    from PIL import Image, ImageOps, ImageSequence
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, EncodedStreamObject, NameObject, NumberObject

    writer = PdfWriter()
    with Image.open(path) as source:
        for raw_frame in ImageSequence.Iterator(source):
            frame = ImageOps.exif_transpose(raw_frame.copy())
            if frame.mode in {"RGBA", "LA"} or (frame.mode == "P" and "transparency" in frame.info):
                rgba = frame.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                frame = background.convert("RGB")
            else:
                frame = frame.convert("RGB")
            width, height = frame.size
            dpi = frame.info.get("dpi") or source.info.get("dpi") or (96, 96)
            try:
                dpi_x, dpi_y = max(1.0, float(dpi[0])), max(1.0, float(dpi[1]))
            except (TypeError, ValueError, IndexError):
                dpi_x = dpi_y = 96.0
            page_width, page_height = width * 72.0 / dpi_x, height * 72.0 / dpi_y
            page = writer.add_blank_page(width=page_width, height=page_height)
            image_stream = EncodedStreamObject()
            image_stream._data = zlib.compress(frame.tobytes(), level=6)
            image_stream.update({
                NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(width), NameObject("/Height"): NumberObject(height),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Filter"): NameObject("/FlateDecode"),
            })
            image_ref = writer._add_object(image_stream)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/InvoiceImage"): image_ref})
            })
            content = DecodedStreamObject()
            content.set_data(f"q {page_width:.6f} 0 0 {page_height:.6f} 0 0 cm /InvoiceImage Do Q".encode("ascii"))
            page.replace_contents(writer._add_object(content))
    output = io.BytesIO(); writer.write(output); return output.getvalue()


def _invoice_source_pdf(invoice: dict[str, Any]) -> tuple[bytes, str]:
    attachment_id = str(invoice.get("attachment_id") or "")
    if not attachment_id:
        raise ValueError("没有原始附件")
    with connect() as conn:
        attachment = conn.execute(
            "SELECT original_name,stored_name,mime_type FROM attachments WHERE id=? AND deleted_at IS NULL",
            (attachment_id,),
        ).fetchone()
    if not attachment:
        raise ValueError("附件记录不存在")
    path = attachment_path(attachment["stored_name"])
    if not path.is_file():
        raise ValueError("附件文件尚未同步到本机")
    extension = path.suffix.lower()
    if extension == ".pdf":
        return path.read_bytes(), "源 PDF（未重绘）"
    if extension in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}:
        try:
            return _lossless_image_pdf(path), "原图像素无损封装 PDF"
        except Exception as exc:
            raise ValueError(f"图片转 PDF 失败：{exc}") from exc
    raise ValueError(f"{extension or '该格式'} 暂不支持无损转为 PDF")


def _supporting_attachment_rows(invoice_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT r.attachment_kind,r.label,a.id AS attachment_id,a.original_name,a.stored_name,a.mime_type
            FROM invoice_supporting_attachments r JOIN attachments a ON a.id=r.attachment_id
            WHERE r.invoice_id=? AND a.deleted_at IS NULL ORDER BY r.sort_order,r.created_at""",
            (invoice_id,),
        ).fetchall()]


def _supporting_pdf_bytes(item: dict[str, Any], conversion_dir: Path) -> bytes | None:
    path = attachment_path(item["stored_name"])
    if not path.is_file():
        raise FileNotFoundError("证明附件在本机不存在")
    extension = path.suffix.lower()
    if extension == ".pdf":
        return path.read_bytes()
    if extension in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}:
        return _lossless_image_pdf(path)
    if extension in OFFICE_WORD | OFFICE_EXCEL | OFFICE_POWERPOINT:
        converted = convert_office_to_pdf(path, conversion_dir)
        return converted.read_bytes() if converted else None
    if extension in TEXT_EXTENSIONS:
        converted = text_to_pdf(path, conversion_dir)
        return converted.read_bytes() if converted else None
    return None


def _record_export_problem(
    invoice_id: str, issue_type: str, message: str, *, user_id: str | None = None, details: dict[str, Any] | None = None
) -> None:
    with transaction() as conn:
        record_issue(
            conn, issue_type, message, invoice_id=invoice_id, severity="error", details=details, user_id=user_id,
        )


def _clear_previous_export_problems(invoice_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            """UPDATE invoice_quality_issues SET status='resolved',updated_at=?
            WHERE invoice_id=? AND status='open' AND issue_type IN ('export_failed','conversion_failed','attachment_missing')""",
            (utc_now(), invoice_id),
        )


def _append_pdf(writer: Any, payload: bytes) -> None:
    from pypdf import PdfReader
    writer.append(PdfReader(io.BytesIO(payload)))


def _create_pdf_export(
    invoices: list[dict[str, Any]], mode: str, include_supporting: bool = False, user_id: str | None = None
) -> tuple[Path, int, int, float]:
    converted: list[tuple[dict[str, Any], bytes, str]] = []
    skipped: list[str] = []
    for index, invoice in enumerate(invoices, 1):
        _clear_previous_export_problems(str(invoice["id"]))
        try:
            pdf_bytes, method = _invoice_source_pdf(invoice)
            converted.append((invoice, pdf_bytes, method))
        except ValueError as exc:
            message = f"{index}. {invoice.get('invoice_no') or invoice.get('vendor') or invoice.get('id')}：{exc}"
            skipped.append(message)
            _record_export_problem(str(invoice["id"]), "export_failed", str(exc), user_id=user_id)
    if not converted:
        raise ValueError("所选发票没有可导出的 PDF 或图片源文件")
    package_as_zip = mode == "separate" or include_supporting
    suffix = ".zip" if package_as_zip else ".pdf"
    fd, name = tempfile.mkstemp(prefix="yxrt_pdf_export_", suffix=suffix, dir=TMP_DIR)
    os.close(fd)
    output_path = Path(name)
    conversion_root = Path(tempfile.mkdtemp(prefix="yxrt_attachment_convert_", dir=TMP_DIR))
    source_extras: list[tuple[str, Path]] = []
    manifest: list[str] = []
    try:
        from pypdf import PdfWriter

        def combined_invoice_pdf(invoice: dict[str, Any], primary: bytes) -> bytes:
            writer = PdfWriter()
            _append_pdf(writer, primary)
            if include_supporting:
                for supporting in _supporting_attachment_rows(str(invoice["id"])):
                    identity = supporting.get("original_name") or supporting.get("attachment_id")
                    path = attachment_path(supporting["stored_name"])
                    try:
                        payload = _supporting_pdf_bytes(supporting, conversion_root)
                        if payload:
                            _append_pdf(writer, payload)
                            manifest.append(f"已追加：{identity}")
                        else:
                            source_extras.append((f"无法合并的源附件/{invoice['id']}/{Path(str(identity)).name}", path))
                            message = f"附件“{identity}”无法转换为 PDF，已保留原文件"
                            skipped.append(message); manifest.append(message)
                            _record_export_problem(str(invoice["id"]), "conversion_failed", message, user_id=user_id)
                    except Exception as exc:
                        if path.is_file():
                            source_extras.append((f"无法合并的源附件/{invoice['id']}/{Path(str(identity)).name}", path))
                        message = f"附件“{identity}”转换失败：{str(exc)[:300]}"
                        skipped.append(message); manifest.append(message)
                        _record_export_problem(str(invoice["id"]), "conversion_failed", message, user_id=user_id)
            output = io.BytesIO(); writer.write(output); return output.getvalue()

        combined: list[tuple[dict[str, Any], bytes]] = []
        for invoice, primary, _ in converted:
            try:
                combined.append((invoice, combined_invoice_pdf(invoice, primary)))
            except Exception as exc:
                message = f"{invoice.get('invoice_no') or invoice.get('vendor') or invoice.get('id')}：合并失败（{exc}）"
                skipped.append(message)
                _record_export_problem(str(invoice["id"]), "export_failed", message, user_id=user_id)

        if not combined:
            raise ValueError("没有可以导出的 PDF 页面")
        if not package_as_zip:
            writer = PdfWriter()
            for invoice, pdf_bytes in combined:
                try:
                    _append_pdf(writer, pdf_bytes)
                except Exception as exc:
                    message = f"{invoice.get('invoice_no') or invoice.get('id')}：合并失败（{exc}）"
                    skipped.append(message)
                    _record_export_problem(str(invoice["id"]), "export_failed", message, user_id=user_id)
            if not writer.pages:
                raise ValueError("没有可以合并的 PDF 页面")
            with output_path.open("wb") as stream:
                writer.write(stream)
        else:
            used_names: set[str] = set()
            with zipfile.ZipFile(output_path, "w", allowZip64=True) as archive:
                if mode == "merged":
                    merged = PdfWriter()
                    for invoice, pdf_bytes in combined:
                        try:
                            _append_pdf(merged, pdf_bytes)
                        except Exception as exc:
                            message = f"{invoice.get('invoice_no') or invoice.get('id')}：合并失败（{exc}）"
                            skipped.append(message)
                            _record_export_problem(str(invoice["id"]), "export_failed", message, user_id=user_id)
                    stream = io.BytesIO(); merged.write(stream)
                    archive.writestr("发票及附件_合并.pdf", stream.getvalue(), compress_type=zipfile.ZIP_STORED)
                else:
                    for index, (invoice, pdf_bytes) in enumerate(combined, 1):
                        identity = invoice.get("invoice_no") or invoice.get("vendor") or f"发票_{index}"
                        base = f"{index:03d}_{_safe_export_stem(str(identity), f'发票_{index}')}.pdf"
                        candidate = base; serial = 2
                        while candidate.lower() in used_names:
                            candidate = f"{Path(base).stem}_{serial}.pdf"; serial += 1
                        used_names.add(candidate.lower())
                        archive.writestr(candidate, pdf_bytes, compress_type=zipfile.ZIP_STORED)
                for archive_name, path in source_extras:
                    if path.is_file():
                        archive.write(path, arcname=archive_name)
                if skipped or manifest:
                    archive.writestr(
                        "导出说明.txt", "\ufeff" + "\n".join(manifest + skipped), compress_type=zipfile.ZIP_DEFLATED,
                    )
        total = round(sum(float(item.get("total_amount") or 0) for item, _ in combined), 2)
        return output_path, len(combined), len(skipped), total
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(conversion_root, ignore_errors=True)


@app.post("/api/export/pdf")
async def export_pdf(payload: dict[str, Any], request: Request, background_tasks: BackgroundTasks) -> FileResponse:
    auth = get_auth(request)
    require_csrf(request, auth)
    mode = str(payload.get("mode") or "separate")
    include_supporting = bool(payload.get("include_supporting"))
    if mode not in {"merged", "separate"}:
        raise HTTPException(status_code=400, detail="PDF 导出方式不正确")
    with connect() as conn:
        invoices = _filtered_export_invoices(conn, payload)
    if not invoices:
        raise HTTPException(status_code=400, detail="当前没有可导出的发票")
    try:
        path, count, skipped, total = await asyncio.to_thread(
            _create_pdf_export, invoices, mode, include_supporting, auth.user["id"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(path.unlink, missing_ok=True)
    date = utc_now()[:10]
    packaged = mode == "separate" or include_supporting
    if include_supporting:
        filename = f"燕翔车队发票及证明_{'合并' if mode == 'merged' else '逐张'}_{date}.zip"
    else:
        filename = f"燕翔车队发票_合并_{date}.pdf" if mode == "merged" else f"燕翔车队发票_逐张_{date}.zip"
    return FileResponse(
        path,
        media_type="application/zip" if packaged else "application/pdf",
        filename=filename,
        headers={
            "X-Export-Count": str(count),
            "X-Export-Skipped": str(skipped),
            "X-Export-Total": f"{total:.2f}",
        },
    )


def _backup_csv(headers: list[str], rows: list[tuple[Any, ...]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _backup_readable_exports(database_path: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = [str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        counts = {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
        personnel = [tuple(row) for row in conn.execute(
            """SELECT s.name,m.name,m.department,m.student_id,m.phone,m.email,
            CASE WHEN m.deleted_at IS NOT NULL THEN '已删除' WHEN m.active=1 THEN '启用' ELSE '停用' END,
            COALESCE(GROUP_CONCAT(u.username,'；'),''),
            COALESCE(GROUP_CONCAT(CASE WHEN u.id IS NULL THEN NULL WHEN u.role='admin' THEN '管理员' WHEN u.role='viewer' THEN '公共查看' ELSE '成员' END,'；'),''),
            COALESCE(GROUP_CONCAT(CASE WHEN u.id IS NULL THEN NULL WHEN u.deleted_at IS NOT NULL THEN '已删除' WHEN u.active=1 THEN '启用' ELSE '停用' END,'；'),''),
            m.created_at,m.updated_at
            FROM members m JOIN seasons s ON s.id=m.season_id
            LEFT JOIN users u ON u.member_id=m.id
            GROUP BY m.id ORDER BY s.sort_order DESC,m.sort_order,m.name"""
        ).fetchall()]
        departments = [tuple(row) for row in conn.execute(
            """SELECT d.name,d.sort_order,
            (SELECT COUNT(*) FROM members m WHERE m.department=d.name AND m.deleted_at IS NULL),
            CASE WHEN d.deleted_at IS NULL THEN '保留' ELSE '已删除' END,d.created_at,d.updated_at
            FROM departments d ORDER BY d.sort_order,d.name"""
        ).fetchall()]
        accounts = [tuple(row) for row in conn.execute(
            """SELECT u.username,u.display_name,
            CASE u.role WHEN 'admin' THEN '管理员' WHEN 'viewer' THEN '公共查看' ELSE '成员' END,
            COALESCE(m.name,''),COALESCE(s.name,''),
            CASE WHEN u.deleted_at IS NOT NULL THEN '已删除' WHEN u.active=1 THEN '启用' ELSE '停用' END,
            CASE WHEN u.must_change_password=1 THEN '是' ELSE '否' END,u.created_at,u.updated_at
            FROM users u LEFT JOIN members m ON m.id=u.member_id LEFT JOIN seasons s ON s.id=m.season_id
            ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'viewer' THEN 1 ELSE 2 END,u.username"""
        ).fetchall()]
        invoices = [tuple(row) for row in conn.execute(
            """SELECT s.name,i.invoice_no,i.vendor,i.invoice_date,printf('%.2f',i.total_amount_cents/100.0),
            COALESCE(c.name,''),i.product_type,COALESCE(m.name,''),i.burden_type,
            COALESCE(f.name,''),i.reimbursement_status,printf('%.2f',i.reimbursed_amount_cents/100.0),
            i.reimbursement_date,COALESCE(u.display_name,''),i.created_at,i.updated_at
            FROM invoices i JOIN seasons s ON s.id=i.season_id
            LEFT JOIN categories c ON c.id=i.category_id LEFT JOIN members m ON m.id=i.payer_member_id
            LEFT JOIN funding_sources f ON f.id=i.funding_source_id LEFT JOIN users u ON u.id=i.created_by
            ORDER BY s.sort_order DESC,i.invoice_date DESC,i.created_at DESC"""
        ).fetchall()]
        exports = {
            "可查看数据/人员信息.csv": _backup_csv(
                ["所属赛季", "姓名", "组别", "学号", "电话", "邮箱", "成员状态", "登录账号", "账号角色", "账号状态", "创建时间", "更新时间"],
                personnel,
            ),
            "可查看数据/组别信息.csv": _backup_csv(
                ["组别名称", "排序", "当前成员数", "状态", "创建时间", "更新时间"], departments,
            ),
            "可查看数据/账号关联.csv": _backup_csv(
                ["登录账号", "显示名称", "角色", "关联成员", "所属赛季", "账号状态", "下次登录修改密码", "创建时间", "更新时间"],
                accounts,
            ),
            "可查看数据/发票明细.csv": _backup_csv(
                ["所属赛季", "发票号码", "销售方", "开票日期", "总金额", "分类", "产品类型", "垫付成员", "承担方式", "资金来源", "报销状态", "已报销金额", "报销日期", "提交人", "上传日期", "更新时间"],
                invoices,
            ),
        }
        return exports, counts
    finally:
        conn.close()


def _create_backup_archive(output_path: Path | None = None) -> Path:
    if output_path is None:
        fd, name = tempfile.mkstemp(prefix="yxrt_backup_", suffix=".zip", dir=TMP_DIR)
        os.close(fd)
        output_path = Path(name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, archive_temp_name = tempfile.mkstemp(prefix=f".{output_path.stem}_", suffix=".partial", dir=output_path.parent)
    os.close(fd)
    archive_temp = Path(archive_temp_name)
    fd, db_temp_name = tempfile.mkstemp(prefix="yxrt_db_", suffix=".sqlite", dir=TMP_DIR)
    os.close(fd)
    db_temp = Path(db_temp_name)
    try:
        with DB_LOCK:
            source = connect()
            target = sqlite3.connect(str(db_temp))
            try:
                source.backup(target)
            finally:
                target.close(); source.close()
        exports, row_counts = _backup_readable_exports(db_temp)
        manifest = {
            "product": "燕翔车队经费管理系统",
            "version": __version__,
            "created_at": utc_now(),
            "database_complete": True,
            "includes": ["全部赛季与人员", "组别与账号关联", "发票与分摊结算", "设置与回溯记录", "全部上传附件"],
            "row_counts": row_counts,
        }
        with zipfile.ZipFile(archive_temp, "w", allowZip64=True) as archive:
            archive.write(db_temp, "database.sqlite", compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("可查看数据/备份内容清单.json", json.dumps(manifest, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            for name, content in exports.items():
                archive.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
            for path in UPLOAD_DIR.rglob("*"):
                if path.is_file():
                    archive.write(path, Path("uploads") / path.relative_to(UPLOAD_DIR), compress_type=zipfile.ZIP_STORED)
        with zipfile.ZipFile(archive_temp) as check:
            if check.testzip() is not None or not {"database.sqlite", "manifest.json", "可查看数据/人员信息.csv"}.issubset(check.namelist()):
                raise OSError("完整备份压缩包校验失败")
        archive_temp.replace(output_path)
        return output_path
    finally:
        db_temp.unlink(missing_ok=True)
        archive_temp.unlink(missing_ok=True)


@app.get("/api/admin/backup")
async def backup_download(request: Request, background_tasks: BackgroundTasks) -> FileResponse:
    auth = get_auth(request)
    require_admin(auth)
    path = await asyncio.to_thread(_create_backup_archive)
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="application/zip", filename=f"燕翔车队经费备份_{utc_now()[:10]}.zip")


@app.post("/api/admin/backup/save")
async def backup_save_to_desktop(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Save directly to a path selected by the native Windows file dialog.

    The browser download fallback remains available for the web build.  This
    endpoint is deliberately desktop-only so a remote browser cannot choose an
    arbitrary server-side path.
    """
    auth = get_auth(request)
    require_admin(auth)
    require_csrf(request, auth)
    if APP_MODE != "desktop":
        raise HTTPException(status_code=403, detail="仅软件版支持直接保存到本机路径")
    raw_path = str(payload.get("target_path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="未选择备份保存位置")
    target = Path(raw_path).expanduser().resolve()
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    try:
        path = await asyncio.to_thread(_create_backup_archive, target)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法写入所选位置：{exc}") from exc
    return {
        "ok": True,
        "filename": path.name,
        "size": path.stat().st_size,
        "message": "完整备份已保存",
    }


def _restore_backup(path: Path, user_id: str) -> None:
    with zipfile.ZipFile(path) as archive:
        damaged = archive.testzip()
        if damaged is not None:
            raise ValueError(f"备份压缩包中的文件已损坏：{Path(damaged).name}")
        names = set(archive.namelist())
        if "database.sqlite" not in names or "manifest.json" not in names:
            raise ValueError("备份包缺少数据库或清单文件")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("product") != "燕翔车队经费管理系统":
            raise ValueError("不是本系统生成的备份包")
        fd, source_name = tempfile.mkstemp(prefix="yxrt_restore_", suffix=".sqlite", dir=TMP_DIR)
        os.close(fd)
        source_path = Path(source_name)
        try:
            with archive.open("database.sqlite") as source, source_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
            check = sqlite3.connect(str(source_path))
            try:
                required = {"users", "members", "invoices", "invoice_splits", "snapshots"}
                if manifest.get("database_complete") is True:
                    required.update({"seasons", "departments", "categories", "funding_sources", "settlements", "app_settings"})
                tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required.issubset(tables):
                    raise ValueError("备份数据库结构不完整")
                integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity.lower() != "ok":
                    raise ValueError("备份数据库完整性检查失败")
                if check.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("备份中的人员、账号或发票关联关系不完整")
                if not check.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
                    raise ValueError("备份中没有有效管理员账号")
                declared_counts = manifest.get("row_counts") if isinstance(manifest.get("row_counts"), dict) else {}
                for table in ("seasons", "departments", "users", "members", "invoices"):
                    if table in declared_counts:
                        actual_count = int(check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                        if actual_count != int(declared_counts[table]):
                            raise ValueError(f"备份中的{table}记录数量与清单不一致")
            finally:
                check.close()

            backups_dir = DB_PATH.parent / "backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            safe_stamp = utc_now().replace(":", "-")[:19]
            _create_backup_archive(backups_dir / f"pre_restore_{safe_stamp}.zip")
            with DB_LOCK:
                source_db = sqlite3.connect(str(source_path))
                destination_db = connect()
                try:
                    source_db.backup(destination_db)
                    if destination_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
                        destination_db.execute("DELETE FROM sessions")
                finally:
                    destination_db.close(); source_db.close()
            for member in archive.infolist():
                parts = Path(member.filename.replace("\\", "/")).parts
                if member.is_dir() or not parts or parts[0] != "uploads":
                    continue
                relative = Path(*parts[1:])
                target = (UPLOAD_DIR / relative).resolve()
                if not target.is_relative_to(UPLOAD_DIR.resolve()):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=4 * 1024 * 1024)
        finally:
            source_path.unlink(missing_ok=True)
    init_db()


@app.post("/api/admin/restore-backup")
async def backup_restore(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_restore_upload_", suffix=".zip", dir=TMP_DIR)
    os.close(fd)
    try:
        with open(temp_name, "wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                target.write(chunk)
        await asyncio.to_thread(_restore_backup, Path(temp_name), auth.user["id"])
    except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        Path(temp_name).unlink(missing_ok=True)
    await hub.notify("restored")
    return {"ok": True, "message": "备份已恢复，请重新登录"}


def _require_sync(request: Request) -> None:
    if not valid_sync_key(request.headers.get("X-Sync-Key", "")):
        raise HTTPException(status_code=401, detail="同步密钥无效")


@app.get("/api/sync/health")
async def sync_health(request: Request) -> dict[str, Any]:
    _require_sync(request)
    return {"ok": True, "device_id": get_device_id(), "version": __version__}


@app.post("/api/sync/push")
async def sync_push(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_sync(request)
    events = payload.get("events") or []
    if not isinstance(events, list) or len(events) > 1000:
        raise HTTPException(status_code=400, detail="同步事件格式不正确")
    outcome = apply_events(events, source=str(payload.get("device_id") or "remote"))
    await hub.notify("sync_received")
    return {"ok": True, **outcome}


@app.get("/api/sync/pull")
async def sync_pull(request: Request, since: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=1000)) -> dict[str, Any]:
    _require_sync(request)
    events = events_after(since, limit)
    return {"events": events, "last_seq": events[-1]["seq"] if events else since}


def _valid_sha(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


@app.head("/api/sync/blob/{sha256}")
async def sync_blob_head(sha256: str, request: Request) -> Response:
    _require_sync(request)
    if not _valid_sha(sha256):
        raise HTTPException(status_code=400, detail="附件校验值不正确")
    if not find_blob(sha256.lower()):
        raise HTTPException(status_code=404, detail="附件不存在")
    return Response(status_code=200)


@app.get("/api/sync/blob/{sha256}")
async def sync_blob_get(sha256: str, request: Request) -> FileResponse:
    _require_sync(request)
    path = find_blob(sha256.lower()) if _valid_sha(sha256) else None
    if not path:
        raise HTTPException(status_code=404, detail="附件不存在")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@app.put("/api/sync/blob/{sha256}")
async def sync_blob_put(sha256: str, request: Request) -> dict[str, Any]:
    _require_sync(request)
    sha256 = sha256.lower()
    if not _valid_sha(sha256):
        raise HTTPException(status_code=400, detail="附件校验值不正确")
    existing = find_blob(sha256)
    if existing:
        return {"ok": True, "exists": True}
    suffix = Path(request.headers.get("X-File-Name", "")).suffix.lower()[:10]
    destination = attachment_path(sha256 + suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_blob_", dir=TMP_DIR)
    os.close(fd)
    import hashlib
    digest = hashlib.sha256()
    try:
        with open(temp_name, "wb") as output:
            async for chunk in request.stream():
                digest.update(chunk); output.write(chunk)
        if digest.hexdigest() != sha256:
            raise HTTPException(status_code=400, detail="附件校验失败")
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return {"ok": True, "exists": False}


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    raw_token = socket.cookies.get(COOKIE_NAME)
    if not raw_token:
        await socket.close(code=4401)
        return
    with connect() as conn:
        valid = conn.execute(
            """SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.deleted_at IS NULL""",
            (token_hash(raw_token), utc_now()),
        ).fetchone()
    if not valid:
        await socket.close(code=4401)
        return
    await hub.connect(socket)
    try:
        await socket.send_json({"event": "connected", "at": utc_now()})
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(socket)
    except Exception:
        hub.disconnect(socket)
