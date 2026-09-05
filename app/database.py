from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, DEVICE_LABEL
from .security import hash_password
from .story_history import HISTORICAL_STORIES


DB_LOCK = threading.RLock()
DEFAULT_SEASON_ID = "season_2026"
DEFAULT_SEASON_NAME = "2026赛季"
DEFAULT_DEPARTMENTS = (
    "电气部", "底盘部", "车身部", "市场部", "车架组", "悬架组", "电气组",
    "动力组", "制动组", "空气动力组", "运营组", "整车组",
)
BUSINESS_TABLES = (
    "departments",
    "creators",
    "members",
    "categories",
    "funding_sources",
    "attachments",
    "invoices",
    "invoice_splits",
    "settlements",
    "team_tasks",
    "inventory_components",
    "inventory_movements",
)
SEASON_BUSINESS_TABLES = (
    "members",
    "attachments",
    "invoices",
    "invoice_splits",
    "settlements",
    "team_tasks",
    "inventory_movements",
)
SYNC_TABLES = BUSINESS_TABLES + ("users", "seasons")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def transaction(*, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    with DB_LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with transaction() as conn:
        cursor = conn.execute(sql, params)
        return cursor.rowcount


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS departments (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS creators (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  name TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  role_title TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  name TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  student_id TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  avatar_color TEXT NOT NULL DEFAULT '#27d3ff',
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  member_id TEXT REFERENCES members(id) ON DELETE SET NULL,
  username TEXT NOT NULL COLLATE NOCASE UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','member','viewer')),
  active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  color TEXT NOT NULL DEFAULT '#27d3ff',
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS funding_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  source_type TEXT NOT NULL DEFAULT 'other',
  color TEXT NOT NULL DEFAULT '#9aa7b7',
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL,
  uploaded_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  invoice_no TEXT NOT NULL DEFAULT '',
  vendor TEXT NOT NULL DEFAULT '',
  invoice_date TEXT NOT NULL,
  total_amount_cents INTEGER NOT NULL CHECK(total_amount_cents >= 0),
  tax_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(tax_amount_cents >= 0),
  category_id TEXT REFERENCES categories(id) ON DELETE SET NULL,
  product_type TEXT NOT NULL DEFAULT '其他',
  payer_member_id TEXT REFERENCES members(id) ON DELETE SET NULL,
  burden_type TEXT NOT NULL CHECK(burden_type IN ('team_aa','self_paid','specified_split')),
  reimbursement_status TEXT NOT NULL DEFAULT 'pending' CHECK(reimbursement_status IN ('pending','partial','reimbursed')),
  reimbursed_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(reimbursed_amount_cents >= 0),
  reimbursement_date TEXT,
  funding_source_id TEXT REFERENCES funding_sources(id) ON DELETE SET NULL,
  note TEXT NOT NULL DEFAULT '',
  attachment_id TEXT REFERENCES attachments(id) ON DELETE SET NULL,
  ocr_text TEXT NOT NULL DEFAULT '',
  ocr_confidence REAL NOT NULL DEFAULT 0,
  ocr_status TEXT NOT NULL DEFAULT 'manual',
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS invoice_splits (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  share_cents INTEGER NOT NULL CHECK(share_cents >= 0),
  paid_cents INTEGER NOT NULL DEFAULT 0 CHECK(paid_cents >= 0),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','partial','paid')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE(invoice_id, member_id)
);

CREATE TABLE IF NOT EXISTS settlements (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  from_member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  to_member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  status TEXT NOT NULL DEFAULT 'paid' CHECK(status IN ('pending','paid','cancelled')),
  settled_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  device_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  label TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  state_gzip BLOB NOT NULL,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  source_device_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('upsert','delete')),
  payload_json TEXT NOT NULL,
  attachment_sha256 TEXT,
  modified_at TEXT NOT NULL,
  device_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  pushed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_state (
  remote_id TEXT PRIMARY KEY,
  last_pull_seq INTEGER NOT NULL DEFAULT 0,
  last_push_at TEXT,
  last_pull_at TEXT,
  last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ocr_jobs (
  id TEXT PRIMARY KEY,
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  invoice_id TEXT REFERENCES invoices(id) ON DELETE SET NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','processing','done','failed')),
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_supporting_attachments (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  attachment_kind TEXT NOT NULL DEFAULT 'other' CHECK(attachment_kind IN ('shopping','signature','other')),
  label TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(invoice_id,attachment_id)
);

CREATE TABLE IF NOT EXISTS invoice_quality_issues (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  invoice_id TEXT REFERENCES invoices(id) ON DELETE CASCADE,
  issue_type TEXT NOT NULL CHECK(issue_type IN ('low_confidence','import_failed','ocr_failed','export_failed','conversion_failed','attachment_missing','other')),
  severity TEXT NOT NULL DEFAULT 'warning' CHECK(severity IN ('warning','error')),
  field_name TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  settings_json TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_media (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL CHECK(media_type IN ('audio','background','avatar')),
  title TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_recovery (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  original_username_hash TEXT NOT NULL,
  original_password_hash TEXT NOT NULL,
  username_hint TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_reports (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  season_id TEXT REFERENCES seasons(id) ON DELETE SET NULL,
  reporter_name TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sent','failed')),
  delivery_method TEXT NOT NULL DEFAULT 'local_queue',
  delivery_reference TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  archive_name TEXT NOT NULL DEFAULT '',
  consent_public_attachment INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_attachments (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES feedback_reports(id) ON DELETE CASCADE,
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  author_name TEXT NOT NULL DEFAULT '',
  published_date TEXT NOT NULL DEFAULT '',
  layout_style TEXT NOT NULL DEFAULT 'editorial' CHECK(layout_style IN ('editorial','timeline','gallery','technical')),
  accent_color TEXT NOT NULL DEFAULT '#27d3ff',
  published INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS story_assets (
  id TEXT PRIMARY KEY,
  story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  asset_role TEXT NOT NULL DEFAULT 'document' CHECK(asset_role IN ('cover','gallery','video','document')),
  caption TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(story_id,attachment_id)
);

CREATE TABLE IF NOT EXISTS team_tasks (
  id TEXT PRIMARY KEY,
  season_id TEXT NOT NULL REFERENCES seasons(id),
  external_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','review','done','blocked')),
  priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low','medium','high','urgent')),
  start_date TEXT NOT NULL DEFAULT '',
  due_date TEXT NOT NULL DEFAULT '',
  progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
  parent_id TEXT REFERENCES team_tasks(id) ON DELETE SET NULL,
  department_json TEXT NOT NULL DEFAULT '[]',
  assignee_user_ids_json TEXT NOT NULL DEFAULT '[]',
  dependency_ids_json TEXT NOT NULL DEFAULT '[]',
  reminder_days_json TEXT NOT NULL DEFAULT '[7,3,1,0]',
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS task_reminder_state (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reminder_key TEXT NOT NULL,
  read_at TEXT,
  dismissed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(task_id,user_id,reminder_key)
);

CREATE TABLE IF NOT EXISTS inventory_components (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  manufacturer TEXT NOT NULL DEFAULT '',
  manufacturer_part_no TEXT NOT NULL DEFAULT '',
  mouser_part_no TEXT NOT NULL DEFAULT '',
  package TEXT NOT NULL DEFAULT '',
  parameters TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  unit TEXT NOT NULL DEFAULT '个',
  quantity REAL NOT NULL DEFAULT 0,
  minimum_quantity REAL NOT NULL DEFAULT 0,
  unit_cost_cents INTEGER NOT NULL DEFAULT 0,
  supplier TEXT NOT NULL DEFAULT '',
  image_url TEXT NOT NULL DEFAULT '',
  datasheet_url TEXT NOT NULL DEFAULT '',
  purchase_url TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  mouser_cache_json TEXT NOT NULL DEFAULT '{}',
  mouser_cached_at TEXT NOT NULL DEFAULT '',
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS inventory_movements (
  id TEXT PRIMARY KEY,
  component_id TEXT NOT NULL REFERENCES inventory_components(id),
  season_id TEXT NOT NULL REFERENCES seasons(id),
  requested_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  approved_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  movement_type TEXT NOT NULL CHECK(movement_type IN ('in','out','adjust')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected')),
  quantity REAL NOT NULL,
  unit_cost_cents INTEGER NOT NULL DEFAULT 0,
  batch_no TEXT NOT NULL DEFAULT '',
  project_name TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  device_id TEXT NOT NULL,
  deleted_at TEXT
);

DROP INDEX IF EXISTS idx_attachments_sha_active;
CREATE INDEX IF NOT EXISTS idx_attachments_sha_active ON attachments(sha256) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(reimbursement_status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_invoices_category ON invoices(category_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_invoices_payer ON invoices(payer_member_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_splits_invoice ON invoice_splits(invoice_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_splits_member ON invoice_splits(member_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_unpushed ON sync_events(pushed, seq);
CREATE INDEX IF NOT EXISTS idx_sync_modified ON sync_events(modified_at);
CREATE INDEX IF NOT EXISTS idx_supporting_invoice ON invoice_supporting_attachments(invoice_id,sort_order);
CREATE INDEX IF NOT EXISTS idx_quality_season_status ON invoice_quality_issues(season_id,status,issue_type,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_invoice ON invoice_quality_issues(invoice_id,status);
CREATE INDEX IF NOT EXISTS idx_user_media_owner ON user_media(user_id,media_type,sort_order);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_reports(status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stories_public ON stories(published,published_date DESC,sort_order) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_stories_season ON stories(season_id,published_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_story_assets_story ON story_assets(story_id,sort_order);
CREATE INDEX IF NOT EXISTS idx_team_tasks_season_due ON team_tasks(season_id,due_date,status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_team_tasks_updated ON team_tasks(updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_task_reminder_user ON task_reminder_state(user_id,read_at,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_part_no ON inventory_components(manufacturer_part_no) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_mouser_no ON inventory_components(mouser_part_no) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_category ON inventory_components(category,name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_movements_component ON inventory_movements(component_id,created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_movements_season ON inventory_movements(season_id,created_at DESC) WHERE deleted_at IS NULL;
"""


def get_device_id(conn: sqlite3.Connection | None = None) -> str:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()
        if row:
            return str(row[0])
        device_id = new_id("device")
        conn.execute("INSERT INTO meta(key,value) VALUES('device_id',?)", (device_id,))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('device_label',?)", (DEVICE_LABEL,))
        return device_id
    finally:
        if own:
            conn.close()


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str, *, sync: bool = True) -> None:
    now = utc_now()
    device_id = get_device_id(conn)
    existing = conn.execute("SELECT version FROM app_settings WHERE key=?", (key,)).fetchone()
    version = int(existing[0]) + 1 if existing else 1
    conn.execute(
        """INSERT INTO app_settings(key,value,updated_at,version,device_id) VALUES(?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,
        version=excluded.version,device_id=excluded.device_id""",
        (key, value, now, version, device_id),
    )
    if sync and key in {
        "team_name", "background_image", "background_media_id", "background_media_kind",
        "background_overlay", "accent_color", "login_slideshow_enabled", "login_slides",
        "login_transition", "loading_cars", "login_panel_opacity", "sidebar_transparency", "topbar_transparency", "login_random_enabled",
        "global_background_random", "classification_rules", "invoice_defaults", "current_season_id",
        "login_info_panels", "ai_connectors", "nas_config",
    }:
        enqueue_sync_event(conn, "app_settings", key, "upsert", {
            "key": key, "value": value, "updated_at": now, "version": version, "device_id": device_id
        })


def current_season_id(conn: sqlite3.Connection) -> str:
    configured = setting(conn, "current_season_id", DEFAULT_SEASON_ID).strip() or DEFAULT_SEASON_ID
    if conn.execute("SELECT 1 FROM seasons WHERE id=? AND deleted_at IS NULL", (configured,)).fetchone():
        return configured
    fallback = conn.execute(
        "SELECT id FROM seasons WHERE deleted_at IS NULL ORDER BY active DESC,sort_order DESC,created_at DESC LIMIT 1"
    ).fetchone()
    return str(fallback[0]) if fallback else DEFAULT_SEASON_ID


def current_season(conn: sqlite3.Connection) -> dict[str, Any]:
    season_id = current_season_id(conn)
    row = conn.execute("SELECT * FROM seasons WHERE id=? AND deleted_at IS NULL", (season_id,)).fetchone()
    if not row:
        return {"id": DEFAULT_SEASON_ID, "name": DEFAULT_SEASON_NAME, "active": 1, "is_open": True}
    item = dict(row)
    item["is_open"] = bool(item["active"])
    return item


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _department_id(name: str) -> str:
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:20]
    return f"department_{digest}"


def _migrate_season_schema(conn: sqlite3.Connection) -> None:
    now = utc_now()
    device_id = get_device_id(conn)
    conn.execute(
        """INSERT OR IGNORE INTO seasons(
        id,name,active,sort_order,created_at,updated_at,version,device_id,deleted_at
        ) VALUES(?,?,?,?,?,?,?,?,NULL)""",
        (DEFAULT_SEASON_ID, DEFAULT_SEASON_NAME, 1, 2026, now, now, 1, device_id),
    )
    for table in ("members", "attachments", "invoices", "invoice_splits", "settlements", "audit_logs", "snapshots"):
        _ensure_column(conn, table, "season_id", "TEXT REFERENCES seasons(id)")
        conn.execute(f"UPDATE {table} SET season_id=? WHERE season_id IS NULL OR trim(season_id)=''", (DEFAULT_SEASON_ID,))

    configured = setting(conn, "current_season_id", "").strip()
    if not configured or not conn.execute(
        "SELECT 1 FROM seasons WHERE id=? AND deleted_at IS NULL", (configured,)
    ).fetchone():
        set_setting(conn, "current_season_id", DEFAULT_SEASON_ID, sync=False)

    has_department_history = bool(conn.execute("SELECT 1 FROM departments LIMIT 1").fetchone())
    department_names = set() if has_department_history else set(DEFAULT_DEPARTMENTS)
    department_names.update(
        str(row[0]).strip() for row in conn.execute(
            "SELECT DISTINCT department FROM members WHERE trim(department)<>''"
        ).fetchall() if str(row[0]).strip()
    )
    for index, name in enumerate(sorted(department_names, key=lambda value: value.encode("utf-8"))):
        conn.execute(
            """INSERT OR IGNORE INTO departments(
            id,name,sort_order,created_at,updated_at,version,device_id,deleted_at
            ) VALUES(?,?,?,?,?,?,?,NULL)""",
            (_department_id(name), name, index, now, now, 1, device_id),
        )

    conn.execute(
        """INSERT OR IGNORE INTO creators(
        id,season_id,name,department,role_title,note,active,sort_order,created_at,updated_at,version,device_id,deleted_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
        (
            "creator_2026_liu_songning", DEFAULT_SEASON_ID, "刘松宁", "电气部", "高压",
            "燕翔车队经费管理系统创作者", 1, 0, now, now, 1, device_id,
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO creators(
        id,season_id,name,department,role_title,note,active,sort_order,created_at,updated_at,version,device_id,deleted_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
        (
            "creator_2026_duan_lipei", DEFAULT_SEASON_ID, "段力裴", "电气部", "高压",
            "燕翔车队经费管理系统创作者", 1, 1, now, now, 1, device_id,
        ),
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_members_season ON members(season_id,active,sort_order) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_attachments_season ON attachments(season_id,created_at DESC) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_invoices_season_date ON invoices(season_id,invoice_date DESC) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_splits_season ON invoice_splits(season_id,invoice_id) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_settlements_season ON settlements(season_id,created_at DESC) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_creators_season ON creators(season_id,active,sort_order) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_audit_season ON audit_logs(season_id,created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_season ON snapshots(season_id,created_at DESC)",
    ):
        conn.execute(statement)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','8')")


def _migrate_v23_schema(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "members", "avatar_attachment_id", "TEXT REFERENCES attachments(id) ON DELETE SET NULL")
    now = utc_now()
    device_id = get_device_id(conn)
    # Keep existing customized sources and add the commonly used team options
    # for upgraded databases. Administrators may still rename or disable them.
    common_sources = (
        ("src_yuanda_loan", "远达借款", "loan", "#f97316"),
        ("src_department_aa", "部门AA", "aa", "#fb7185"),
        ("src_personal_advance", "个人垫付", "other", "#38bdf8"),
        ("src_teacher_advance", "老师垫付", "reimbursement", "#ffb020"),
    )
    for index, (source_id, name, source_type, color) in enumerate(common_sources, start=100):
        conn.execute(
            """INSERT OR IGNORE INTO funding_sources(
            id,name,source_type,color,active,sort_order,created_at,updated_at,version,device_id,deleted_at
            ) VALUES(?,?,?,?,1,?,?,?,?,?,NULL)""",
            (source_id, name, source_type, color, index, now, now, 1, device_id),
        )
    admin = conn.execute(
        """SELECT id,username,password_hash FROM users
        WHERE role='admin' AND active=1 AND deleted_at IS NULL ORDER BY created_at LIMIT 1"""
    ).fetchone()
    if admin:
        conn.execute(
            """INSERT OR IGNORE INTO admin_recovery(
            user_id,original_username_hash,original_password_hash,username_hint,enabled,
            failed_attempts,locked_until,created_at,updated_at
            ) VALUES(?,?,?,?,1,0,NULL,?,?)""",
            (
                admin["id"], hashlib.sha256(str(admin["username"]).strip().lower().encode("utf-8")).hexdigest(),
                admin["password_hash"], f"{str(admin['username'])[:1]}***（原始管理员账号）", now, now,
            ),
        )


def seed_historical_stories(conn: sqlite3.Connection) -> int:
    """Add curated public history once without overwriting administrator edits."""
    now = utc_now()
    device_id = get_device_id(conn)
    colors = ("#df422f", "#0d8fa8", "#b97823", "#4969a8")
    inserted = 0
    for index, item in enumerate(HISTORICAL_STORIES):
        result = conn.execute(
            """INSERT OR IGNORE INTO stories(
            id,season_id,title,summary,body,author_name,published_date,layout_style,
            accent_color,published,sort_order,created_by,created_at,updated_at,version,device_id,deleted_at
            ) VALUES(?,?,?,?,?,?,?,?,?,1,?,NULL,?,?,1,?,NULL)""",
            (
                item["id"], DEFAULT_SEASON_ID, item["title"], item["summary"], item["body"],
                "燕翔车队历史资料整理", item["published_date"], item["layout_style"],
                colors[index % len(colors)], index, now, now, device_id,
            ),
        )
        inserted += int(result.rowcount or 0)
    return inserted


def _migrate_v239_schema(conn: sqlite3.Connection) -> None:
    seed_historical_stories(conn)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','9')")


def _migrate_v240_schema(conn: sqlite3.Connection) -> None:
    seed_historical_stories(conn)
    if not conn.execute("SELECT 1 FROM app_settings WHERE key='inventory_manager_ids'").fetchone():
        set_setting(conn, "inventory_manager_ids", "[]", sync=False)
    if not conn.execute("SELECT 1 FROM app_settings WHERE key='mouser_config'").fetchone():
        set_setting(conn, "mouser_config", '{"enabled":false,"api_key_encrypted":""}', sync=False)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','10')")


def _migrate_v241_schema(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "team_tasks", "external_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "inventory_components", "supplier", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "inventory_components", "purchase_url", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_team_tasks_external_id
        ON team_tasks(season_id,external_id)
        WHERE deleted_at IS NULL AND trim(external_id)<>''"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_inventory_supplier
        ON inventory_components(supplier,name) WHERE deleted_at IS NULL"""
    )
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','11')")


def audit(
    conn: sqlite3.Connection,
    user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_logs(
        id,season_id,user_id,action,entity_type,entity_id,detail_json,created_at,device_id
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            new_id("log"), current_season_id(conn), user_id, action, entity_type, entity_id,
            json.dumps(detail or {}, ensure_ascii=False), utc_now(), get_device_id(conn),
        ),
    )


def enqueue_sync_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    payload: dict[str, Any],
    attachment_sha256: str | None = None,
    *,
    pushed: int = 0,
) -> str:
    event_id = new_id("event")
    modified_at = str(payload.get("updated_at") or payload.get("created_at") or utc_now())
    conn.execute(
        """INSERT OR IGNORE INTO sync_events(
        event_id,entity_type,entity_id,action,payload_json,attachment_sha256,modified_at,device_id,created_at,pushed
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, entity_type, entity_id, action,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            attachment_sha256, modified_at, get_device_id(conn), utc_now(), pushed,
        ),
    )
    return event_id


def row_dict(conn: sqlite3.Connection, table: str, entity_id: str, id_column: str = "id") -> dict[str, Any] | None:
    if table not in SYNC_TABLES and table != "app_settings":
        raise ValueError("不支持的数据表")
    row = conn.execute(f"SELECT * FROM {table} WHERE {id_column}=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def snapshot_state(conn: sqlite3.Connection) -> dict[str, Any]:
    season_id = current_season_id(conn)
    state: dict[str, Any] = {
        "schema_version": 11,
        "season_id": season_id,
        "captured_at": utc_now(),
        "tables": {},
    }
    for table in (
        "members", "attachments", "invoices", "invoice_splits", "settlements",
        "invoice_quality_issues", "stories", "team_tasks", "inventory_movements",
    ):
        state["tables"][table] = [
            dict(row) for row in conn.execute(
                f"SELECT * FROM {table} WHERE season_id=?", (season_id,)
            ).fetchall()
        ]
    state["tables"]["users"] = [dict(row) for row in conn.execute(
        """SELECT u.* FROM users u JOIN members m ON m.id=u.member_id
        WHERE m.season_id=?""", (season_id,),
    ).fetchall()]
    state["tables"]["invoice_supporting_attachments"] = [dict(row) for row in conn.execute(
        """SELECT r.* FROM invoice_supporting_attachments r
        JOIN invoices i ON i.id=r.invoice_id WHERE i.season_id=?""", (season_id,),
    ).fetchall()]
    state["tables"]["ocr_jobs"] = [dict(row) for row in conn.execute(
        """SELECT DISTINCT o.* FROM ocr_jobs o LEFT JOIN attachments a ON a.id=o.attachment_id
        LEFT JOIN invoices i ON i.id=o.invoice_id WHERE a.season_id=? OR i.season_id=?""", (season_id, season_id),
    ).fetchall()]
    state["tables"]["user_preferences"] = [dict(row) for row in conn.execute(
        """SELECT p.* FROM user_preferences p JOIN users u ON u.id=p.user_id
        JOIN members m ON m.id=u.member_id WHERE m.season_id=?""", (season_id,),
    ).fetchall()]
    state["tables"]["user_media"] = [dict(row) for row in conn.execute(
        """SELECT um.* FROM user_media um JOIN attachments a ON a.id=um.attachment_id
        WHERE a.season_id=?""", (season_id,),
    ).fetchall()]
    state["tables"]["story_assets"] = [dict(row) for row in conn.execute(
        """SELECT sa.* FROM story_assets sa JOIN stories s ON s.id=sa.story_id
        WHERE s.season_id=?""", (season_id,),
    ).fetchall()]
    state["tables"]["task_reminder_state"] = [dict(row) for row in conn.execute(
        """SELECT r.* FROM task_reminder_state r JOIN team_tasks t ON t.id=r.task_id
        WHERE t.season_id=?""", (season_id,),
    ).fetchall()]
    for table in ("departments", "categories", "funding_sources", "app_settings", "inventory_components"):
        state["tables"][table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return state


def create_snapshot(
    conn: sqlite3.Connection,
    user_id: str | None,
    label: str,
    reason: str = "",
) -> str:
    snapshot_id = new_id("snapshot")
    raw = json.dumps(snapshot_state(conn), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    conn.execute(
        """INSERT INTO snapshots(
        id,season_id,label,reason,state_gzip,created_by,created_at,source_device_id
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            snapshot_id, current_season_id(conn), label[:80], reason[:300],
            gzip.compress(raw, compresslevel=6), user_id, utc_now(), get_device_id(conn),
        ),
    )
    conn.execute(
        """DELETE FROM snapshots WHERE id IN (
        SELECT id FROM snapshots WHERE season_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 100
        )""",
        (current_season_id(conn),),
    )
    return snapshot_id


def _insert_snapshot_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    for item in rows:
        columns = list(item)
        conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(item[column] for column in columns),
        )


def _restore_global_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if table == "app_settings":
        conn.execute("DELETE FROM app_settings")
        _insert_snapshot_rows(conn, table, rows)
        return
    snapshot_ids = {str(item["id"]) for item in rows}
    now = utc_now()
    for current in conn.execute(f"SELECT id FROM {table}").fetchall():
        if str(current["id"]) not in snapshot_ids:
            conn.execute(f"UPDATE {table} SET deleted_at=?,updated_at=? WHERE id=?", (now, now, current["id"]))
    for item in rows:
        columns = list(item)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(item[column] for column in columns),
        )


def restore_snapshot(conn: sqlite3.Connection, snapshot_id: str, user_id: str) -> dict[str, int]:
    row = conn.execute(
        "SELECT * FROM snapshots WHERE id=? AND season_id=?", (snapshot_id, current_season_id(conn))
    ).fetchone()
    if not row:
        raise ValueError("未找到该历史版本")
    create_snapshot(conn, user_id, "回溯前自动保护点", f"准备恢复到 {row['label']}")
    state = json.loads(gzip.decompress(row["state_gzip"]).decode("utf-8"))
    tables = state.get("tables", {})
    if not isinstance(tables, dict) or not {"members", "attachments", "invoices"}.issubset(tables):
        raise ValueError("历史版本内容不完整，已停止回溯")
    restore_season_id = str(row["season_id"] or current_season_id(conn))
    member_ids = [str(item["id"]) for item in tables.get("members", [])]
    attachment_ids = [str(item["id"]) for item in tables.get("attachments", [])]
    current_user_ids = [str(item[0]) for item in conn.execute(
        """SELECT u.id FROM users u JOIN members m ON m.id=u.member_id WHERE m.season_id=?""",
        (restore_season_id,),
    ).fetchall()]
    preserved_memberships = {
        str(item["id"]): str(item["member_id"]) for item in conn.execute(
            """SELECT u.id,u.member_id FROM users u JOIN members m ON m.id=u.member_id
            WHERE m.season_id=?""", (restore_season_id,),
        ).fetchall()
    }
    current_story_ids = [str(item[0]) for item in conn.execute(
        "SELECT id FROM stories WHERE season_id=?", (restore_season_id,),
    ).fetchall()]
    current_task_ids = [str(item[0]) for item in conn.execute(
        "SELECT id FROM team_tasks WHERE season_id=?", (restore_season_id,),
    ).fetchall()]
    current_invoice_ids = [str(item[0]) for item in conn.execute(
        "SELECT id FROM invoices WHERE season_id=?", (restore_season_id,),
    ).fetchall()]
    current_attachment_ids = [str(item[0]) for item in conn.execute(
        "SELECT id FROM attachments WHERE season_id=?", (restore_season_id,),
    ).fetchall()]

    def delete_ids(table: str, column: str, values: list[str]) -> None:
        if not values:
            return
        conn.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' for _ in values)})", values)

    if "stories" in tables:
        delete_ids("story_assets", "story_id", current_story_ids)
        conn.execute("DELETE FROM stories WHERE season_id=?", (restore_season_id,))
    if "team_tasks" in tables:
        delete_ids("task_reminder_state", "task_id", current_task_ids)
        conn.execute("DELETE FROM team_tasks WHERE season_id=?", (restore_season_id,))
    if "inventory_movements" in tables:
        conn.execute("DELETE FROM inventory_movements WHERE season_id=?", (restore_season_id,))
    delete_ids("invoice_supporting_attachments", "invoice_id", current_invoice_ids)
    conn.execute("DELETE FROM invoice_quality_issues WHERE season_id=?", (restore_season_id,))
    delete_ids("ocr_jobs", "attachment_id", current_attachment_ids)
    delete_ids("user_media", "attachment_id", current_attachment_ids)
    if "users" in tables:
        delete_ids("user_preferences", "user_id", current_user_ids)
    conn.execute("DELETE FROM invoice_splits WHERE season_id=?", (restore_season_id,))
    conn.execute("DELETE FROM settlements WHERE season_id=?", (restore_season_id,))
    conn.execute("DELETE FROM invoices WHERE season_id=?", (restore_season_id,))
    if "users" in tables:
        delete_ids("users", "id", current_user_ids)
    conn.execute("DELETE FROM members WHERE season_id=?", (restore_season_id,))
    conn.execute("DELETE FROM attachments WHERE season_id=?", (restore_season_id,))

    attachment_rows = []
    attachment_uploaders: dict[str, Any] = {}
    for raw in tables.get("attachments", []):
        item = {**raw, "season_id": restore_season_id}
        attachment_uploaders[str(item["id"])] = item.get("uploaded_by")
        item["uploaded_by"] = None
        attachment_rows.append(item)
    _insert_snapshot_rows(conn, "attachments", attachment_rows)
    for table in ("members", "users"):
        rows = [{**item, **({"season_id": restore_season_id} if "season_id" in item else {})} for item in tables.get(table, [])]
        _insert_snapshot_rows(conn, table, rows)
    if "users" not in tables:
        restored_member_ids = {str(item["id"]) for item in tables.get("members", [])}
        for preserved_user_id, preserved_member_id in preserved_memberships.items():
            conn.execute(
                "UPDATE users SET member_id=? WHERE id=?",
                (preserved_member_id if preserved_member_id in restored_member_ids else None, preserved_user_id),
            )
    restored_users = {str(item["id"]) for item in tables.get("users", [])}
    for attachment_id, uploaded_by in attachment_uploaders.items():
        conn.execute(
            "UPDATE attachments SET uploaded_by=? WHERE id=?",
            (uploaded_by if uploaded_by in restored_users or conn.execute("SELECT 1 FROM users WHERE id=?", (uploaded_by,)).fetchone() else None, attachment_id),
        )
    if "inventory_components" in tables:
        _restore_global_rows(conn, "inventory_components", tables.get("inventory_components", []))
    if "team_tasks" in tables:
        task_rows = [{**item, "season_id": restore_season_id} for item in tables.get("team_tasks", [])]
        task_parents = {str(item["id"]): item.get("parent_id") for item in task_rows}
        for item in task_rows:
            item["parent_id"] = None
        _insert_snapshot_rows(conn, "team_tasks", task_rows)
        for task_id, parent_id in task_parents.items():
            if parent_id:
                conn.execute("UPDATE team_tasks SET parent_id=? WHERE id=?", (parent_id, task_id))
    for table in (
        "invoices", "invoice_splits", "settlements", "invoice_supporting_attachments",
        "invoice_quality_issues", "ocr_jobs", "user_preferences", "user_media", "stories", "story_assets",
        "task_reminder_state", "inventory_movements",
    ):
        rows = [{**item, **({"season_id": restore_season_id} if "season_id" in item else {})} for item in tables.get(table, [])]
        _insert_snapshot_rows(conn, table, rows)
    for table in ("departments", "categories", "funding_sources", "app_settings"):
        if table in tables:
            _restore_global_rows(conn, table, tables.get(table, []))

    now = utc_now()
    device_id = get_device_id(conn)
    for table in SEASON_BUSINESS_TABLES:
        for item in conn.execute(
            f"SELECT * FROM {table} WHERE season_id=?", (restore_season_id,)
        ).fetchall():
            payload = dict(item)
            payload["updated_at"] = now
            payload["device_id"] = device_id
            enqueue_sync_event(
                conn, table, str(payload["id"]),
                "delete" if payload.get("deleted_at") else "upsert", payload,
                payload.get("sha256") if table == "attachments" else None,
            )
    audit(conn, user_id, "restore", "snapshot", snapshot_id, {"label": row["label"]})
    return {
        "invoices": len(tables.get("invoices", [])),
        "members": len(member_ids),
        "attachments": len(attachment_ids),
        "stories": len(tables.get("stories", [])),
    }


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    for item in rows:
        columns = list(item)
        conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(item[column] for column in columns),
        )


def seed_defaults(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    now = utc_now()
    device_id = get_device_id(conn)
    colors = ["#27d3ff", "#ff4d4d", "#ffb020", "#61e786", "#a78bfa", "#38bdf8", "#fb7185", "#facc15"]
    departments = ["车架组", "悬架组", "电气组", "动力组", "制动组", "空气动力组", "运营组", "整车组"]
    members = []
    for index in range(8):
        members.append({
            "id": f"member_{index + 1:02d}", "season_id": DEFAULT_SEASON_ID, "name": f"成员{index + 1:02d}",
            "department": departments[index], "student_id": "", "phone": "", "email": "",
            "avatar_color": colors[index], "active": 1, "sort_order": index,
            "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None,
        })
    _insert_rows(conn, "members", members)

    categories = [
        ("cat_material", "材料采购", "#27d3ff"), ("cat_machining", "加工服务", "#ffb020"),
        ("cat_electrical", "电气与三电", "#61e786"), ("cat_travel", "差旅交通", "#a78bfa"),
        ("cat_logistics", "物流运输", "#38bdf8"), ("cat_office", "办公资料", "#fb7185"),
        ("cat_other", "其他", "#9aa7b7"),
    ]
    _insert_rows(conn, "categories", [
        {"id": cid, "name": name, "color": color, "active": 1, "sort_order": i,
         "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None}
        for i, (cid, name, color) in enumerate(categories)
    ])
    sources = [
        ("src_team", "车队账目", "team", "#27d3ff"), ("src_innovation", "大创报销", "reimbursement", "#61e786"),
        ("src_teacher", "指导教师经费", "reimbursement", "#ffb020"), ("src_sponsor", "赞助款", "sponsor", "#a78bfa"),
        ("src_aa", "成员AA垫付", "aa", "#fb7185"), ("src_borrow", "借款", "loan", "#f97316"),
        ("src_other", "其他", "other", "#9aa7b7"),
    ]
    _insert_rows(conn, "funding_sources", [
        {"id": sid, "name": name, "source_type": source_type, "color": color, "active": 1, "sort_order": i,
         "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None}
        for i, (sid, name, source_type, color) in enumerate(sources)
    ])

    users = [{
        "id": "user_admin", "member_id": None, "username": "admin", "display_name": "系统管理员",
        "password_hash": hash_password("YXRT@2026"), "role": "admin", "active": 1, "must_change_password": 1,
        "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None,
    }]
    for index, member in enumerate(members, start=1):
        users.append({
            "id": f"user_member_{index:02d}", "member_id": member["id"], "username": f"member{index:02d}",
            "display_name": member["name"], "password_hash": hash_password("Member@2026"), "role": "member",
            "active": 1, "must_change_password": 1, "created_at": now, "updated_at": now,
            "version": 1, "device_id": device_id, "deleted_at": None,
        })
    users.append({
        "id": "user_viewer", "member_id": None, "username": "viewer", "display_name": "公共查看账号",
        "password_hash": hash_password("View@2026"), "role": "viewer", "active": 1, "must_change_password": 0,
        "created_at": now, "updated_at": now, "version": 1, "device_id": device_id, "deleted_at": None,
    })
    _insert_rows(conn, "users", users)

    for key, value in {
        "team_name": "燕翔车队 Racing Team",
        "background_image": "",
        "background_media_id": "",
        "background_media_kind": "image",
        "background_overlay": "0.82",
        "accent_color": "#27d3ff",
        "login_slideshow_enabled": "1",
        "login_slides": "[]",
        "login_transition": "fade",
        "loading_cars": "[]",
        "login_panel_opacity": "0.78",
        "sidebar_transparency": "0.22",
        "topbar_transparency": "0.22",
        "login_info_panels": "{}",
        "ai_connectors": "[]",
        "nas_config": "{}",
        "login_random_enabled": "0",
        "global_background_random": "0",
        "ocr_confidence_threshold": "0.80",
        "invoice_defaults": "{}",
        "classification_rules": "[]",
        "current_season_id": DEFAULT_SEASON_ID,
        "sync_enabled": "0",
        "remote_url": "",
        "sync_shared_secret": "",
    }.items():
        set_setting(conn, key, value, sync=False)

    demo_invoices = [
        ("demo_inv_01", "2026-08-03", 138000, 0, "cat_electrical", "线束/连接器", "member_03", "team_aa", "pending", 0, None, "src_aa", "高压互锁连接器与屏蔽线采购", "苏州某电子科技有限公司", "202608030001", list(range(1, 9))),
        ("demo_inv_02", "2026-08-08", 46850, 0, "cat_material", "3D打印/耗材", "member_01", "specified_split", "pending", 0, None, "src_aa", "转向限位与传感器支架打印材料", "某材料旗舰店", "202608080026", [1, 2, 3, 8]),
        ("demo_inv_03", "2026-08-12", 326000, 0, "cat_machining", "机械加工/零件", "member_02", "team_aa", "reimbursed", 326000, "2026-08-20", "src_innovation", "后轮立柱精加工", "无锡某精密机械厂", "202608120113", list(range(1, 9))),
        ("demo_inv_04", "2026-08-18", 98000, 0, "cat_logistics", "物流/快递", "member_07", "specified_split", "partial", 30000, "2026-08-22", "src_team", "赛车测试场往返运输", "某物流有限公司", "202608180078", [1, 2, 4, 7, 8]),
        ("demo_inv_05", "2026-08-21", 7560, 0, "cat_material", "紧固件/标准件", "member_05", "self_paid", "pending", 0, None, "src_aa", "M6法兰螺栓补充采购", "标准件商行", "202608210019", [5]),
    ]
    for item in demo_invoices:
        invoice_id, invoice_date, amount, tax, category_id, product_type, payer_id, burden, status, reimbursed, reimb_date, source_id, note, vendor, invoice_no, member_numbers = item
        conn.execute(
            """INSERT INTO invoices(id,season_id,invoice_no,vendor,invoice_date,total_amount_cents,tax_amount_cents,category_id,
            product_type,payer_member_id,burden_type,reimbursement_status,reimbursed_amount_cents,reimbursement_date,
            funding_source_id,note,attachment_id,ocr_text,ocr_confidence,ocr_status,is_demo,created_by,created_at,
            updated_at,version,device_id,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (invoice_id, DEFAULT_SEASON_ID, invoice_no, vendor, invoice_date, amount, tax, category_id, product_type, payer_id, burden,
             status, reimbursed, reimb_date, source_id, note, None, "", 0, "manual", 1, "user_admin", now, now, 1, device_id),
        )
        quotient, remainder = divmod(amount, len(member_numbers))
        for position, member_number in enumerate(member_numbers):
            share = quotient + (1 if position < remainder else 0)
            conn.execute(
                """INSERT INTO invoice_splits(id,season_id,invoice_id,member_id,share_cents,paid_cents,status,created_at,
                updated_at,version,device_id,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (new_id("split"), DEFAULT_SEASON_ID, invoice_id, f"member_{member_number:02d}", share, 0, "pending", now, now, 1, device_id),
            )
    audit(conn, "user_admin", "seed", "system", None, {"demo_invoices": len(demo_invoices)})
    create_snapshot(conn, "user_admin", "V2.1 初始演示数据", "首次初始化")


def init_db() -> None:
    logger = logging.getLogger("yxrt.database")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DB_LOCK:
        conn = connect()
        try:
            try:
                schema_row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                stories_ready = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stories'"
                ).fetchone()
            except sqlite3.Error:
                schema_row = None; stories_ready = None
            modules_ready = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_tasks'"
            ).fetchone() if stories_ready else None
            if schema_row and str(schema_row[0]) == "11" and stories_ready and modules_ready:
                logger.info("database ready schema_version=11 path=%s", DB_PATH)
                return
            logger.info("database migration started from=%s path=%s", schema_row[0] if schema_row else "unknown", DB_PATH)
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            _migrate_season_schema(conn)
            ocr_columns = {row[1] for row in conn.execute("PRAGMA table_info(ocr_jobs)").fetchall()}
            if "invoice_id" not in ocr_columns:
                conn.execute("ALTER TABLE ocr_jobs ADD COLUMN invoice_id TEXT REFERENCES invoices(id) ON DELETE SET NULL")
            seed_defaults(conn)
            _migrate_v23_schema(conn)
            _migrate_v239_schema(conn)
            _migrate_v240_schema(conn)
            _migrate_v241_schema(conn)
            conn.commit()
            logger.info("database migration completed schema_version=11")
        except Exception:
            conn.rollback()
            logger.exception("database initialization failed")
            raise
        finally:
            conn.close()
