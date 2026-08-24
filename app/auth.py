from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, Response, status

from .config import COOKIE_SECURE, SESSION_HOURS
from .database import audit, get_device_id, new_id, transaction, utc_now
from .security import new_token, token_hash, validate_new_password, validate_username, verify_password, hash_password


COOKIE_NAME = "yxrt_session"


@dataclass(slots=True)
class AuthContext:
    user: dict[str, Any]
    session: dict[str, Any]


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "member_id": user.get("member_id"),
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "must_change_password": bool(user["must_change_password"]),
        "permissions": {
            "write": user["role"] in {"admin", "member"},
            "manage_users": user["role"] == "admin",
            "restore_versions": user["role"] == "admin",
            "manage_settings": user["role"] == "admin",
            "export": True,
        },
    }


def login(username: str, password: str, response: Response) -> dict[str, Any]:
    with transaction() as conn:
        user_row = conn.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1 AND deleted_at IS NULL",
            (str(username or "").strip(),),
        ).fetchone()
        if not user_row or not verify_password(str(password or ""), user_row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码不正确")
        user = dict(user_row)
        raw_token = new_token()
        csrf = new_token(20)
        now = datetime.now(UTC)
        expires = now + timedelta(hours=SESSION_HOURS)
        conn.execute("DELETE FROM sessions WHERE expires_at<?", (utc_now(),))
        conn.execute(
            "INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,created_at,last_seen_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("session"), user["id"], token_hash(raw_token), csrf,
             expires.isoformat(timespec="milliseconds").replace("+00:00", "Z"), utc_now(), utc_now()),
        )
        audit(conn, user["id"], "login", "session", None, {})
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"user": public_user(user), "csrf_token": csrf}


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with transaction() as conn:
            row = conn.execute("SELECT user_id FROM sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            if row:
                audit(conn, row["user_id"], "logout", "session", None, {})
    response.delete_cookie(COOKIE_NAME, path="/")


def get_auth(request: Request) -> AuthContext:
    raw_token = request.cookies.get(COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    now = utc_now()
    with transaction(immediate=False) as conn:
        row = conn.execute(
            """SELECT s.id AS session_id,s.csrf_token,s.expires_at,s.last_seen_at,
            u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.deleted_at IS NULL""",
            (token_hash(raw_token), now),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")
        data = dict(row)
        session = {"id": data.pop("session_id"), "csrf_token": data.pop("csrf_token"),
                   "expires_at": data.pop("expires_at"), "last_seen_at": data.pop("last_seen_at")}
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now, session["id"]))
    return AuthContext(user=data, session=session)


def require_csrf(request: Request, auth: AuthContext) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(supplied, auth.session["csrf_token"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="安全令牌无效，请刷新页面后重试")


def require_write(auth: AuthContext) -> None:
    if auth.user["role"] not in {"admin", "member"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="公共账号仅可查看与导出")


def require_admin(auth: AuthContext) -> None:
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作")


def change_own_credentials(auth: AuthContext, current_password: str, username: str, password: str) -> dict[str, Any]:
    with transaction() as conn:
        current = conn.execute("SELECT * FROM users WHERE id=?", (auth.user["id"],)).fetchone()
        if not current or not verify_password(current_password, current["password_hash"]):
            raise ValueError("当前密码不正确")
        new_username = validate_username(username or current["username"])
        validate_new_password(password)
        duplicate = conn.execute(
            "SELECT id FROM users WHERE username=? COLLATE NOCASE AND id<>? AND deleted_at IS NULL",
            (new_username, current["id"]),
        ).fetchone()
        if duplicate:
            raise ValueError("该账号已被使用")
        now = utc_now()
        version = int(current["version"]) + 1
        device_id = get_device_id(conn)
        conn.execute(
            """UPDATE users SET username=?,password_hash=?,must_change_password=0,updated_at=?,version=?,device_id=?
            WHERE id=?""",
            (new_username, hash_password(password), now, version, device_id, current["id"]),
        )
        updated = dict(conn.execute("SELECT * FROM users WHERE id=?", (current["id"],)).fetchone())
        from .database import enqueue_sync_event
        enqueue_sync_event(conn, "users", current["id"], "upsert", updated)
        audit(conn, current["id"], "change_credentials", "user", current["id"], {"username": new_username})
        return public_user(updated)
