from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .attachments import attachment_path
from .database import audit, current_season, transaction


def _placeholders(values: Iterable[str]) -> tuple[list[str], str]:
    items = list(dict.fromkeys(str(value) for value in values if str(value)))
    return items, ",".join("?" for _ in items)


def _global_appearance_attachment_ids(conn: sqlite3.Connection) -> set[str]:
    protected: set[str] = set()
    rows = conn.execute(
        "SELECT key,value FROM app_settings WHERE key IN ('background_media_id','login_slides','loading_cars')"
    ).fetchall()
    for row in rows:
        if row["key"] == "background_media_id":
            if str(row["value"] or "").strip():
                protected.add(str(row["value"]).strip())
            continue
        try:
            values = json.loads(row["value"] or "[]")
        except json.JSONDecodeError:
            values = []
        if isinstance(values, list):
            protected.update(
                str(item.get("attachment_id") or "")
                for item in values if isinstance(item, dict) and item.get("attachment_id")
            )
    return protected


def clear_current_season_data(admin_user_id: str, current_session_id: str) -> dict[str, Any]:
    """Remove current-season working data while retaining global references and the active admin."""
    stored_names: list[str] = []
    with transaction() as conn:
        season = current_season(conn)
        season_id = str(season["id"])
        members = [dict(row) for row in conn.execute(
            "SELECT id,avatar_attachment_id FROM members WHERE season_id=?", (season_id,)
        ).fetchall()]
        member_ids, member_marks = _placeholders(row["id"] for row in members)
        accounts: list[dict[str, Any]] = []
        if member_ids:
            accounts = [dict(row) for row in conn.execute(
                f"SELECT id FROM users WHERE member_id IN ({member_marks})", member_ids
            ).fetchall()]
        account_ids = [row["id"] for row in accounts if row["id"] != admin_user_id]
        preference_user_ids = list(dict.fromkeys([admin_user_id, *account_ids]))

        invoices = [dict(row) for row in conn.execute(
            "SELECT id,attachment_id FROM invoices WHERE season_id=?", (season_id,)
        ).fetchall()]
        invoice_ids, invoice_marks = _placeholders(row["id"] for row in invoices)
        delete_attachment_ids = {
            str(row["attachment_id"]) for row in invoices if row.get("attachment_id")
        }
        if invoice_ids:
            delete_attachment_ids.update(str(row[0]) for row in conn.execute(
                f"SELECT attachment_id FROM invoice_supporting_attachments WHERE invoice_id IN ({invoice_marks})",
                invoice_ids,
            ).fetchall())
        delete_attachment_ids.update(
            str(row["avatar_attachment_id"]) for row in members if row.get("avatar_attachment_id")
        )

        preference_ids, preference_marks = _placeholders(preference_user_ids)
        if preference_ids:
            delete_attachment_ids.update(str(row[0]) for row in conn.execute(
                f"SELECT attachment_id FROM user_media WHERE user_id IN ({preference_marks})", preference_ids
            ).fetchall())

        feedback_ids = [str(row[0]) for row in conn.execute(
            "SELECT id FROM feedback_reports WHERE season_id=?", (season_id,)
        ).fetchall()]
        feedback_values, feedback_marks = _placeholders(feedback_ids)
        if feedback_values:
            delete_attachment_ids.update(str(row[0]) for row in conn.execute(
                f"SELECT attachment_id FROM feedback_attachments WHERE report_id IN ({feedback_marks})",
                feedback_values,
            ).fetchall())

        protected = _global_appearance_attachment_ids(conn)
        protected.update(str(row[0]) for row in conn.execute(
            """SELECT um.attachment_id FROM user_media um JOIN attachments a ON a.id=um.attachment_id
            WHERE um.user_id NOT IN (SELECT u.id FROM users u JOIN members m ON m.id=u.member_id WHERE m.season_id=?)
            AND um.user_id<>?""",
            (season_id, admin_user_id),
        ).fetchall())
        protected.update(str(row[0]) for row in conn.execute(
            "SELECT avatar_attachment_id FROM members WHERE season_id<>? AND avatar_attachment_id IS NOT NULL",
            (season_id,),
        ).fetchall())
        delete_attachment_ids.update(str(row[0]) for row in conn.execute(
            "SELECT id FROM attachments WHERE season_id=?", (season_id,)
        ).fetchall())
        delete_attachment_ids.difference_update(protected)

        attachment_values, attachment_marks = _placeholders(delete_attachment_ids)
        if attachment_values:
            stored_names = [str(row[0]) for row in conn.execute(
                f"SELECT stored_name FROM attachments WHERE id IN ({attachment_marks}) AND season_id=?",
                (*attachment_values, season_id),
            ).fetchall()]

        counts = {
            "invoices": len(invoice_ids), "members": len(member_ids),
            "accounts": len(account_ids), "attachments": len(stored_names),
        }

        if feedback_values:
            conn.execute(f"DELETE FROM feedback_reports WHERE id IN ({feedback_marks})", feedback_values)
        if preference_ids:
            conn.execute(f"DELETE FROM user_media WHERE user_id IN ({preference_marks})", preference_ids)
            conn.execute(f"DELETE FROM user_preferences WHERE user_id IN ({preference_marks})", preference_ids)
        account_values, account_marks = _placeholders(account_ids)
        if account_values:
            conn.execute(f"DELETE FROM sessions WHERE user_id IN ({account_marks})", account_values)
            conn.execute(f"DELETE FROM admin_recovery WHERE user_id IN ({account_marks})", account_values)
        conn.execute("UPDATE users SET member_id=NULL WHERE id=?", (admin_user_id,))
        if account_values:
            conn.execute(f"DELETE FROM users WHERE id IN ({account_marks})", account_values)

        conn.execute("DELETE FROM settlements WHERE season_id=?", (season_id,))
        conn.execute("DELETE FROM invoice_splits WHERE season_id=?", (season_id,))
        conn.execute("DELETE FROM invoices WHERE season_id=?", (season_id,))
        conn.execute("DELETE FROM members WHERE season_id=?", (season_id,))
        if attachment_values:
            conn.execute(
                f"DELETE FROM attachments WHERE id IN ({attachment_marks}) AND season_id=?",
                (*attachment_values, season_id),
            )

        deleted_entity_ids = [*invoice_ids, *member_ids, *account_ids, *attachment_values]
        entity_values, entity_marks = _placeholders(deleted_entity_ids)
        if entity_values:
            conn.execute(f"DELETE FROM sync_events WHERE entity_id IN ({entity_marks})", entity_values)
        conn.execute("DELETE FROM audit_logs WHERE season_id=?", (season_id,))
        conn.execute("DELETE FROM snapshots WHERE season_id=?", (season_id,))
        # The active session is explicitly retained so the administrator can
        # immediately rebuild members and accounts after the reset.
        conn.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (admin_user_id, current_session_id))
        audit(conn, admin_user_id, "clear_season_data", "season", season_id, counts)

    removed_files = 0
    for stored_name in stored_names:
        try:
            path = attachment_path(stored_name)
            if path.is_file():
                path.unlink()
                removed_files += 1
        except OSError:
            continue
    return {"season": season, "counts": counts, "removed_files": removed_files}
