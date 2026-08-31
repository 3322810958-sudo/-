from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.integrations import IntegrationError, list_nas_backups, resolve_nas_backup, store_nas_backup
from app.main import app
from tests.test_api import login


def test_public_history_is_seeded_with_source_links() -> None:
    with TestClient(app) as client:
        response = client.get("/api/stories")
        assert response.status_code == 200
        payload = response.json()
        assert payload["can_edit"] is False
        by_id = {item["id"]: item for item in payload["items"]}
        assert "story_history_2007_honda" in by_id
        assert "story_history_2026_international" in by_id
        assert by_id["story_history_2007_honda"]["period_label"] == "2007 · 车队历史"
        assert "https://www.honda.com.cn/" in by_id["story_history_2007_honda"]["body"]


def test_nas_backup_copy_listing_and_path_guard(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"YXRT test archive")
    directory = tmp_path / "nas"
    config = {"enabled": True, "protocol": "local", "location": str(directory), "username": ""}
    stored = store_nas_backup(config, source, "燕翔车队经费完整备份_V2.3.9_20260831_120000.zip")
    assert stored["size"] == len(b"YXRT test archive")
    assert len(stored["sha256"]) == 64
    items = list_nas_backups(config)
    assert [item["filename"] for item in items] == [stored["filename"]]
    assert resolve_nas_backup(config, stored["filename"]).read_bytes() == source.read_bytes()
    with pytest.raises(IntegrationError):
        resolve_nas_backup(config, "../燕翔车队恶意.zip")


def test_admin_can_upload_complete_backup_to_saved_nas(tmp_path: Path) -> None:
    directory = tmp_path / "shared-backups"
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        saved = client.put(
            "/api/admin/integrations/nas",
            json={"enabled": True, "protocol": "local", "location": str(directory), "username": ""},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        uploaded = client.post("/api/admin/integrations/nas/backup", json={}, headers=headers)
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["filename"].startswith("燕翔车队经费完整备份_V2.3.9_")
        assert (directory / uploaded.json()["filename"]).is_file()
        listed = client.get("/api/admin/integrations/nas/backups")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["filename"] == uploaded.json()["filename"]


def test_v239_static_story_and_nas_controls_are_wired() -> None:
    index = Path("app/static/index.html").read_text(encoding="utf-8")
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    stories_js = Path("app/static/stories.js").read_text(encoding="utf-8")
    for element_id in ("nasBackupNowBtn", "nasRefreshBtn", "nasBackupList"):
        assert f'id="{element_id}"' in index
    assert "/api/admin/integrations/nas/backup" in app_js
    assert "/api/admin/integrations/nas/restore" in app_js
    assert "data-external-url" in stories_js
    assert "open_external_url" in stories_js
