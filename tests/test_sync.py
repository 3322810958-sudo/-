from __future__ import annotations

from app.database import connect
from app.sync_engine import apply_events


def _category() -> dict:
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM categories WHERE id='cat_other'").fetchone())


def test_sync_last_write_wins_and_is_idempotent():
    current = _category()
    newer = {
        **current,
        "name": "同步后的其他分类",
        "updated_at": "2099-01-01T00:00:00.000000Z",
        "device_id": "remote-z",
        "version": int(current["version"]) + 1,
    }
    event = {
        "event_id": "event_sync_newer",
        "entity_type": "categories",
        "entity_id": current["id"],
        "action": "upsert",
        "payload": newer,
        "modified_at": newer["updated_at"],
        "device_id": newer["device_id"],
    }
    assert apply_events([event])["applied"] == 1
    assert _category()["name"] == "同步后的其他分类"
    assert apply_events([event]) == {"applied": 0, "ignored": 0}

    older = {**newer, "name": "过期修改", "updated_at": "2000-01-01T00:00:00.000000Z"}
    old_event = {**event, "event_id": "event_sync_older", "payload": older, "modified_at": older["updated_at"]}
    assert apply_events([old_event])["ignored"] == 1
    assert _category()["name"] == "同步后的其他分类"
