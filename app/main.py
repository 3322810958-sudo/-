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
from .ocr_engine import create_ocr_job, get_ocr_job, parse_invoice_text, warmup_ocr
from .security import hash_password, token_hash, validate_new_password, validate_username
from .sync_engine import SyncError, apply_events, events_after, perform_sync, sync_config, valid_sync_key
from .updater import check_for_update, get_update_job, schedule_update_install, start_update_download
from .wallpaper_engine import scan_wallpapers, wallpaper_item


STATIC_DIR = APP_DIR / "static"
STOP_EVENT = threading.Event()
APPEARANCE_SETTING_KEYS = (
    "team_name", "background_image", "background_media_id", "background_media_kind",
    "background_overlay", "accent_color", "login_slideshow_enabled", "login_slides",
    "login_transition", "loading_cars",
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
    if os.environ.get("YXRT_OCR_WARMUP", "1") == "1" and "PYTEST_CURRENT_TEST" not in os.environ:
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
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:; font-src 'self'; object-src 'none'; base-uri 'self'"
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


@app.post("/api/admin/update/download")
async def update_download(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        return await asyncio.to_thread(start_update_download, auth.user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/update/jobs/{job_id}")
async def update_job_status(job_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_admin(auth)
    job = get_update_job(job_id)
    if not job or job.get("created_by") != auth.user["id"]:
        raise HTTPException(status_code=404, detail="更新任务不存在")
    return job


@app.post("/api/admin/update/jobs/{job_id}/install")
async def update_install(job_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    try:
        return schedule_update_install(job_id, auth.user["id"])
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
    return values


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


def _reference_data(conn: sqlite3.Connection) -> dict[str, Any]:
    season = current_season(conn)
    members = [dict(row) for row in conn.execute(
        """SELECT * FROM members WHERE season_id=? AND deleted_at IS NULL
        ORDER BY active DESC,sort_order,name""", (season["id"],)
    ).fetchall()]
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
        """SELECT c.*,s.name AS season_name FROM creators c JOIN seasons s ON s.id=c.season_id
        WHERE c.season_id=? AND c.active=1 AND c.deleted_at IS NULL
        ORDER BY c.sort_order,c.department,c.name""", (season["id"],)
    ).fetchall()]
    settings = _appearance_settings(conn)
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
        })
        return data


@app.get("/api/dashboard")
async def dashboard_endpoint(request: Request) -> dict[str, Any]:
    get_auth(request)
    with connect() as conn:
        return dashboard(conn)


def _season_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    selected_id = current_season_id(conn)
    rows = [dict(row) for row in conn.execute(
        """SELECT s.*,
        (SELECT COUNT(*) FROM members m WHERE m.season_id=s.id AND m.deleted_at IS NULL) AS member_count,
        (SELECT COUNT(*) FROM invoices i WHERE i.season_id=s.id AND i.deleted_at IS NULL) AS invoice_count,
        (SELECT COUNT(*) FROM creators c WHERE c.season_id=s.id AND c.active=1 AND c.deleted_at IS NULL) AS creator_count
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
        """SELECT c.*,s.name AS season_name FROM creators c JOIN seasons s ON s.id=c.season_id
        WHERE c.id=? AND c.deleted_at IS NULL""", (creator_id,)
    ).fetchone()
    return dict(row) if row else None


@app.get("/api/creators")
async def creators_list(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with connect() as conn:
        season_id = current_season_id(conn)
        active_clause = "" if auth.user["role"] == "admin" else "AND c.active=1"
        items = [dict(row) for row in conn.execute(
            f"""SELECT c.*,s.name AS season_name FROM creators c JOIN seasons s ON s.id=c.season_id
            WHERE c.season_id=? {active_clause} AND c.deleted_at IS NULL
            ORDER BY c.active DESC,c.sort_order,c.department,c.name""", (season_id,)
        ).fetchall()]
    return {"items": items}


@app.get("/api/public/creators")
async def creators_public() -> dict[str, Any]:
    with connect() as conn:
        season = current_season(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT c.name,c.department,c.role_title,c.note,c.sort_order,s.name AS season_name
            FROM creators c JOIN seasons s ON s.id=c.season_id
            WHERE c.season_id=? AND c.active=1 AND c.deleted_at IS NULL
            ORDER BY c.sort_order,c.department,c.name""", (season["id"],)
        ).fetchall()]
    return {"season": season["name"], "items": items}


@app.post("/api/admin/creators")
async def creators_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="创作者姓名不能为空")
    with transaction() as conn:
        season_id = str(payload.get("season_id") or current_season_id(conn))
        if not conn.execute("SELECT 1 FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)).fetchone():
            raise HTTPException(status_code=400, detail="所选赛季不存在")
        department = str(payload.get("department") or "").strip()[:80]
        _ensure_department(conn, department)
        now = utc_now()
        row = {
            "id": new_id("creator"), "season_id": season_id, "name": name[:80],
            "department": department, "role_title": str(payload.get("role_title") or "").strip()[:80],
            "note": str(payload.get("note") or "").strip()[:500],
            "active": int(bool(payload.get("active", True))),
            "sort_order": int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM creators WHERE season_id=?", (season_id,)
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
        season_id = str(payload.get("season_id") or item["season_id"])
        if not conn.execute("SELECT 1 FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)).fetchone():
            raise HTTPException(status_code=400, detail="所选赛季不存在")
        department = str(payload.get("department", item["department"]) or "").strip()[:80]
        _ensure_department(conn, department)
        item.update({
            "season_id": season_id, "name": name[:80], "department": department,
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
    await hub.notify()
    return item


@app.put("/api/invoices/{invoice_id}")
async def invoice_update(invoice_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_write(auth)
    item = save_invoice(payload, auth.user, invoice_id)
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
async def attachment_content(attachment_id: str, request: Request) -> FileResponse:
    get_auth(request)
    with connect() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL", (attachment_id,)).fetchone()
        if row and row["season_id"] != current_season_id(conn) and attachment_id not in _configured_public_media_ids(conn):
            row = None
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = attachment_path(row["stored_name"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="附件文件尚未同步到本机")
    return FileResponse(path, media_type=row["mime_type"], filename=row["original_name"])


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
        jobs = [
            {"attachment_id": attachment["id"], "invoice_id": draft["id"],
             "job_id": create_ocr_job(attachment["id"], auth.user["id"], draft["id"])}
            for attachment, draft in zip(attachments, drafts)
        ]
        await hub.notify()
        return {"attachments": attachments, "drafts": drafts, "jobs": jobs, "skipped": skipped, "count": len(attachments)}
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="ZIP 压缩包损坏或格式不正确") from exc
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
    jobs = [
        {"attachment_id": attachment["id"], "invoice_id": draft["id"],
         "job_id": create_ocr_job(attachment["id"], auth.user["id"], draft["id"])}
        for attachment, draft in zip(attachments, drafts)
    ]
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
            restore_snapshot(conn, snapshot_id, auth.user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await hub.notify("restored")
    return {"ok": True, "message": "历史版本已恢复，恢复前状态已自动保存"}


@app.delete("/api/admin/demo-data")
async def demo_delete(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    count = delete_demo_data(auth.user)
    await hub.notify()
    return {"ok": True, "deleted_count": count}


@app.put("/api/admin/settings")
async def settings_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    require_csrf(request, auth)
    require_admin(auth)
    allowed = {
        "team_name", "background_image", "background_media_id", "background_overlay", "accent_color",
        "login_slideshow_enabled", "login_slides", "login_transition", "loading_cars",
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
                elif key == "login_slideshow_enabled":
                    value = "1" if bool(payload[key]) else "0"
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
    writer.writerow(["开票日期", "上传日期", "提交人", "提交账号", "发票号码", "销售方", "总金额", "税额", "分类", "产品类型", "垫付人", "承担方式", "报销状态", "已报销金额", "报销日期", "资金来源", "参与成员", "备注"])
    burden_labels = {"team_aa": "全队AA", "self_paid": "个人承担", "specified_split": "指定成员分摊"}
    status_labels = {"pending": "未报销", "partial": "部分报销", "reimbursed": "已报销"}
    for item in invoices:
        writer.writerow([
            item["invoice_date"], str(item.get("uploaded_at") or item.get("created_at") or "").replace("T", " ")[:19],
            item.get("created_by_name") or "系统任务", item.get("created_by_username") or "",
            item["invoice_no"], item["vendor"], f"{item['total_amount']:.2f}", f"{item['tax_amount']:.2f}",
            item.get("category_name") or "", item["product_type"], item.get("payer_name") or "",
            burden_labels.get(item["burden_type"], item["burden_type"]), status_labels.get(item["reimbursement_status"], item["reimbursement_status"]),
            f"{item['reimbursed_amount']:.2f}", item.get("reimbursement_date") or "", item.get("funding_source_name") or "",
            "、".join(split["member_name"] for split in item["splits"]), item["note"],
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


def _create_backup_archive(output_path: Path | None = None) -> Path:
    if output_path is None:
        fd, name = tempfile.mkstemp(prefix="yxrt_backup_", suffix=".zip", dir=TMP_DIR)
        os.close(fd)
        output_path = Path(name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        manifest = {"product": "燕翔车队经费管理系统", "version": __version__, "created_at": utc_now()}
        with zipfile.ZipFile(output_path, "w", allowZip64=True) as archive:
            archive.write(db_temp, "database.sqlite", compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            for path in UPLOAD_DIR.rglob("*"):
                if path.is_file():
                    archive.write(path, Path("uploads") / path.relative_to(UPLOAD_DIR), compress_type=zipfile.ZIP_STORED)
        return output_path
    finally:
        db_temp.unlink(missing_ok=True)


@app.get("/api/admin/backup")
async def backup_download(request: Request, background_tasks: BackgroundTasks) -> FileResponse:
    auth = get_auth(request)
    require_admin(auth)
    path = await asyncio.to_thread(_create_backup_archive)
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="application/zip", filename=f"燕翔车队经费备份_{utc_now()[:10]}.zip")


def _restore_backup(path: Path, user_id: str) -> None:
    with zipfile.ZipFile(path) as archive:
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
                tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required.issubset(tables):
                    raise ValueError("备份数据库结构不完整")
                if not check.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
                    raise ValueError("备份中没有有效管理员账号")
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
