from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import __version__
from .attachments import attachment_path, find_blob
from .config import TMP_DIR
from .database import (
    SYNC_TABLES,
    audit,
    connect,
    create_snapshot,
    get_device_id,
    setting,
    transaction,
    utc_now,
)


SYNC_LOCK = threading.Lock()
ALLOWED_SETTINGS = {
    "team_name", "background_image", "background_media_id", "background_media_kind",
    "background_overlay", "accent_color", "login_slideshow_enabled", "login_slides",
    "login_transition", "loading_cars", "classification_rules", "current_season_id",
}


class SyncError(RuntimeError):
    pass


def sync_config() -> dict[str, Any]:
    with connect() as conn:
        enabled = setting(conn, "sync_enabled", "0") == "1"
        remote_url = setting(conn, "remote_url", "").rstrip("/")
        secret = setting(conn, "sync_shared_secret", "")
        state = conn.execute("SELECT * FROM sync_state WHERE remote_id=?", (remote_url,)).fetchone() if remote_url else None
        pending = conn.execute("SELECT COUNT(*) FROM sync_events WHERE pushed=0").fetchone()[0]
        return {
            "enabled": enabled,
            "remote_url": remote_url,
            "secret_configured": bool(secret or os.environ.get("YXRT_SYNC_SHARED_SECRET")),
            "pending_events": int(pending),
            "last_push_at": state["last_push_at"] if state else None,
            "last_pull_at": state["last_pull_at"] if state else None,
            "last_error": state["last_error"] if state else "",
        }


def inbound_secret() -> str:
    if os.environ.get("YXRT_SYNC_SHARED_SECRET"):
        return os.environ["YXRT_SYNC_SHARED_SECRET"]
    with connect() as conn:
        return setting(conn, "sync_shared_secret", "")


def valid_sync_key(supplied: str) -> bool:
    expected = inbound_secret()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _table_columns(conn: Any, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _incoming_is_newer(current: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    if current is None:
        return True
    incoming_key = (str(payload.get("updated_at") or payload.get("created_at") or ""), str(payload.get("device_id") or ""))
    current_key = (str(current.get("updated_at") or current.get("created_at") or ""), str(current.get("device_id") or ""))
    return incoming_key > current_key


def apply_events(events: list[dict[str, Any]], source: str = "remote") -> dict[str, int]:
    applied = 0
    ignored = 0
    with transaction() as conn:
        unseen = [event for event in events if not conn.execute(
            "SELECT 1 FROM sync_events WHERE event_id=?", (event.get("event_id"),)
        ).fetchone()]
        if unseen:
            create_snapshot(conn, None, "云端同步前自动保护点", f"接收 {len(unseen)} 项变更")
        for event in unseen:
            table = str(event.get("entity_type") or "")
            entity_id = str(event.get("entity_id") or "")
            if table not in SYNC_TABLES and table != "app_settings":
                ignored += 1
                continue
            payload_raw = event.get("payload") if isinstance(event.get("payload"), dict) else event.get("payload_json", "{}")
            try:
                payload = payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw)
            except (TypeError, json.JSONDecodeError):
                ignored += 1
                continue
            if table == "app_settings":
                key = str(payload.get("key") or entity_id)
                if key not in ALLOWED_SETTINGS:
                    ignored += 1
                    continue
                current_row = conn.execute("SELECT * FROM app_settings WHERE key=?", (key,)).fetchone()
                current = dict(current_row) if current_row else None
                conflict_key = "key"
                payload["key"] = key
            else:
                current_row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
                current = dict(current_row) if current_row else None
                conflict_key = "id"
                payload["id"] = entity_id
            if _incoming_is_newer(current, payload):
                columns = _table_columns(conn, table)
                clean = {key: value for key, value in payload.items() if key in columns}
                if event.get("action") == "delete" and "deleted_at" in columns and not clean.get("deleted_at"):
                    clean["deleted_at"] = event.get("modified_at") or utc_now()
                if clean:
                    names = list(clean)
                    updates = ",".join(f"{name}=excluded.{name}" for name in names if name != conflict_key)
                    try:
                        conn.execute(
                            f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)}) "
                            f"ON CONFLICT({conflict_key}) DO UPDATE SET {updates}",
                            tuple(clean[name] for name in names),
                        )
                        applied += 1
                    except Exception as exc:
                        audit(conn, None, "sync_conflict", table, entity_id, {"error": str(exc), "source": source})
                        ignored += 1
            else:
                ignored += 1
            conn.execute(
                """INSERT OR IGNORE INTO sync_events(event_id,entity_type,entity_id,action,payload_json,
                attachment_sha256,modified_at,device_id,created_at,pushed) VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (
                    str(event.get("event_id")), table, entity_id, str(event.get("action") or "upsert"),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")), event.get("attachment_sha256"),
                    str(event.get("modified_at") or payload.get("updated_at") or utc_now()),
                    str(event.get("device_id") or payload.get("device_id") or source), utc_now(),
                ),
            )
        if unseen:
            audit(conn, None, "sync_apply", "system", None, {"source": source, "applied": applied, "ignored": ignored})
    return {"applied": applied, "ignored": ignored}


def events_after(seq: int, limit: int = 500) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_events WHERE seq>? ORDER BY seq LIMIT ?", (max(0, seq), min(max(1, limit), 1000))
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result


def _client():
    try:
        import httpx
    except ImportError as exc:
        raise SyncError("缺少同步组件 httpx，请重新运行安装程序") from exc
    return httpx.Client(timeout=httpx.Timeout(30.0, read=180.0, write=180.0), follow_redirects=True)


def _headers(secret: str) -> dict[str, str]:
    return {"X-Sync-Key": secret, "User-Agent": f"YanxiangExpenseV2/{__version__}"}


def _push_blob(client: Any, remote_url: str, secret: str, sha256: str) -> None:
    path = find_blob(sha256)
    if not path:
        raise SyncError(f"本地缺少附件文件：{sha256[:12]}")
    check = client.head(f"{remote_url}/api/sync/blob/{sha256}", headers=_headers(secret))
    if check.status_code == 200:
        return
    with path.open("rb") as stream:
        headers = _headers(secret)
        headers["X-File-Name"] = path.name
        response = client.put(f"{remote_url}/api/sync/blob/{sha256}", headers=headers, content=stream)
    if response.status_code >= 300:
        raise SyncError(f"附件上传失败：HTTP {response.status_code}")


def _download_blob(client: Any, remote_url: str, secret: str, sha256: str, stored_name: str) -> None:
    if find_blob(sha256):
        return
    destination = attachment_path(stored_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="yxrt_sync_", dir=TMP_DIR)
    os.close(fd)
    try:
        with client.stream("GET", f"{remote_url}/api/sync/blob/{sha256}", headers=_headers(secret)) as response:
            if response.status_code != 200:
                raise SyncError(f"附件下载失败：HTTP {response.status_code}")
            digest = hashlib.sha256()
            with open(temp_name, "wb") as target:
                for chunk in response.iter_bytes(4 * 1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
        if digest.hexdigest() != sha256:
            raise SyncError("附件校验失败，已停止写入")
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def perform_sync() -> dict[str, Any]:
    if not SYNC_LOCK.acquire(blocking=False):
        return {"ok": True, "message": "同步正在进行中"}
    try:
        with connect() as conn:
            enabled = setting(conn, "sync_enabled", "0") == "1"
            remote_url = setting(conn, "remote_url", "").rstrip("/")
            secret = setting(conn, "sync_shared_secret", "") or os.environ.get("YXRT_SYNC_SHARED_SECRET", "")
            device_id = get_device_id(conn)
            state = conn.execute("SELECT * FROM sync_state WHERE remote_id=?", (remote_url,)).fetchone() if remote_url else None
            last_pull_seq = int(state["last_pull_seq"]) if state else 0
        if not enabled:
            return {"ok": True, "message": "当前为本地模式"}
        if not remote_url or not secret:
            raise SyncError("请先由管理员填写云端地址和同步密钥")
        pushed_count = 0
        pulled_count = 0
        applied_count = 0
        with _client() as client:
            health = client.get(f"{remote_url}/api/sync/health", headers=_headers(secret))
            if health.status_code != 200:
                raise SyncError(f"无法连接云端同步服务：HTTP {health.status_code}")
            while True:
                with connect() as conn:
                    pending_rows = conn.execute(
                        "SELECT * FROM sync_events WHERE pushed=0 ORDER BY seq LIMIT 200"
                    ).fetchall()
                    pending = [dict(row) for row in pending_rows]
                if not pending:
                    break
                for event in pending:
                    if event.get("attachment_sha256"):
                        _push_blob(client, remote_url, secret, event["attachment_sha256"])
                wire_events = []
                for event in pending:
                    item = dict(event)
                    item["payload"] = json.loads(item.pop("payload_json"))
                    wire_events.append(item)
                response = client.post(
                    f"{remote_url}/api/sync/push", headers=_headers(secret),
                    json={"device_id": device_id, "events": wire_events},
                )
                if response.status_code != 200:
                    raise SyncError(f"上传变更失败：HTTP {response.status_code}")
                ids = [event["event_id"] for event in pending]
                with transaction() as conn:
                    conn.executemany("UPDATE sync_events SET pushed=1 WHERE event_id=?", [(value,) for value in ids])
                pushed_count += len(ids)

            while True:
                response = client.get(
                    f"{remote_url}/api/sync/pull", headers=_headers(secret),
                    params={"since": last_pull_seq, "limit": 500},
                )
                if response.status_code != 200:
                    raise SyncError(f"拉取云端变更失败：HTTP {response.status_code}")
                payload = response.json()
                events = payload.get("events") or []
                if not events:
                    break
                outcome = apply_events(events, source=remote_url)
                applied_count += outcome["applied"]
                pulled_count += len(events)
                last_pull_seq = max(int(event.get("seq") or 0) for event in events)
                for event in events:
                    if event.get("entity_type") == "attachments" and event.get("attachment_sha256"):
                        attachment = event.get("payload") or {}
                        _download_blob(client, remote_url, secret, event["attachment_sha256"], attachment.get("stored_name") or event["attachment_sha256"])
                if len(events) < 500:
                    break
        now = utc_now()
        with transaction() as conn:
            conn.execute(
                """INSERT INTO sync_state(remote_id,last_pull_seq,last_push_at,last_pull_at,last_error)
                VALUES(?,?,?,?, '') ON CONFLICT(remote_id) DO UPDATE SET last_pull_seq=excluded.last_pull_seq,
                last_push_at=excluded.last_push_at,last_pull_at=excluded.last_pull_at,last_error=''""",
                (remote_url, last_pull_seq, now, now),
            )
        return {"ok": True, "pushed": pushed_count, "pulled": pulled_count, "applied": applied_count, "synced_at": now}
    except Exception as exc:
        message = str(exc)
        try:
            with transaction() as conn:
                remote_url = setting(conn, "remote_url", "").rstrip("/") or "unconfigured"
                conn.execute(
                    """INSERT INTO sync_state(remote_id,last_error) VALUES(?,?)
                    ON CONFLICT(remote_id) DO UPDATE SET last_error=excluded.last_error""",
                    (remote_url, message[:1000]),
                )
        except Exception:
            pass
        raise SyncError(message) from exc
    finally:
        SYNC_LOCK.release()
