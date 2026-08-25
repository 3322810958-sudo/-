from __future__ import annotations

import base64
import io
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app


def login(client: TestClient, username: str, password: str) -> tuple[dict, dict]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    payload = response.json()
    headers = {"X-CSRF-Token": payload["csrf_token"]}
    return payload, headers


def test_core_invoice_and_permissions():
    with TestClient(app) as client:
        login_payload, headers = login(client, "admin", "YXRT@2026")
        assert login_payload["user"]["role"] == "admin"
        bootstrap = client.get("/api/bootstrap").json()
        assert bootstrap["dashboard"]["invoice_count"] == 5
        member_ids = [item["id"] for item in bootstrap["members"] if item["active"]]
        payload = {
            "invoice_date": "2026-08-24", "total_amount": "100.01", "tax_amount": "0",
            "invoice_no": "TEST-001", "vendor": "测试加工厂", "product_type": "机械加工/零件",
            "category_id": "cat_machining", "payer_member_id": member_ids[0], "funding_source_id": "src_aa",
            "burden_type": "specified_split", "split_mode": "weighted", "split_member_ids": member_ids[:3],
            "split_weights": {member_ids[0]: 1, member_ids[1]: 2, member_ids[2]: 3},
            "reimbursed_amount": "20", "note": "自动化测试",
        }
        created = client.post("/api/invoices", json=payload, headers=headers)
        assert created.status_code == 200, created.text
        invoice = created.json()
        assert invoice["total_amount"] == 100.01
        assert round(sum(item["share_amount"] for item in invoice["splits"]), 2) == 100.01
        assert invoice["reimbursement_status"] == "partial"
        conflict_payload = {**payload, "version": invoice["version"] - 1}
        conflict = client.put(f"/api/invoices/{invoice['id']}", json=conflict_payload, headers=headers)
        assert conflict.status_code == 409

    with TestClient(app) as viewer:
        _, viewer_headers = login(viewer, "viewer", "View@2026")
        assert viewer.get("/api/invoices").status_code == 200
        denied = viewer.post("/api/invoices", json=payload, headers=viewer_headers)
        assert denied.status_code == 403
        reference_denied = viewer.post(
            "/api/categories", json={"name": "只读账号不可新增"}, headers=viewer_headers
        )
        assert reference_denied.status_code == 403


def test_reference_management():
    with TestClient(app) as client:
        _, headers = login(client, "member01", "Member@2026")
        category = client.post(
            "/api/categories",
            json={"name": "测试新分类", "color": "#336699", "active": True},
            headers=headers,
        )
        assert category.status_code == 200, category.text
        category_id = category.json()["id"]
        updated = client.put(
            f"/api/categories/{category_id}",
            json={"name": "测试分类已停用", "color": "#884422", "active": False},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["active"] == 0

        source = client.post(
            "/api/funding-sources",
            json={"name": "测试专项资金", "source_type": "sponsor", "color": "#22aa77"},
            headers=headers,
        )
        assert source.status_code == 200, source.text
        assert source.json()["source_type"] == "sponsor"


def test_members_snapshots_and_restore():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        member = client.post("/api/members", json={"name": "测试成员", "department": "测试组", "avatar_color": "#123456"}, headers=headers)
        assert member.status_code == 200, member.text
        member_id = member.json()["id"]
        account = client.post("/api/admin/users", json={"username": "testmember", "display_name": "测试成员", "member_id": member_id, "role": "member", "password": "Testing123"}, headers=headers)
        assert account.status_code == 200, account.text
        snapshots = client.get("/api/admin/snapshots").json()["items"]
        assert snapshots
        chosen = snapshots[0]["id"]
        before = client.get("/api/dashboard").json()["invoice_count"]
        restore = client.post(f"/api/admin/snapshots/{chosen}/restore", headers=headers)
        assert restore.status_code == 200, restore.text
        assert client.get("/api/dashboard").json()["invoice_count"] <= before

    with TestClient(app) as viewer:
        _, viewer_headers = login(viewer, "viewer", "View@2026")
        denied = viewer.post(f"/api/admin/snapshots/{chosen}/restore", headers=viewer_headers)
        assert denied.status_code == 403


def test_text_upload_ocr_and_exports():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        content = "发票号码：12345678\n开票日期：2026-08-24\n价税合计（小写）￥88.80\n销售方名称：测试商行"
        upload = client.post("/api/attachments", files={"file": ("invoice.txt", content.encode("utf-8"), "text/plain")}, headers=headers)
        assert upload.status_code == 200, upload.text
        attachment_id = upload.json()["attachment"]["id"]
        job_id = client.post(f"/api/ocr/{attachment_id}", headers=headers).json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/ocr/jobs/{job_id}").json()
            if job["status"] in {"done", "failed"}:
                break
        assert job["status"] == "done", job
        assert job["result"]["total_amount"] == 88.8
        csv_response = client.get("/api/export/csv")
        assert csv_response.status_code == 200
        assert csv_response.content.startswith(b"\xef\xbb\xbf")
        backup = client.get("/api/admin/backup")
        assert backup.status_code == 200
        assert backup.content[:2] == b"PK"


def test_v21_smart_classification_and_public_login_media():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        parsed = client.post(
            "/api/ocr/parse-text",
            json={"text": "项目名称：24V 固态继电器与接触器\n销售方名称：测试电气商行"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["product_type"] == "继电器/接触器"
        assert parsed.json()["category_id"] == "cat_electrical"

        rules = client.get("/api/admin/classification-rules")
        assert rules.status_code == 200
        assert any(item["id"] == "rule_relay" for item in rules.json()["items"])

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII=")
        upload = client.post(
            "/api/admin/appearance/media",
            files={"file": ("login.png", png, "image/png")},
            headers=headers,
        )
        assert upload.status_code == 200, upload.text
        media = upload.json()["media"]
        saved = client.put(
            "/api/admin/settings",
            json={
                "background_media_id": media["attachment_id"],
                "login_slideshow_enabled": True,
                "login_transition": "fade",
                "login_slides": [{**media, "duration": 5}],
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"]["login_slides"][0]["duration"] == 5

    with TestClient(app) as public:
        appearance = public.get("/api/public/appearance")
        assert appearance.status_code == 200
        slide = appearance.json()["settings"]["login_slides"][0]
        content = public.get(slide["url"])
        assert content.status_code == 200
        assert content.content == png


def test_v21_classification_rules_are_admin_only():
    with TestClient(app) as viewer:
        _, headers = login(viewer, "viewer", "View@2026")
        assert viewer.get("/api/admin/classification-rules").status_code == 403
        denied = viewer.put("/api/admin/classification-rules", json={"items": []}, headers=headers)
        assert denied.status_code == 403


def test_v22_selected_csv_batch_actions_and_submitter_columns():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        invoices = client.get("/api/invoices").json()["items"]
        chosen = invoices[:2]
        assert all(item.get("created_by_name") for item in chosen)
        assert all(item.get("uploaded_at") for item in chosen)

        selected_export = client.post(
            "/api/export/csv", json={"ids": [item["id"] for item in chosen]}, headers=headers
        )
        assert selected_export.status_code == 200, selected_export.text
        assert selected_export.headers["X-Export-Count"] == "2"
        expected_total = sum(float(item["total_amount"]) for item in chosen)
        assert float(selected_export.headers["X-Export-Total"]) == expected_total
        csv_text = selected_export.content.decode("utf-8-sig")
        assert csv_text.splitlines()[0] == "发票号码,总金额,分类,承担方式,资金来源,成员"
        assert "垫付：" in csv_text and "分摊：" in csv_text

        category_update = client.post(
            "/api/invoices/batch-action",
            json={"ids": [item["id"] for item in chosen], "action": "category", "category_id": "cat_electrical"},
            headers=headers,
        )
        assert category_update.status_code == 200, category_update.text
        assert category_update.json()["changed_count"] == 2

        status_update = client.post(
            "/api/invoices/batch-action",
            json={
                "ids": [item["id"] for item in chosen], "action": "status", "status": "reimbursed",
                "reimbursement_date": "2026-08-25",
            },
            headers=headers,
        )
        assert status_update.status_code == 200, status_update.text
        assert status_update.json()["changed_count"] == 2
        refreshed = {item["id"]: item for item in client.get("/api/invoices").json()["items"]}
        assert all(refreshed[item["id"]]["reimbursement_status"] == "reimbursed" for item in chosen)

    with TestClient(app) as viewer:
        _, viewer_headers = login(viewer, "viewer", "View@2026")
        denied = viewer.post(
            "/api/invoices/batch-action",
            json={"ids": [chosen[0]["id"]], "action": "delete"},
            headers=viewer_headers,
        )
        assert denied.status_code == 403


def test_v22_multiple_file_import_and_editable_loading_cars():
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        files = [
            ("files", ("invoice-a.txt", "发票号码：A001\n开票日期：2026-08-24\n价税合计（小写）￥12.34\n销售方名称：测试商店A".encode("utf-8"), "text/plain")),
            ("files", ("invoice-b.txt", "发票号码：B002\n开票日期：2026-08-25\n价税合计（小写）￥56.78\n销售方名称：测试商店B".encode("utf-8"), "text/plain")),
        ]
        imported = client.post(
            "/api/import/files",
            files=files,
            data={"payer_member_id": "member_01", "burden_type": "team_aa", "split_member_ids": "[]"},
            headers=headers,
        )
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result["count"] == 2
        assert len(result["jobs"]) == 2
        totals = []
        for queued in result["jobs"]:
            for _ in range(100):
                job = client.get(f"/api/ocr/jobs/{queued['job_id']}").json()
                if job["status"] in {"done", "failed"}:
                    break
            assert job["status"] == "done", job
            totals.append(job["result"]["total_amount"])
        assert sorted(totals) == [12.34, 56.78]

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII=")
        upload = client.post(
            "/api/admin/appearance/media",
            files={"file": ("custom-car.png", png, "image/png")},
            headers=headers,
        )
        media = upload.json()["media"]
        saved = client.put(
            "/api/admin/settings",
            json={"loading_cars": [{"attachment_id": media["attachment_id"], "title": "自定义测试赛车"}]},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"]["loading_cars"][0]["title"] == "自定义测试赛车"
        public = client.get("/api/public/appearance").json()["settings"]
        assert public["loading_cars"][0]["url"].startswith("/api/public/media/")


def test_season_isolation_and_global_creator_management():
    with TestClient(app) as admin:
        _, headers = login(admin, "admin", "YXRT@2026")
        initial = admin.get("/api/bootstrap").json()
        season_2026 = initial["season"]
        assert season_2026["name"] == "2026赛季"
        assert season_2026["is_open"] is True
        assert initial["members"]
        assert initial["dashboard"]["invoice_count"] > 0
        assert any(
            item["name"] == "刘松宁" and item["department"] == "电气部" and item["role_title"] == "高压"
            for item in initial["creators"]
        )

        created = admin.post("/api/admin/seasons", json={"name": "2027赛季"}, headers=headers)
        assert created.status_code == 200, created.text
        season_2027 = created.json()
        switched = admin.post(f"/api/admin/seasons/{season_2027['id']}/switch", headers=headers)
        assert switched.status_code == 200, switched.text
        fresh = admin.get("/api/bootstrap").json()
        assert fresh["season"]["name"] == "2027赛季"
        assert fresh["members"] == []
        assert fresh["dashboard"]["invoice_count"] == 0
        assert any(item["name"] == "刘松宁" for item in fresh["creators"])
        assert {item["name"] for item in fresh["departments"]}.issuperset({"电气部", "底盘部", "车身部", "市场部"})

        creator = admin.post(
            "/api/admin/creators",
            json={"name": "测试创作者", "department": "电气部", "role_title": "低压", "note": "赛季隔离测试"},
            headers=headers,
        )
        assert creator.status_code == 200, creator.text
        assert creator.json()["season_id"] == season_2027["id"]
        member = admin.post(
            "/api/members", json={"name": "2027成员", "department": "电气部"}, headers=headers
        )
        assert member.status_code == 200, member.text
        account = admin.post(
            "/api/admin/users",
            json={
                "username": "season2027member", "display_name": "2027成员",
                "member_id": member.json()["id"], "role": "member", "password": "Season2027",
            },
            headers=headers,
        )
        assert account.status_code == 200, account.text

        with TestClient(app) as old_member:
            denied = old_member.post("/api/auth/login", json={"username": "member01", "password": "Member@2026"})
            assert denied.status_code == 401
        with TestClient(app) as new_member:
            payload, _ = login(new_member, "season2027member", "Season2027")
            assert payload["user"]["role"] == "member"
            creator_names = {item["name"] for item in new_member.get("/api/creators").json()["items"]}
            assert creator_names.issuperset({"刘松宁", "测试创作者"})

        snapshot = admin.post(
            "/api/admin/snapshots", json={"label": "2027赛季隔离回溯测试"}, headers=headers
        )
        assert snapshot.status_code == 200, snapshot.text
        global_update = admin.put(
            f"/api/admin/creators/{creator.json()['id']}",
            json={"name": "全赛季测试创作者", "department": "电气部", "role_title": "低压"},
            headers=headers,
        )
        assert global_update.status_code == 200, global_update.text
        added_after_snapshot = admin.post(
            "/api/members", json={"name": "回溯后新增成员", "department": "底盘部"}, headers=headers
        )
        assert added_after_snapshot.status_code == 200, added_after_snapshot.text

        assert admin.post(f"/api/admin/seasons/{season_2026['id']}/switch", headers=headers).status_code == 200
        restored = admin.get("/api/bootstrap").json()
        assert restored["season"]["name"] == "2026赛季"
        assert restored["members"]
        assert all(item["name"] != "2027成员" for item in restored["members"])
        assert any(item["name"] == "刘松宁" for item in restored["creators"])
        assert any(item["name"] == "全赛季测试创作者" for item in restored["creators"])

        member_count_2026 = len(restored["members"])
        assert admin.post(f"/api/admin/seasons/{season_2027['id']}/switch", headers=headers).status_code == 200
        rollback = admin.post(
            f"/api/admin/snapshots/{snapshot.json()['id']}/restore", headers=headers
        )
        assert rollback.status_code == 200, rollback.text
        season_after_rollback = admin.get("/api/bootstrap").json()
        assert any(item["name"] == "2027成员" for item in season_after_rollback["members"])
        assert all(item["name"] != "回溯后新增成员" for item in season_after_rollback["members"])
        assert any(item["name"] == "全赛季测试创作者" for item in season_after_rollback["creators"])
        assert admin.post(f"/api/admin/seasons/{season_2026['id']}/switch", headers=headers).status_code == 200
        assert len(admin.get("/api/bootstrap").json()["members"]) == member_count_2026

        with TestClient(app) as viewer:
            _, viewer_headers = login(viewer, "viewer", "View@2026")
            assert any(item["name"] == "刘松宁" for item in viewer.get("/api/creators").json()["items"])
            assert len(viewer.get("/api/seasons").json()["items"]) == 1
            denied_creator_edit = viewer.post(
                "/api/admin/creators", json={"name": "禁止编辑"}, headers=viewer_headers
            )
            assert denied_creator_edit.status_code == 403

        archived = admin.put(
            f"/api/admin/seasons/{season_2027['id']}",
            json={"name": "2027赛季", "active": False},
            headers=headers,
        )
        assert archived.status_code == 200, archived.text
        assert admin.post(f"/api/admin/seasons/{season_2027['id']}/switch", headers=headers).status_code == 200
        assert admin.get("/api/bootstrap").json()["season"]["is_open"] is False
        read_only = admin.post("/api/members", json={"name": "禁止新增"}, headers=headers)
        assert read_only.status_code == 403
        assert admin.post(f"/api/admin/seasons/{season_2026['id']}/switch", headers=headers).status_code == 200


def test_v222_pdf_export_defaults_and_member_update_permissions(monkeypatch):
    with TestClient(app) as admin:
        _, headers = login(admin, "admin", "YXRT@2026")
        bootstrap = admin.get("/api/bootstrap").json()
        member_id = next(item["id"] for item in bootstrap["members"] if item["active"])
        defaults = admin.put(
            "/api/admin/invoice-defaults",
            json={
                "category_id": "cat_electrical", "payer_member_id": member_id,
                "funding_source_id": "src_aa", "burden_type": "specified_split",
                "split_member_ids": [member_id], "batch_note": "默认测试批次",
                "burden_labels": {"team_aa": "全员均摊", "specified_split": "选定成员", "self_paid": "个人自付"},
            },
            headers=headers,
        )
        assert defaults.status_code == 200, defaults.text
        saved_defaults = admin.get("/api/bootstrap").json()["settings"]["invoice_defaults"]
        assert saved_defaults["batch_note"] == "默认测试批次"
        assert saved_defaults["burden_labels"]["specified_split"] == "选定成员"

        pdf = io.BytesIO()
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842); writer.write(pdf)
        uploaded = admin.post(
            "/api/attachments", files={"file": ("source-invoice.pdf", pdf.getvalue(), "application/pdf")}, headers=headers
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment_id = uploaded.json()["attachment"]["id"]
        created = admin.post(
            "/api/invoices",
            json={
                "invoice_date": "2026-08-25", "total_amount": "66.60", "tax_amount": "0",
                "invoice_no": "PDF-222", "vendor": "PDF测试商家", "product_type": "电气元器件",
                "category_id": "cat_electrical", "payer_member_id": member_id, "funding_source_id": "src_aa",
                "burden_type": "self_paid", "split_mode": "equal", "split_member_ids": [member_id],
                "attachment_id": attachment_id,
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        invoice_id = created.json()["id"]
        separate = admin.post("/api/export/pdf", json={"mode": "separate", "ids": [invoice_id]}, headers=headers)
        assert separate.status_code == 200, separate.text
        assert separate.headers["X-Export-Count"] == "1"
        with zipfile.ZipFile(io.BytesIO(separate.content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
            assert len(names) == 1
            assert archive.read(names[0]).startswith(b"%PDF")
        merged = admin.post("/api/export/pdf", json={"mode": "merged", "ids": [invoice_id]}, headers=headers)
        assert merged.status_code == 200, merged.text
        assert merged.content.startswith(b"%PDF")

        import app.main as main_module
        old_mode = main_module.APP_MODE
        main_module.APP_MODE = "desktop"
        try:
            backup_target = main_module.TMP_DIR / "native-backup-test.zip"
            native_backup = admin.post(
                "/api/admin/backup/save", json={"target_path": str(backup_target)}, headers=headers
            )
            assert native_backup.status_code == 200, native_backup.text
            assert native_backup.json()["size"] > 0
            assert backup_target.read_bytes()[:2] == b"PK"
            backup_target.unlink()
        finally:
            main_module.APP_MODE = old_mode

    monkeypatch.setattr(main_module, "start_update_download", lambda user_id: {"id": "update_member", "created_by": user_id, "status": "downloading"})
    monkeypatch.setattr(main_module, "get_update_job", lambda job_id: {"id": job_id, "created_by": "user_viewer", "status": "ready", "progress": 100})
    monkeypatch.setattr(main_module, "schedule_update_install", lambda job_id, user_id: {"ok": True, "message": "测试安装", "user_id": user_id})
    with TestClient(app) as viewer:
        payload, viewer_headers = login(viewer, "viewer", "View@2026")
        denied_defaults = viewer.put("/api/admin/invoice-defaults", json={}, headers=viewer_headers)
        assert denied_defaults.status_code == 403
        download = viewer.post("/api/admin/update/download", headers=viewer_headers)
        assert download.status_code == 200, download.text
        monkeypatch.setattr(main_module, "get_update_job", lambda job_id: {"id": job_id, "created_by": payload["user"]["id"], "status": "ready", "progress": 100})
        assert viewer.get("/api/admin/update/jobs/update_member").status_code == 200
        assert viewer.post("/api/admin/update/jobs/update_member/install", headers=viewer_headers).status_code == 200
