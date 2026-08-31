from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app
from app.database import transaction
from app.desktop import desktop_server_config, open_edge_or_default, startup_page
from app.quality import record_issue, sync_ocr_issues
from app.updater import UPDATE_REPOSITORY, _safe_asset_url
from launcher import ensure_runtime_streams
from tests.test_api import login


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=200, height=200); writer.write(output)
    return output.getvalue()


def test_v230_bootstrap_references_preferences_and_recovery_status():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        bootstrap = client.get("/api/bootstrap").json()
        assert any(item["name"] == "段力裴" for item in bootstrap["creators"])
        assert any(item["name"] == "PaddleOCR" for item in bootstrap["open_source_references"])
        saved = client.put(
            "/api/user/preferences",
            json={"theme": "graphite", "shortcuts": {"new_invoice": "Ctrl+N"}, "audio_muted": True},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert client.get("/api/user/preferences").json()["settings"]["theme"] == "graphite"
        package = client.get("/api/user/settings-package/export")
        assert package.status_code == 200
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            assert "settings.json" in archive.namelist()
        recovery = client.get("/api/auth/recovery-status").json()
        assert recovery["enabled"] is True


def test_supporting_attachment_and_invoice_plus_attachment_export():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        bootstrap = client.get("/api/bootstrap").json()
        member_id = next(item["id"] for item in bootstrap["members"] if item["active"])
        uploaded = client.post(
            "/api/attachments",
            files={"file": ("invoice.pdf", _pdf_bytes(), "application/pdf")},
            headers=headers,
        ).json()["attachment"]
        invoice = client.post(
            "/api/invoices",
            json={
                "invoice_date": "2026-08-26", "total_amount": "18.80", "tax_amount": "0",
                "invoice_no": "V230-001", "vendor": "V230测试商家", "product_type": "其他",
                "payer_member_id": member_id, "burden_type": "self_paid", "split_mode": "equal",
                "split_member_ids": [member_id], "reimbursed_amount": "0", "attachment_id": uploaded["id"],
            },
            headers=headers,
        ).json()
        supporting = client.post(
            f"/api/invoices/{invoice['id']}/supporting-attachments",
            data={"attachment_kind": "signature", "label": "签字证明"},
            files={"file": ("proof.txt", "签字确认", "text/plain")},
            headers=headers,
        )
        assert supporting.status_code == 200, supporting.text
        detail = client.get(f"/api/invoices/{invoice['id']}").json()
        assert detail["supporting_attachments"][0]["attachment_kind"] == "signature"
        exported = client.post(
            "/api/export/pdf",
            json={"ids": [invoice["id"]], "mode": "separate", "include_supporting": True},
            headers=headers,
        )
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert any(name.endswith(".pdf") for name in archive.namelist())


def test_feedback_queue_and_avatar_permissions():
    with TestClient(app) as client:
        _, headers = login(client, "member01", "Member@2026")
        member_id = client.get("/api/bootstrap").json()["user"]["member_id"]
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        avatar = client.post(
            f"/api/members/{member_id}/avatar",
            files={"file": ("avatar.png", png, "image/png")},
            headers=headers,
        )
        assert avatar.status_code == 200, avatar.text
        feedback = client.post(
            "/api/feedback",
            data={"description": "批量导出偶发失败", "contact": "仅本地保存"},
            headers=headers,
        )
        assert feedback.status_code == 200, feedback.text
        assert feedback.json()["status"] in {"queued", "sent"}


def test_update_repository_matches_current_github_location():
    assert UPDATE_REPOSITORY == "3322810958-sudo/YXRT_Money_APP"
    assert _safe_asset_url(
        "https://github.com/3322810958-sudo/YXRT_Money_APP/releases/download/v2.3.3/update.zip"
    )


def test_windowed_launcher_supplies_streams_and_disables_uvicorn_terminal_logging(monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(sys, "stdout", None)
        context.setattr(sys, "stderr", None)
        ensure_runtime_streams()
        assert sys.stdout is not None and isinstance(sys.stdout.isatty(), bool)
        assert sys.stderr is not None and isinstance(sys.stderr.isatty(), bool)
    config = desktop_server_config("127.0.0.1", 8765)
    assert config.log_config is None
    assert config.access_log is False


def test_startup_failure_page_is_visible_and_patch_bundles_runtime():
    page = startup_page("启动异常 <测试>", failed=True, log_path=Path("C:/Temp/startup.log"))
    assert "启动失败" in page
    assert "启动异常 &lt;测试&gt;" in page
    assert "startup.log" in page
    build_script = (Path(__file__).parents[1] / "scripts" / "build_release.ps1").read_text(encoding="utf-8-sig")
    assert '"_internal"' in build_script
    assert 'runtime_bundle = "matched-pyinstaller-runtime"' in build_script
    smoke_script = (Path(__file__).parents[1] / "scripts" / "smoke_test_frozen.ps1").read_text(encoding="utf-8-sig")
    desktop_source = (Path(__file__).parents[1] / "app" / "desktop.py").read_text(encoding="utf-8-sig")
    assert 'YXRT_SMOKE_TEST = "1"' in smoke_script
    assert 'os.environ.get("YXRT_SMOKE_TEST") == "1"' in desktop_source


def test_startup_page_navigates_without_cross_thread_webview_calls():
    page = startup_page(log_path=Path("C:/Temp/startup.log"), target_url="http://127.0.0.1:8765")
    assert "fetch(target + '/health'" in page
    assert "window.location.replace(target)" in page
    assert "http://127.0.0.1:8765" in page
    desktop_source = (Path(__file__).parents[1] / "app" / "desktop.py").read_text(encoding="utf-8-sig")
    assert "window.load_url(url)" not in desktop_source


def test_windows_default_uses_isolated_edge_app_mode():
    desktop_source = (Path(__file__).parents[1] / "app" / "desktop.py").read_text(encoding="utf-8-sig")
    assert 'os.environ.get("YXRT_EMBEDDED_WEBVIEW") != "1"' in desktop_source
    assert 'f"--app={url}"' in desktop_source
    assert 'runtime_home / "data" / "edge-app"' in desktop_source
    assert "edge_app_running(edge_profile)" in desktop_source
    assert "edge_process.wait()" not in desktop_source


def test_successful_ocr_closes_previous_failure_issues():
    with transaction() as conn:
        invoice_id = str(conn.execute(
            "SELECT id FROM invoices WHERE deleted_at IS NULL ORDER BY created_at LIMIT 1"
        ).fetchone()[0])
        record_issue(conn, "ocr_failed", "测试识别失败", invoice_id=invoice_id, severity="error")
        record_issue(conn, "import_failed", "测试导入失败", invoice_id=invoice_id, severity="error")
        sync_ocr_issues(conn, invoice_id, {"ocr_confidence": 0.95, "uncertain_fields": []})
        remaining = conn.execute(
            """SELECT COUNT(*) FROM invoice_quality_issues
            WHERE invoice_id=? AND status='open' AND issue_type IN ('ocr_failed','import_failed')""",
            (invoice_id,),
        ).fetchone()[0]
        assert remaining == 0


def test_admin_can_clear_current_season_without_losing_admin_or_global_references():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        before = client.get("/api/bootstrap").json()
        assert before["members"] and before["dashboard"]["invoice_count"] > 0
        saved = client.put(
            "/api/user/preferences", json={"theme": "graphite", "shortcuts": {"dashboard": "Alt+1"}},
            headers=headers,
        )
        assert saved.status_code == 200
        cleared = client.delete("/api/admin/current-season-data", headers=headers)
        assert cleared.status_code == 200, cleared.text
        result = cleared.json()
        assert result["counts"]["invoices"] > 0
        assert result["counts"]["members"] > 0
        assert result["backup_path"].endswith(".zip")
        after = client.get("/api/bootstrap")
        assert after.status_code == 200
        payload = after.json()
        assert payload["user"]["role"] == "admin"
        assert payload["members"] == []
        assert payload["dashboard"]["invoice_count"] == 0
        assert payload["departments"]
        assert payload["creators"]
        assert client.get("/api/user/preferences").json()["settings"] == {}
        public_login = client.post(
            "/api/auth/login", json={"username": "viewer", "password": "View@2026"}
        )
        assert public_login.status_code == 200
