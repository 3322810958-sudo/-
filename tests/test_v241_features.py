from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.test_api import login


def test_v241_gantt_excel_template_filters_and_upsert() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        admin_id = client.get("/api/plans/meta").json()["user"]["id"]
        original = client.post(
            "/api/plans/tasks",
            json={
                "external_id": "V241-PLAN-001",
                "title": "高压系统初版计划",
                "departments": ["电气部"],
                "assignee_user_ids": [admin_id],
                "status": "todo",
                "priority": "medium",
                "progress": 5,
            },
            headers=headers,
        )
        assert original.status_code == 200, original.text

        csv_data = (
            "任务编号,任务名称,组别,负责人账号,开始日期,截止日期,优先级,状态,进度,前置任务,父任务\n"
            "V241-PLAN-001,高压系统联调,电气部,admin,2026/09/01,2026/09/08,紧急,进行中,0.55,,\n"
            "V241-PLAN-002,整车动态验收,电气部,admin,2026-09-09,2026-09-12,高,未开始,0%,V241-PLAN-001,V241-PLAN-001\n"
        ).encode("utf-8")
        preview = client.post(
            "/api/plans/import",
            files={"file": ("plan.csv", csv_data, "text/csv")},
            data={"apply": "false", "strategy": "upsert"},
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["can_apply"] is True
        assert body["create_count"] == 1 and body["update_count"] == 1
        assert body["items"][0]["progress"] == 55

        applied = client.post(
            "/api/plans/import",
            files={"file": ("plan.csv", csv_data, "text/csv")},
            data={"apply": "true", "strategy": "upsert"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["created_count"] == 1
        tasks = client.get(
            "/api/plans/tasks",
            params={"department": "电气部", "assignee_id": admin_id, "priority": "urgent"},
        ).json()["items"]
        updated = next(item for item in tasks if item["external_id"] == "V241-PLAN-001")
        assert updated["title"] == "高压系统联调" and updated["progress"] == 55

        applied_again = client.post(
            "/api/plans/import",
            files={"file": ("plan.csv", csv_data, "text/csv")},
            data={"apply": "true", "strategy": "upsert"},
            headers=headers,
        )
        assert applied_again.status_code == 200, applied_again.text
        assert applied_again.json()["created_count"] == 0
        all_tasks = client.get("/api/plans/tasks").json()["items"]
        assert sum(item["external_id"] == "V241-PLAN-002" for item in all_tasks) == 1

        template = client.get("/api/plans/import-template.xlsx")
        assert template.status_code == 200
        assert template.content.startswith(b"PK")
        xlsx_preview = client.post(
            "/api/plans/import",
            files={"file": ("template.xlsx", template.content, template.headers["content-type"])},
            data={"apply": "false"},
            headers=headers,
        )
        assert xlsx_preview.status_code == 200, xlsx_preview.text
        assert xlsx_preview.json()["count"] == 2


def test_v241_component_excel_import_resources_and_statistics() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        created = client.post(
            "/api/inventory/components",
            json={
                "name": "V241 电流传感器",
                "category": "传感器",
                "manufacturer_part_no": "V241-SENSOR-001",
                "unit": "个",
                "minimum_quantity": 2,
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        component_id = created.json()["id"]
        stocked = client.post(
            "/api/inventory/movements",
            json={"component_id": component_id, "movement_type": "in", "quantity": 5},
            headers=headers,
        )
        assert stocked.status_code == 200, stocked.text

        csv_data = (
            "元件名称,分类,制造商,制造商型号,封装,库位,单位,库存数量,最低库存,参考单价,供应商,图片链接,数据手册链接,采购链接,备注\n"
            "V241 电流传感器,传感器,Allegro,V241-SENSOR-001,CB-5,A-01,个,2,3,68.5,测试供应商,https://example.com/a.png,https://example.com/a.pdf,https://example.com/buy,更新档案\n"
            "V241 隔离芯片,数字隔离,ADI,V241-ISO-002,SOIC-16,A-02,个,4,5,28.8,测试供应商,,,,新建档案\n"
        ).encode("utf-8")
        preview = client.post(
            "/api/inventory/components/import",
            files={"file": ("components.csv", csv_data, "text/csv")},
            data={"mode": "catalog", "apply": "false"},
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["can_apply"] is True
        assert preview.json()["create_count"] == preview.json()["update_count"] == 1

        catalog = client.post(
            "/api/inventory/components/import",
            files={"file": ("components.csv", csv_data, "text/csv")},
            data={"mode": "catalog", "apply": "true"},
            headers=headers,
        )
        assert catalog.status_code == 200, catalog.text
        existing = next(
            item for item in client.get("/api/inventory/components").json()["items"]
            if item["manufacturer_part_no"] == "V241-SENSOR-001"
        )
        assert existing["quantity"] == 5
        assert existing["supplier"] == "测试供应商"
        assert existing["purchase_url"] == "https://example.com/buy"

        stock_import = client.post(
            "/api/inventory/components/import",
            files={"file": ("components.csv", csv_data, "text/csv")},
            data={"mode": "stock_in", "apply": "true"},
            headers=headers,
        )
        assert stock_import.status_code == 200, stock_import.text
        assert stock_import.json()["movement_count"] == 2
        components = client.get("/api/inventory/components", params={"supplier": "测试供应商"}).json()["items"]
        quantities = {item["manufacturer_part_no"]: item["quantity"] for item in components}
        assert quantities["V241-SENSOR-001"] == 7
        assert quantities["V241-ISO-002"] == 4

        statistics = client.get("/api/inventory/statistics")
        assert statistics.status_code == 200
        assert any(item["category"] == "传感器" for item in statistics.json()["categories"])
        exported = client.get("/api/inventory/export.csv")
        assert exported.status_code == 200
        assert "采购链接" in exported.content.decode("utf-8-sig")

        invalid_url = client.post(
            "/api/inventory/components",
            json={"name": "不安全链接测试", "image_url": "javascript:alert(1)"},
            headers=headers,
        )
        assert invalid_url.status_code == 400

        template = client.get("/api/inventory/import-template.xlsx")
        assert template.status_code == 200 and template.content.startswith(b"PK")

