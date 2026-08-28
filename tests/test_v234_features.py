from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.test_api import login


def _new_invoice(client: TestClient, headers: dict[str, str], member_id: str, number: str, attachment_id: str) -> dict:
    response = client.post(
        "/api/invoices",
        json={
            "invoice_date": "2026-08-27", "total_amount": "10.00", "tax_amount": "0",
            "invoice_no": number, "vendor": f"测试商家{number}", "product_type": "其他",
            "category_id": "cat_other", "payer_member_id": member_id, "funding_source_id": "src_aa",
            "burden_type": "self_paid", "split_mode": "equal", "split_member_ids": [member_id],
            "reimbursed_amount": "0", "attachment_id": attachment_id,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_full_update_preserves_each_attachment_and_submitter() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        bootstrap = client.get("/api/bootstrap").json()
        members = [item["id"] for item in bootstrap["members"] if item["active"]]
        while len(members) < 2:
            created_member = client.post(
                "/api/members",
                json={"name": f"V234测试成员{len(members) + 1}", "department": "电气部", "avatar_color": "#27d3ff"},
                headers=headers,
            )
            assert created_member.status_code == 200, created_member.text
            members.append(created_member.json()["id"])
        attachment_ids = []
        for index in range(2):
            upload = client.post(
                "/api/attachments",
                files={"file": (f"batch-{index}.txt", f"invoice {index}".encode(), "text/plain")},
                headers=headers,
            )
            assert upload.status_code == 200, upload.text
            attachment_ids.append(upload.json()["attachment"]["id"])
        invoices = [_new_invoice(client, headers, members[0], f"BATCH-{index}", attachment_ids[index]) for index in range(2)]
        result = client.post(
            "/api/invoices/batch-action",
            json={
                "ids": [item["id"] for item in invoices], "action": "full_update",
                "invoice_date": "2026-08-28", "total_amount": "88.88", "tax_amount": "1.23",
                "invoice_no": "UNIFIED", "vendor": "统一商家", "product_type": "电气与三电",
                "category_id": "cat_electrical", "payer_member_id": members[1],
                "funding_source_id": "src_teacher", "burden_type": "specified_split",
                "split_mode": "equal", "split_member_ids": members[:2],
                "reimbursed_amount": "8.88", "reimbursement_date": "2026-08-29", "note": "批量相同设置",
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["changed_count"] == 2
        for index, original in enumerate(invoices):
            updated = client.get(f"/api/invoices/{original['id']}").json()
            assert updated["attachment_id"] == attachment_ids[index]
            assert updated["created_by_name"] == original["created_by_name"]
            assert updated["total_amount"] == 88.88
            assert updated["funding_source_id"] == "src_teacher"
            assert len(updated["splits"]) == 2


def test_login_info_required_items_and_independent_transparency() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        saved = client.put(
            "/api/admin/login-info",
            json={"interval": 2, "items": [{"id": "motto", "type": "motto", "title": "队训", "content": "修改内容", "visible": False}]},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        info = saved.json()["settings"]["login_info"]
        assert info["interval"] == 3
        required = {item["type"]: item for item in info["items"] if item["type"] in {"credits", "updates", "motto", "philosophy"}}
        assert set(required) == {"credits", "updates", "motto", "philosophy"}
        assert all(item["visible"] and item["required"] for item in required.values())
        appearance = client.put(
            "/api/admin/settings",
            json={"sidebar_transparency": 1, "topbar_transparency": 0},
            headers=headers,
        )
        assert appearance.status_code == 200, appearance.text
        settings = appearance.json()["settings"]
        assert settings["sidebar_transparency"] == 1
        assert settings["topbar_transparency"] == 0
        public = client.get("/api/public/appearance").json()["settings"]
        assert public["login_info"]["season_id"] == bootstrap_season(client)


def bootstrap_season(client: TestClient) -> str:
    return client.get("/api/bootstrap").json()["season"]["id"]


def test_ai_and_nas_configuration_stays_disabled_by_default() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        saved = client.put(
            "/api/admin/integrations/ai",
            json={"items": [{"id": "local_ollama", "name": "本地 Ollama", "kind": "ollama", "scope": "local", "base_url": "http://127.0.0.1:11434", "model": "qwen-coder", "enabled": False, "priority": 1}]},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["ai"][0]["enabled"] is False
        assert "不参与" in saved.json()["notice"]
        nas = client.put(
            "/api/admin/integrations/nas",
            json={"enabled": False, "protocol": "smb", "location": r"\\server\share", "username": ""},
            headers=headers,
        )
        assert nas.status_code == 200, nas.text
        assert nas.json()["nas"]["enabled"] is False
