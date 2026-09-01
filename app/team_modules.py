from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zipfile
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from . import __version__
from .auth import AuthContext, get_auth, require_admin, require_csrf, require_write
from .database import (
    audit,
    current_season,
    current_season_id,
    enqueue_sync_event,
    get_device_id,
    new_id,
    row_dict,
    set_setting,
    setting,
    transaction,
    utc_now,
)
from .integrations import IntegrationError, protect_secret, unprotect_secret


router = APIRouter(prefix="/api", tags=["team-modules"])
TASK_STATUSES = {"todo", "doing", "review", "done", "blocked"}
TASK_PRIORITIES = {"low", "medium", "high", "urgent"}
MOVEMENT_TYPES = {"in", "out", "adjust"}
MAX_IMPORT_ROWS = 2_000


def _json_list(value: Any, *, limit: int = 100) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = re.split(r"[,，;；\n]+", value)
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        clean = str(item if item is not None else "").strip()[:160]
        if clean and clean not in result:
            result.append(clean)
    return result


def _clean_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期格式不正确：{text}") from exc


def _season_is_open(conn: sqlite3.Connection, season_id: str) -> bool:
    row = conn.execute("SELECT active FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)).fetchone()
    return bool(row and row[0])


def _task_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("department_json", "assignee_user_ids_json", "dependency_ids_json", "reminder_days_json"):
        try:
            item[key.removesuffix("_json")] = json.loads(item.get(key) or "[]")
        except json.JSONDecodeError:
            item[key.removesuffix("_json")] = []
        item.pop(key, None)
    return item


def _task_clean(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()[:180]
    if not title:
        raise ValueError("请填写任务名称")
    status_value = str(payload.get("status") or "todo")
    priority = str(payload.get("priority") or "medium")
    if status_value not in TASK_STATUSES or priority not in TASK_PRIORITIES:
        raise ValueError("任务状态或优先级不正确")
    start_date = _clean_date(payload.get("start_date"))
    due_date = _clean_date(payload.get("due_date"))
    if start_date and due_date and start_date > due_date:
        raise ValueError("截止日期不能早于开始日期")
    reminders: list[int] = []
    raw_reminders = payload.get("reminder_days", [7, 3, 1, 0])
    if not isinstance(raw_reminders, list):
        raw_reminders = [7, 3, 1, 0]
    for value in raw_reminders[:20]:
        try:
            number = max(0, min(365, int(value)))
        except (TypeError, ValueError):
            continue
        if number not in reminders:
            reminders.append(number)
    reminders.sort(reverse=True)
    return {
        "title": title,
        "description": str(payload.get("description") or "").strip()[:20_000],
        "status": status_value,
        "priority": priority,
        "start_date": start_date,
        "due_date": due_date,
        "progress": max(0, min(100, int(payload.get("progress") or 0))),
        "parent_id": str(payload.get("parent_id") or "").strip() or None,
        "department_json": json.dumps(_json_list(payload.get("departments")), ensure_ascii=False),
        "assignee_user_ids_json": json.dumps(_json_list(payload.get("assignee_user_ids")), ensure_ascii=False),
        "dependency_ids_json": json.dumps(_json_list(payload.get("dependency_ids")), ensure_ascii=False),
        "reminder_days_json": json.dumps(reminders, ensure_ascii=False),
    }


def _can_manage_task(auth: AuthContext, item: dict[str, Any]) -> bool:
    if auth.user["role"] == "admin":
        return True
    assignees = _json_list(item.get("assignee_user_ids_json"))
    return auth.user["id"] == item.get("created_by") or auth.user["id"] in assignees


def _validate_task_links(conn: sqlite3.Connection, task_id: str, clean: dict[str, Any], season_id: str) -> None:
    links = [value for value in _json_list(clean["dependency_ids_json"]) if value != task_id]
    parent_id = clean.get("parent_id")
    if parent_id == task_id:
        raise ValueError("任务不能成为自己的父任务")
    candidates = links + ([parent_id] if parent_id else [])
    for candidate in candidates:
        row = conn.execute(
            "SELECT id FROM team_tasks WHERE id=? AND season_id=? AND deleted_at IS NULL", (candidate, season_id)
        ).fetchone()
        if not row:
            raise ValueError("前置任务或父任务不属于当前赛季")
    graph: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT id,dependency_ids_json FROM team_tasks WHERE season_id=? AND deleted_at IS NULL", (season_id,)
    ).fetchall():
        graph[str(row["id"])] = _json_list(row["dependency_ids_json"])
    graph[task_id] = links

    def reaches(start: str, target: str, seen: set[str]) -> bool:
        if start == target:
            return True
        if start in seen:
            return False
        seen.add(start)
        return any(reaches(next_id, target, seen) for next_id in graph.get(start, []))

    if any(reaches(dependency, task_id, set()) for dependency in links):
        raise ValueError("任务依赖形成了循环，请调整前置任务")
    clean["dependency_ids_json"] = json.dumps(links, ensure_ascii=False)


@router.get("/plans/meta")
def plans_meta(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with transaction(immediate=False) as conn:
        users = [dict(row) for row in conn.execute(
            """SELECT u.id,u.username,u.display_name,u.role,m.department FROM users u
            LEFT JOIN members m ON m.id=u.member_id WHERE u.active=1 AND u.deleted_at IS NULL
            ORDER BY u.display_name"""
        ).fetchall()]
        seasons = [dict(row) for row in conn.execute(
            "SELECT id,name,active FROM seasons WHERE deleted_at IS NULL ORDER BY sort_order DESC,created_at DESC"
        ).fetchall()]
        departments = [str(row[0]) for row in conn.execute(
            "SELECT name FROM departments WHERE deleted_at IS NULL ORDER BY sort_order,name"
        ).fetchall()]
        return {
            "user": {"id": auth.user["id"], "role": auth.user["role"], "display_name": auth.user["display_name"]},
            "csrf_token": auth.session["csrf_token"], "users": users, "seasons": seasons,
            "departments": departments, "current_season_id": current_season_id(conn), "version": __version__,
        }


@router.get("/plans/tasks")
def tasks_list(request: Request, season_id: str = "", status: str = "", search: str = "") -> dict[str, Any]:
    get_auth(request)
    with transaction(immediate=False) as conn:
        season_id = season_id or current_season_id(conn)
        params: list[Any] = [season_id]
        where = ["t.season_id=?", "t.deleted_at IS NULL"]
        if status in TASK_STATUSES:
            where.append("t.status=?"); params.append(status)
        if search.strip():
            where.append("(t.title LIKE ? OR t.description LIKE ?)")
            token = f"%{search.strip()[:100]}%"; params.extend((token, token))
        rows = conn.execute(
            f"""SELECT t.*,u.display_name AS creator_name FROM team_tasks t
            LEFT JOIN users u ON u.id=t.created_by WHERE {' AND '.join(where)}
            ORDER BY CASE t.status WHEN 'blocked' THEN 0 WHEN 'doing' THEN 1 WHEN 'review' THEN 2 WHEN 'todo' THEN 3 ELSE 4 END,
            CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            CASE WHEN t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.updated_at DESC""", tuple(params)
        ).fetchall()
        items = [_task_item(row) for row in rows]
        return {"items": items, "season_id": season_id, "season_open": _season_is_open(conn, season_id)}


@router.post("/plans/tasks")
def task_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    try:
        clean = _task_clean(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        season_id = current_season_id(conn); task_id = new_id("task")
        try:
            _validate_task_links(conn, task_id, clean, season_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        now = utc_now(); device_id = get_device_id(conn)
        item = {"id": task_id, "season_id": season_id, **clean, "created_by": auth.user["id"],
                "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None}
        columns = list(item)
        conn.execute(f"INSERT INTO team_tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(item[k] for k in columns))
        enqueue_sync_event(conn, "team_tasks", task_id, "upsert", item)
        audit(conn, auth.user["id"], "create", "team_task", task_id, {"title": clean["title"]})
    return {"ok": True, "id": task_id}


@router.put("/plans/tasks/{task_id}")
def task_update(task_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    try:
        clean = _task_clean(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        current = conn.execute("SELECT * FROM team_tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if not current:
            raise HTTPException(404, "任务不存在")
        item = dict(current)
        if not _season_is_open(conn, item["season_id"]):
            raise HTTPException(403, "归档赛季任务仅可查看")
        if not _can_manage_task(auth, item):
            raise HTTPException(403, "只能修改自己创建或负责的任务")
        try:
            _validate_task_links(conn, task_id, clean, item["season_id"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        now = utc_now(); version = int(item["version"]) + 1
        assignments = ",".join(f"{key}=?" for key in clean)
        conn.execute(f"UPDATE team_tasks SET {assignments},updated_at=?,version=?,device_id=? WHERE id=?",
                     (*clean.values(), now, version, get_device_id(conn), task_id))
        updated = row_dict(conn, "team_tasks", task_id)
        enqueue_sync_event(conn, "team_tasks", task_id, "upsert", updated or {})
        audit(conn, auth.user["id"], "update", "team_task", task_id, {"title": clean["title"]})
    return {"ok": True}


@router.delete("/plans/tasks/{task_id}")
def task_delete(task_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with transaction() as conn:
        item = conn.execute("SELECT * FROM team_tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if not item:
            raise HTTPException(404, "任务不存在")
        now = utc_now()
        conn.execute("UPDATE team_tasks SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?", (now, now, task_id))
        updated = row_dict(conn, "team_tasks", task_id)
        enqueue_sync_event(conn, "team_tasks", task_id, "delete", updated or {})
        audit(conn, auth.user["id"], "delete", "team_task", task_id, {"title": item["title"]})
    return {"ok": True}


@router.get("/plans/reminders")
def task_reminders(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    today = datetime.now().astimezone().date()
    with transaction(immediate=False) as conn:
        rows = conn.execute(
            """SELECT t.* FROM team_tasks t WHERE t.season_id=? AND t.deleted_at IS NULL
            AND t.status<>'done' AND trim(t.due_date)<>'' ORDER BY t.due_date""", (current_season_id(conn),)
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            task = dict(row); assignees = _json_list(task["assignee_user_ids_json"])
            if auth.user["role"] != "admin" and auth.user["id"] not in assignees and task.get("created_by") != auth.user["id"]:
                continue
            try:
                days = (date.fromisoformat(task["due_date"]) - today).days
            except ValueError:
                continue
            thresholds = [int(value) for value in _json_list(task["reminder_days_json"]) if str(value).isdigit()]
            if days < 0:
                key = "overdue"
            elif days in thresholds:
                key = f"day_{days}"
            else:
                continue
            state = conn.execute(
                "SELECT read_at,dismissed_at FROM task_reminder_state WHERE task_id=? AND user_id=? AND reminder_key=?",
                (task["id"], auth.user["id"], key),
            ).fetchone()
            if state and state["dismissed_at"]:
                continue
            items.append({"task_id": task["id"], "title": task["title"], "due_date": task["due_date"],
                          "days_left": days, "reminder_key": key, "read": bool(state and state["read_at"]),
                          "priority": task["priority"]})
        return {"items": items, "generated_at": utc_now()}


@router.post("/plans/reminders/{task_id}/{reminder_key}/read")
def reminder_read(task_id: str, reminder_key: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    reminder_key = re.sub(r"[^a-z0-9_-]", "", reminder_key.lower())[:40]
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM team_tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone():
            raise HTTPException(404, "任务不存在")
        now = utc_now(); reminder_id = new_id("reminder")
        conn.execute(
            """INSERT INTO task_reminder_state(id,task_id,user_id,reminder_key,read_at,dismissed_at,created_at,updated_at)
            VALUES(?,?,?,?,?,NULL,?,?) ON CONFLICT(task_id,user_id,reminder_key)
            DO UPDATE SET read_at=excluded.read_at,updated_at=excluded.updated_at""",
            (reminder_id, task_id, auth.user["id"], reminder_key, now, now, now),
        )
    return {"ok": True}


def _xlsx_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root]
        sheets = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        if not sheets:
            return []
        root = ElementTree.fromstring(archive.read(sheets[0]))
        rows: list[list[str]] = []
        for row in root.findall(".//{*}row"):
            values: list[str] = []
            for cell in row.findall("{*}c"):
                ref = cell.attrib.get("r", "A1")
                letters = re.match(r"[A-Z]+", ref.upper())
                column = 0
                for ch in (letters.group(0) if letters else "A"):
                    column = column * 26 + ord(ch) - 64
                while len(values) < column:
                    values.append("")
                node = cell.find("{*}v")
                raw = node.text if node is not None and node.text is not None else ""
                if cell.attrib.get("t") == "s" and raw.isdigit() and int(raw) < len(shared):
                    raw = shared[int(raw)]
                elif cell.attrib.get("t") == "inlineStr":
                    raw = "".join(cell.itertext())
                values[column - 1] = raw.strip()
            rows.append(values)
        return rows


def _tabular_rows(filename: str, data: bytes) -> list[dict[str, str]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "xlsx":
        raw_rows = _xlsx_rows(data)
    elif suffix in {"csv", "tsv"}:
        text = ""
        for encoding in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = data.decode(encoding); break
            except UnicodeError:
                continue
        delimiter = "\t" if suffix == "tsv" else ","
        raw_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    else:
        raise ValueError("仅支持 XLSX、CSV 和 TSV 文件")
    if not raw_rows:
        return []
    headers = [str(value).strip() for value in raw_rows[0]]
    return [{headers[index]: str(value).strip() for index, value in enumerate(row) if index < len(headers) and headers[index]}
            for row in raw_rows[1:MAX_IMPORT_ROWS + 1] if any(str(value).strip() for value in row)]


def _pick(row: dict[str, str], *names: str) -> str:
    normalized = {re.sub(r"[\s_\-/]+", "", key).lower(): value for key, value in row.items()}
    for name in names:
        key = re.sub(r"[\s_\-/]+", "", name).lower()
        if key in normalized:
            return normalized[key]
    return ""


def _task_import_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    result: list[dict[str, Any]] = []; errors: list[str] = []
    for number, row in enumerate(rows, 2):
        try:
            title = _pick(row, "任务名称", "任务", "title")
            if not title:
                raise ValueError("缺少任务名称")
            progress_text = _pick(row, "进度", "progress").replace("%", "") or "0"
            item = {
                "external_id": _pick(row, "任务编号", "编号", "id") or f"ROW-{number}",
                "title": title, "description": _pick(row, "描述", "备注", "description"),
                "status": _pick(row, "状态", "status") or "todo",
                "priority": _pick(row, "优先级", "priority") or "medium",
                "start_date": _pick(row, "开始日期", "开始时间", "startdate"),
                "due_date": _pick(row, "截止日期", "截止时间", "deadline", "duedate"),
                "progress": int(float(progress_text)),
                "departments": re.split(r"[,，;；]+", _pick(row, "部门", "组别", "department")),
                "assignee_names": re.split(r"[,，;；]+", _pick(row, "负责人", "负责人账号", "assignee")),
                "dependency_external_ids": re.split(r"[,，;；]+", _pick(row, "前置任务", "依赖任务", "dependencies")),
                "parent_external_id": _pick(row, "父任务", "parent"),
                "reminder_days": [7, 3, 1, 0],
            }
            item["status"] = {"未开始": "todo", "进行中": "doing", "待验收": "review", "已完成": "done", "阻塞": "blocked"}.get(item["status"], item["status"])
            item["priority"] = {"低": "low", "中": "medium", "高": "high", "紧急": "urgent"}.get(item["priority"], item["priority"])
            _task_clean(item)
            result.append(item)
        except (ValueError, TypeError) as exc:
            errors.append(f"第 {number} 行：{exc}")
    seen: set[str] = set()
    for number, item in enumerate(result, 2):
        external_id = str(item["external_id"])
        if external_id in seen:
            errors.append(f"第 {number} 行：任务编号重复（{external_id}）")
        seen.add(external_id)
    known = {str(item["external_id"]) for item in result}
    for number, item in enumerate(result, 2):
        references = [value.strip() for value in item["dependency_external_ids"] if value.strip()]
        if str(item["parent_external_id"] or "").strip():
            references.append(str(item["parent_external_id"]).strip())
        missing = sorted({value for value in references if value not in known})
        if missing:
            errors.append(f"第 {number} 行：引用了不存在的任务编号（{'、'.join(missing)}）")
    return result, errors


@router.get("/plans/import-template")
def task_import_template(request: Request) -> StreamingResponse:
    get_auth(request)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["任务编号", "任务名称", "描述", "部门", "负责人账号", "开始日期", "截止日期", "优先级", "状态", "进度", "前置任务", "父任务"])
    writer.writerow(["TASK-001", "完成电池箱模型修改", "检查安装空间", "电气部", "member01", "2026-09-01", "2026-09-07", "high", "doing", "40", "", ""])
    data = "\ufeff" + output.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename*=UTF-8''YXRT-Gantt-Template.csv"})


@router.post("/plans/import")
async def task_import(request: Request, file: UploadFile = File(...), apply: bool = Form(False)) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "甘特图文件不能超过 20MB")
    try:
        parsed, errors = _task_import_rows(_tabular_rows(file.filename or "", data))
    except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not apply:
        return {"items": parsed[:200], "count": len(parsed), "errors": errors[:100], "can_apply": bool(parsed and not errors)}
    if errors:
        raise HTTPException(400, "导入预检存在错误，请修正后重试")
    with transaction() as conn:
        season_id = current_season_id(conn); now = utc_now(); device_id = get_device_id(conn)
        users = [dict(row) for row in conn.execute("SELECT id,username,display_name FROM users WHERE active=1 AND deleted_at IS NULL").fetchall()]
        user_map = {str(value).strip().lower(): item["id"] for item in users for value in (item["username"], item["display_name"])}
        id_map = {item["external_id"]: new_id("task") for item in parsed}
        inserted: list[str] = []
        pending_parents: list[tuple[str, str | None]] = []
        for raw in parsed:
            raw["assignee_user_ids"] = [user_map[name.strip().lower()] for name in raw.pop("assignee_names") if name.strip().lower() in user_map]
            raw["dependency_ids"] = [id_map[value.strip()] for value in raw.pop("dependency_external_ids") if value.strip() in id_map]
            parent_external = raw.pop("parent_external_id", "")
            requested_parent_id = id_map.get(parent_external)
            raw["parent_id"] = None
            external_id = raw.pop("external_id")
            clean = _task_clean(raw); task_id = id_map[external_id]
            item = {"id": task_id, "season_id": season_id, **clean, "created_by": auth.user["id"], "created_at": now,
                    "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None}
            columns = list(item)
            conn.execute(f"INSERT INTO team_tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(item[k] for k in columns))
            pending_parents.append((task_id, requested_parent_id)); inserted.append(task_id)
        for task_id, parent_id in pending_parents:
            if parent_id:
                conn.execute("UPDATE team_tasks SET parent_id=? WHERE id=?", (parent_id, task_id))
            item = row_dict(conn, "team_tasks", task_id)
            enqueue_sync_event(conn, "team_tasks", task_id, "upsert", item or {})
        audit(conn, auth.user["id"], "import", "team_task", None, {"count": len(inserted), "filename": file.filename})
    return {"ok": True, "count": len(inserted)}


def _inventory_manager_ids(conn: sqlite3.Connection) -> list[str]:
    try:
        return _json_list(json.loads(setting(conn, "inventory_manager_ids", "[]")))
    except json.JSONDecodeError:
        return []


def _is_inventory_manager(conn: sqlite3.Connection, auth: AuthContext) -> bool:
    return auth.user["role"] == "admin" or auth.user["id"] in _inventory_manager_ids(conn)


def _component_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["low_stock"] = float(item["quantity"]) <= float(item["minimum_quantity"])
    item["stock_value_cents"] = round(float(item["quantity"]) * int(item["unit_cost_cents"]))
    item.pop("mouser_cache_json", None)
    return item


@router.get("/inventory/meta")
def inventory_meta(request: Request) -> dict[str, Any]:
    auth = get_auth(request)
    with transaction(immediate=False) as conn:
        users = [dict(row) for row in conn.execute(
            "SELECT id,username,display_name,role FROM users WHERE active=1 AND deleted_at IS NULL ORDER BY display_name"
        ).fetchall()]
        config = json.loads(setting(conn, "mouser_config", "{}") or "{}")
        return {"user": {"id": auth.user["id"], "role": auth.user["role"], "display_name": auth.user["display_name"]},
                "csrf_token": auth.session["csrf_token"], "users": users, "manager_ids": _inventory_manager_ids(conn),
                "can_manage": _is_inventory_manager(conn, auth), "mouser_enabled": bool(config.get("enabled")),
                "mouser_configured": bool(config.get("api_key_encrypted")), "version": __version__}


@router.put("/admin/inventory/managers")
def inventory_managers_save(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    ids = _json_list(payload.get("user_ids"))
    with transaction() as conn:
        valid = {str(row[0]) for row in conn.execute("SELECT id FROM users WHERE active=1 AND deleted_at IS NULL").fetchall()}
        ids = [value for value in ids if value in valid]
        set_setting(conn, "inventory_manager_ids", json.dumps(ids, ensure_ascii=False), sync=False)
        audit(conn, auth.user["id"], "update", "inventory_managers", None, {"count": len(ids)})
    return {"ok": True, "user_ids": ids}


@router.get("/inventory/components")
def components_list(request: Request, search: str = "", category: str = "", low_stock: bool = False) -> dict[str, Any]:
    get_auth(request)
    with transaction(immediate=False) as conn:
        params: list[Any] = []; where = ["deleted_at IS NULL"]
        if search.strip():
            token = f"%{search.strip()[:120]}%"; where.append("(name LIKE ? OR manufacturer_part_no LIKE ? OR mouser_part_no LIKE ? OR manufacturer LIKE ?)"); params.extend([token] * 4)
        if category.strip():
            where.append("category=?"); params.append(category.strip()[:100])
        if low_stock:
            where.append("quantity<=minimum_quantity")
        rows = conn.execute(f"SELECT * FROM inventory_components WHERE {' AND '.join(where)} ORDER BY category,name LIMIT 5000", tuple(params)).fetchall()
        items = [_component_item(row) for row in rows]
        return {"items": items, "count": len(items), "quantity": sum(float(item["quantity"]) for item in items),
                "value_cents": sum(int(item["stock_value_cents"]) for item in items),
                "low_stock_count": sum(1 for item in items if item["low_stock"])}


def _component_clean(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:180]
    part_no = str(payload.get("manufacturer_part_no") or "").strip()[:180]
    if not name and not part_no:
        raise ValueError("请填写元件名称或制造商型号")
    try:
        minimum = max(0.0, float(payload.get("minimum_quantity") or 0))
        unit_cost = max(0, round(float(payload.get("unit_cost") or 0) * 100))
    except (TypeError, ValueError) as exc:
        raise ValueError("库存阈值或单价格式不正确") from exc
    return {"name": name or part_no, "category": str(payload.get("category") or "未分类").strip()[:100],
            "manufacturer": str(payload.get("manufacturer") or "").strip()[:180], "manufacturer_part_no": part_no,
            "mouser_part_no": str(payload.get("mouser_part_no") or "").strip()[:180], "package": str(payload.get("package") or "").strip()[:120],
            "parameters": str(payload.get("parameters") or "").strip()[:1000], "location": str(payload.get("location") or "").strip()[:200],
            "unit": str(payload.get("unit") or "个").strip()[:30], "minimum_quantity": minimum, "unit_cost_cents": unit_cost,
            "image_url": str(payload.get("image_url") or "").strip()[:1000], "datasheet_url": str(payload.get("datasheet_url") or "").strip()[:1000],
            "note": str(payload.get("note") or "").strip()[:5000]}


@router.post("/inventory/components")
def component_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    try:
        clean = _component_clean(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        if not _is_inventory_manager(conn, auth):
            raise HTTPException(403, "仅元件库管理员可新建元件档案")
        component_id = new_id("component"); now = utc_now(); item = {"id": component_id, **clean, "quantity": 0,
            "mouser_cache_json": "{}", "mouser_cached_at": "", "created_by": auth.user["id"], "created_at": now,
            "updated_at": now, "version": 1, "device_id": get_device_id(conn), "deleted_at": None}
        columns = list(item); conn.execute(f"INSERT INTO inventory_components({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(item[k] for k in columns))
        enqueue_sync_event(conn, "inventory_components", component_id, "upsert", item)
        audit(conn, auth.user["id"], "create", "inventory_component", component_id, {"name": clean["name"]})
    return {"ok": True, "id": component_id}


@router.put("/inventory/components/{component_id}")
def component_update(component_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    try:
        clean = _component_clean(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        if not _is_inventory_manager(conn, auth):
            raise HTTPException(403, "仅元件库管理员可修改元件档案")
        current = conn.execute("SELECT * FROM inventory_components WHERE id=? AND deleted_at IS NULL", (component_id,)).fetchone()
        if not current: raise HTTPException(404, "元件不存在")
        now = utc_now(); assignments = ",".join(f"{key}=?" for key in clean)
        conn.execute(f"UPDATE inventory_components SET {assignments},updated_at=?,version=version+1,device_id=? WHERE id=?",
                     (*clean.values(), now, get_device_id(conn), component_id))
        updated = row_dict(conn, "inventory_components", component_id); enqueue_sync_event(conn, "inventory_components", component_id, "upsert", updated or {})
        audit(conn, auth.user["id"], "update", "inventory_component", component_id, {"name": clean["name"]})
    return {"ok": True}


@router.delete("/inventory/components/{component_id}")
def component_delete(component_id: str, request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with transaction() as conn:
        current = conn.execute("SELECT * FROM inventory_components WHERE id=? AND deleted_at IS NULL", (component_id,)).fetchone()
        if not current: raise HTTPException(404, "元件不存在")
        now = utc_now(); conn.execute("UPDATE inventory_components SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?", (now, now, component_id))
        updated = row_dict(conn, "inventory_components", component_id); enqueue_sync_event(conn, "inventory_components", component_id, "delete", updated or {})
        audit(conn, auth.user["id"], "delete", "inventory_component", component_id, {"name": current["name"]})
    return {"ok": True}


def _apply_movement(conn: sqlite3.Connection, movement: dict[str, Any], approver_id: str) -> None:
    component = conn.execute("SELECT * FROM inventory_components WHERE id=? AND deleted_at IS NULL", (movement["component_id"],)).fetchone()
    if not component:
        raise ValueError("元件档案不存在")
    quantity = float(movement["quantity"]); movement_type = movement["movement_type"]
    delta = quantity if movement_type in {"in", "adjust"} else -quantity
    new_quantity = float(component["quantity"]) + delta
    if new_quantity < -0.000001:
        raise ValueError(f"库存不足，当前仅有 {component['quantity']} {component['unit']}")
    now = utc_now(); component_version = int(component["version"]) + 1
    unit_cost = int(movement["unit_cost_cents"] or component["unit_cost_cents"])
    conn.execute("UPDATE inventory_components SET quantity=?,unit_cost_cents=?,updated_at=?,version=?,device_id=? WHERE id=?",
                 (new_quantity, unit_cost, now, component_version, get_device_id(conn), component["id"]))
    conn.execute("UPDATE inventory_movements SET status='applied',approved_by=?,updated_at=?,version=version+1,device_id=? WHERE id=?",
                 (approver_id, now, get_device_id(conn), movement["id"]))
    updated_component = row_dict(conn, "inventory_components", component["id"]); updated_movement = row_dict(conn, "inventory_movements", movement["id"])
    enqueue_sync_event(conn, "inventory_components", component["id"], "upsert", updated_component or {})
    enqueue_sync_event(conn, "inventory_movements", movement["id"], "upsert", updated_movement or {})


@router.get("/inventory/movements")
def movements_list(request: Request, component_id: str = "", status: str = "") -> dict[str, Any]:
    get_auth(request)
    with transaction(immediate=False) as conn:
        params: list[Any] = []; where = ["m.deleted_at IS NULL"]
        if component_id: where.append("m.component_id=?"); params.append(component_id)
        if status in {"pending", "applied", "rejected"}: where.append("m.status=?"); params.append(status)
        rows = conn.execute(f"""SELECT m.*,c.name AS component_name,c.unit,u.display_name AS requester_name
            FROM inventory_movements m JOIN inventory_components c ON c.id=m.component_id
            LEFT JOIN users u ON u.id=m.requested_by WHERE {' AND '.join(where)} ORDER BY m.created_at DESC LIMIT 1000""", tuple(params)).fetchall()
        return {"items": [dict(row) for row in rows]}


@router.post("/inventory/movements")
def movement_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    movement_type = str(payload.get("movement_type") or "out")
    if movement_type not in MOVEMENT_TYPES: raise HTTPException(400, "出入库类型不正确")
    try: quantity = float(payload.get("quantity") or 0)
    except (TypeError, ValueError) as exc: raise HTTPException(400, "数量格式不正确") from exc
    if quantity <= 0: raise HTTPException(400, "数量必须大于 0")
    with transaction() as conn:
        manager = _is_inventory_manager(conn, auth)
        if movement_type == "adjust" and not manager: raise HTTPException(403, "库存调整仅管理员可用")
        component_id = str(payload.get("component_id") or "")
        if not conn.execute("SELECT 1 FROM inventory_components WHERE id=? AND deleted_at IS NULL", (component_id,)).fetchone():
            raise HTTPException(404, "元件不存在")
        movement_id = new_id("movement"); now = utc_now()
        try: unit_cost = max(0, round(float(payload.get("unit_cost") or 0) * 100))
        except (TypeError, ValueError): unit_cost = 0
        item = {"id": movement_id, "component_id": component_id, "season_id": current_season_id(conn),
                "requested_by": auth.user["id"], "approved_by": None, "movement_type": movement_type,
                "status": "pending", "quantity": quantity, "unit_cost_cents": unit_cost,
                "batch_no": str(payload.get("batch_no") or "").strip()[:160],
                "project_name": str(payload.get("project_name") or "").strip()[:200],
                "note": str(payload.get("note") or "").strip()[:3000], "created_at": now, "updated_at": now,
                "version": 1, "device_id": get_device_id(conn), "deleted_at": None}
        columns = list(item); conn.execute(f"INSERT INTO inventory_movements({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(item[k] for k in columns))
        if manager: _apply_movement(conn, item, auth.user["id"])
        else: enqueue_sync_event(conn, "inventory_movements", movement_id, "upsert", item)
        audit(conn, auth.user["id"], "create", "inventory_movement", movement_id, {"type": movement_type, "quantity": quantity, "applied": manager})
    return {"ok": True, "id": movement_id, "status": "applied" if manager else "pending"}


@router.post("/inventory/movements/{movement_id}/decision")
def movement_decision(movement_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_write(auth)
    decision = str(payload.get("decision") or "")
    if decision not in {"approve", "reject"}: raise HTTPException(400, "处理结果不正确")
    with transaction() as conn:
        if not _is_inventory_manager(conn, auth): raise HTTPException(403, "仅元件库管理员可审批")
        movement_row = conn.execute("SELECT * FROM inventory_movements WHERE id=? AND status='pending' AND deleted_at IS NULL", (movement_id,)).fetchone()
        if not movement_row: raise HTTPException(404, "待审批记录不存在")
        movement = dict(movement_row)
        try:
            if decision == "approve": _apply_movement(conn, movement, auth.user["id"])
            else:
                now = utc_now(); conn.execute("UPDATE inventory_movements SET status='rejected',approved_by=?,updated_at=?,version=version+1,device_id=? WHERE id=?",
                                              (auth.user["id"], now, get_device_id(conn), movement_id))
                updated = row_dict(conn, "inventory_movements", movement_id); enqueue_sync_event(conn, "inventory_movements", movement_id, "upsert", updated or {})
        except ValueError as exc: raise HTTPException(400, str(exc)) from exc
        audit(conn, auth.user["id"], decision, "inventory_movement", movement_id, {})
    return {"ok": True}


def _bom_rows(rows: list[dict[str, str]], multiplier: float) -> tuple[list[dict[str, Any]], list[str]]:
    result: list[dict[str, Any]] = []; errors: list[str] = []
    for number, row in enumerate(rows, 2):
        try:
            quantity_text = _pick(row, "数量", "需求数量", "采购数量", "qty", "quantity") or "0"
            quantity = float(quantity_text) * multiplier
            if quantity <= 0: raise ValueError("数量必须大于 0")
            part_no = _pick(row, "制造商型号", "型号", "MPN", "manufacturerpartnumber")
            mouser_no = _pick(row, "贸泽料号", "mouserpartnumber")
            name = _pick(row, "元件名称", "名称", "描述", "description", "name") or part_no or mouser_no
            if not name: raise ValueError("缺少名称或型号")
            result.append({"name": name[:180], "manufacturer_part_no": part_no[:180], "mouser_part_no": mouser_no[:180],
                           "manufacturer": _pick(row, "制造商", "品牌", "manufacturer")[:180], "package": _pick(row, "封装", "package")[:120],
                           "category": (_pick(row, "分类", "category") or "未分类")[:100], "quantity": quantity,
                           "unit_cost": float(_pick(row, "单价", "unitprice") or 0), "location": _pick(row, "库位", "位置", "location")[:200]})
        except (TypeError, ValueError) as exc: errors.append(f"第 {number} 行：{exc}")
    return result, errors


def _match_component(conn: sqlite3.Connection, item: dict[str, Any]) -> sqlite3.Row | None:
    if item["mouser_part_no"]:
        row = conn.execute("SELECT * FROM inventory_components WHERE mouser_part_no=? COLLATE NOCASE AND deleted_at IS NULL LIMIT 1", (item["mouser_part_no"],)).fetchone()
        if row: return row
    if item["manufacturer_part_no"]:
        row = conn.execute("SELECT * FROM inventory_components WHERE manufacturer_part_no=? COLLATE NOCASE AND deleted_at IS NULL LIMIT 1", (item["manufacturer_part_no"],)).fetchone()
        if row: return row
    rows = conn.execute("SELECT * FROM inventory_components WHERE name=? COLLATE NOCASE AND package=? COLLATE NOCASE AND deleted_at IS NULL", (item["name"], item["package"])).fetchall()
    return rows[0] if len(rows) == 1 else None


@router.post("/inventory/bom-import")
async def bom_import(request: Request, file: UploadFile = File(...), mode: str = Form("compare"), production_count: float = Form(1), apply: bool = Form(False)) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth)
    data = await file.read()
    if len(data) > 30 * 1024 * 1024: raise HTTPException(413, "BOM 文件不能超过 30MB")
    try: items, errors = _bom_rows(_tabular_rows(file.filename or "", data), max(0.001, min(100000, float(production_count))))
    except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc: raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        preview: list[dict[str, Any]] = []
        for item in items:
            match = _match_component(conn, item); available = float(match["quantity"]) if match else 0.0
            preview.append({**item, "component_id": match["id"] if match else "", "available": available,
                            "shortage": max(0.0, float(item["quantity"]) - available), "match": "matched" if match else "new"})
        if not apply:
            return {"items": preview[:500], "count": len(preview), "errors": errors[:100], "can_apply": bool(preview and not errors), "mode": mode}
        require_write(auth)
        if errors: raise HTTPException(400, "BOM 预检存在错误，请修正后重试")
        if mode != "stock_in": raise HTTPException(400, "缺料比对无需写入，请使用导出或保存结果")
        if not _is_inventory_manager(conn, auth): raise HTTPException(403, "仅元件库管理员可执行 BOM 批量入库")
        now = utc_now(); device_id = get_device_id(conn); applied_count = 0
        for raw in preview:
            component_id = raw["component_id"]
            if not component_id:
                component_id = new_id("component")
                component = {"id": component_id, "name": raw["name"], "category": raw["category"], "manufacturer": raw["manufacturer"],
                    "manufacturer_part_no": raw["manufacturer_part_no"], "mouser_part_no": raw["mouser_part_no"], "package": raw["package"],
                    "parameters": "", "location": raw["location"], "unit": "个", "quantity": 0, "minimum_quantity": 0,
                    "unit_cost_cents": max(0, round(raw["unit_cost"] * 100)), "image_url": "", "datasheet_url": "", "note": "BOM 导入建立",
                    "mouser_cache_json": "{}", "mouser_cached_at": "", "created_by": auth.user["id"], "created_at": now, "updated_at": now,
                    "version": 1, "device_id": device_id, "deleted_at": None}
                columns = list(component); conn.execute(f"INSERT INTO inventory_components({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(component[k] for k in columns))
                enqueue_sync_event(conn, "inventory_components", component_id, "upsert", component)
            movement_id = new_id("movement")
            movement = {"id": movement_id, "component_id": component_id, "season_id": current_season_id(conn), "requested_by": auth.user["id"],
                "approved_by": None, "movement_type": "in", "status": "pending", "quantity": raw["quantity"],
                "unit_cost_cents": max(0, round(raw["unit_cost"] * 100)), "batch_no": "BOM批量入库", "project_name": "",
                "note": f"来源：{file.filename or 'BOM'}", "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None}
            columns = list(movement); conn.execute(f"INSERT INTO inventory_movements({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(movement[k] for k in columns))
            _apply_movement(conn, movement, auth.user["id"]); applied_count += 1
        audit(conn, auth.user["id"], "bom_import", "inventory", None, {"count": applied_count, "filename": file.filename})
        return {"ok": True, "count": applied_count}


@router.get("/inventory/mouser-config")
def mouser_config_get(request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_admin(auth)
    with transaction(immediate=False) as conn:
        raw = json.loads(setting(conn, "mouser_config", "{}") or "{}")
        return {"enabled": bool(raw.get("enabled")), "configured": bool(raw.get("api_key_encrypted"))}


@router.put("/admin/inventory/mouser-config")
def mouser_config_save(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    auth = get_auth(request); require_csrf(request, auth); require_admin(auth)
    with transaction() as conn:
        current = json.loads(setting(conn, "mouser_config", "{}") or "{}")
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            try: encrypted = protect_secret(api_key)
            except IntegrationError as exc: raise HTTPException(400, str(exc)) from exc
        else: encrypted = str(current.get("api_key_encrypted") or "")
        clean = {"enabled": bool(payload.get("enabled", False)), "api_key_encrypted": encrypted}
        set_setting(conn, "mouser_config", json.dumps(clean, ensure_ascii=False), sync=False)
        audit(conn, auth.user["id"], "update", "mouser_config", None, {"enabled": clean["enabled"], "configured": bool(encrypted)})
    return {"ok": True, "configured": bool(encrypted)}


@router.get("/inventory/mouser-search")
async def mouser_search(request: Request, q: str) -> dict[str, Any]:
    get_auth(request); query = q.strip()[:200]
    if len(query) < 2: raise HTTPException(400, "请输入完整型号或至少 2 个字符")
    with transaction(immediate=False) as conn: config = json.loads(setting(conn, "mouser_config", "{}") or "{}")
    if not config.get("enabled") or not config.get("api_key_encrypted"): raise HTTPException(400, "请先由管理员启用并配置贸泽 Search API")
    try: api_key = unprotect_secret(config["api_key_encrypted"])
    except IntegrationError as exc: raise HTTPException(400, str(exc)) from exc
    endpoint = "https://api.mouser.com/api/v1/search/keyword"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.post(endpoint, params={"apiKey": api_key}, json={"SearchByKeywordRequest": {"keyword": query, "records": 20, "startingRecord": 0, "searchOptions": "", "searchWithYourSignUpLanguage": ""}}, headers={"User-Agent": f"YXRT-Money-App/{__version__}", "Accept": "application/json"})
        if response.status_code in {401, 403}: raise HTTPException(400, "贸泽 API 密钥验证失败")
        if response.status_code >= 300: raise HTTPException(502, f"贸泽服务返回 HTTP {response.status_code}")
        payload = response.json(); parts = ((payload.get("SearchResults") or {}).get("Parts") or [])[:20]
        items = [{"mouser_part_no": str(part.get("MouserPartNumber") or ""), "manufacturer_part_no": str(part.get("ManufacturerPartNumber") or ""),
                  "manufacturer": str(part.get("Manufacturer") or ""), "description": str(part.get("Description") or ""),
                  "availability": str(part.get("Availability") or ""), "minimum_order": str(part.get("Min") or ""),
                  "lead_time": str(part.get("LeadTime") or ""), "datasheet_url": str(part.get("DataSheetUrl") or ""),
                  "image_url": str(part.get("ImagePath") or ""), "product_url": str(part.get("ProductDetailUrl") or ""),
                  "price_breaks": part.get("PriceBreaks") or []} for part in parts]
        return {"items": items, "count": len(items), "source": "Mouser Search API"}
    except httpx.TimeoutException as exc: raise HTTPException(504, "贸泽查询超时，请稍后重试") from exc
    except (httpx.HTTPError, ValueError) as exc: raise HTTPException(502, f"贸泽查询失败：{exc}") from exc
