from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app
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
