from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app
from app.story_engine import extract_embedded_images, extract_story_text
from app.updater import _release_details
from tests.test_api import login


def test_public_story_mode_admin_import_publish_and_snapshot_restore(tmp_path):
    with TestClient(app) as client:
        assert client.get("/stories").status_code == 200
        assert client.get("/api/stories").json()["can_edit"] is False

        _, headers = login(client, "admin", "YXRT@2026")
        season_id = client.get("/api/bootstrap").json()["season"]["id"]
        snapshot = client.post("/api/admin/snapshots", json={"label": "故事前"}, headers=headers)
        assert snapshot.status_code == 200
        created = client.post(
            "/api/admin/stories",
            json={
                "title": "赛季首测", "season_id": season_id, "published_date": "2026-08-29",
                "author_name": "测试成员", "summary": "测试摘要", "body": "", "published": False,
                "layout_style": "technical", "accent_color": "#27d3ff",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        story_id = created.json()["id"]
        uploaded = client.post(
            f"/api/admin/stories/{story_id}/assets",
            files=[("files", ("log.txt", "电池包绝缘测试完成".encode("utf-8"), "text/plain"))],
            data={"extract_content": "true"}, headers=headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        draft = client.get("/api/stories?include_drafts=1").json()
        assert draft["can_edit"] is True
        assert "电池包绝缘测试完成" in draft["items"][0]["body"]

        with TestClient(app) as visitor:
            assert all(item["id"] != story_id for item in visitor.get("/api/stories").json()["items"])

        published = client.put(
            f"/api/admin/stories/{story_id}",
            json={
                "title": "赛季首测", "season_id": season_id, "published_date": "2026-08-29",
                "author_name": "测试成员", "summary": "测试摘要", "body": "电池包绝缘测试完成",
                "published": True, "layout_style": "technical", "accent_color": "#27d3ff",
            }, headers=headers,
        )
        assert published.status_code == 200
        with TestClient(app) as visitor:
            public = visitor.get("/api/stories").json()["items"]
            assert any(item["id"] == story_id for item in public)
            asset_id = next(item for item in public if item["id"] == story_id)["assets"][0]["id"]
            assert visitor.get(f"/api/stories/assets/{asset_id}").content == "电池包绝缘测试完成".encode("utf-8")

        restored = client.post(f"/api/admin/snapshots/{snapshot.json()['id']}/restore", headers=headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored"]["stories"] == 0
        with TestClient(app) as visitor:
            assert all(item["id"] != story_id for item in visitor.get("/api/stories").json()["items"])


def test_office_story_extraction_and_patch_release_selection(tmp_path):
    office = tmp_path / "story.docx"
    with zipfile.ZipFile(office, "w") as archive:
        archive.writestr("word/document.xml", "<w:document xmlns:w='x'><w:p><w:t>赛车完成动态测试</w:t></w:p></w:document>")
        archive.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n")
    assert "赛车完成动态测试" in extract_story_text(office)
    media_dir = tmp_path / "media"; media_dir.mkdir()
    assert len(extract_embedded_images(office, media_dir)) == 1

    release = _release_details({
        "tag_name": "v2.3.3", "assets": [
            {"name": "完整版.zip", "url": "full", "browser_download_url": "full", "size": 10},
            {"name": "WindowsUpdate补丁.zip", "url": "patch", "browser_download_url": "patch", "size": 5},
            {"name": "WindowsUpdate补丁.zip.sha256", "url": "hash", "browser_download_url": "hash", "size": 1},
        ],
    })
    assert release["direction"] == "rollback"
    assert release["package"]["url"] == "patch"
    assert release["checksum_url"] == "hash"
