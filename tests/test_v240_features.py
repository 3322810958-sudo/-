from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app import __version__
from app.main import app
from tests.test_api import login


def test_v240_pages_plan_tasks_import_and_reminders() -> None:
    assert __version__ == "2.4.0"
    with TestClient(app) as client:
        assert client.get("/plans").status_code == 200
        assert client.get("/components").status_code == 200
        _, headers = login(client, "admin", "YXRT@2026")
        meta = client.get("/api/plans/meta").json()
        admin_id = meta["user"]["id"]
        created = client.post(
            "/api/plans/tasks",
            json={
                "title": "完成高压绝缘检查",
                "departments": ["电气部"],
                "assignee_user_ids": [admin_id],
                "start_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "priority": "urgent",
                "status": "doing",
                "progress": 60,
                "reminder_days": [7, 3, 1, 0],
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        tasks = client.get("/api/plans/tasks").json()["items"]
        assert any(item["id"] == created.json()["id"] and item["department"] == ["电气部"] for item in tasks)
        reminders = client.get("/api/plans/reminders").json()["items"]
        assert any(item["task_id"] == created.json()["id"] and item["days_left"] == 0 for item in reminders)

        csv_data = (
            "任务编号,任务名称,部门,负责人账号,开始日期,截止日期,优先级,状态,进度,前置任务,父任务\n"
            "T1,布置赛道,车身部,admin,2026-09-01,2026-09-05,high,doing,20,,\n"
            "T2,整车验收,电气部,admin,2026-09-06,2026-09-07,urgent,todo,0,T1,T1\n"
        ).encode("utf-8")
        preview = client.post(
            "/api/plans/import",
            files={"file": ("plan.csv", csv_data, "text/csv")},
            data={"apply": "false"},
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["count"] == 2 and preview.json()["can_apply"] is True
        applied = client.post(
            "/api/plans/import",
            files={"file": ("plan.csv", csv_data, "text/csv")},
            data={"apply": "true"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["count"] == 2


def test_v240_component_inventory_movements_and_bom() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        created = client.post(
            "/api/inventory/components",
            json={
                "name": "高压继电器",
                "category": "高压元件",
                "manufacturer": "TE",
                "manufacturer_part_no": "EV200AAANA",
                "package": "法兰安装",
                "unit": "个",
                "minimum_quantity": 2,
                "unit_cost": 368.5,
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        component_id = created.json()["id"]
        stock_in = client.post(
            "/api/inventory/movements",
            json={"component_id": component_id, "movement_type": "in", "quantity": 10, "batch_no": "TEST"},
            headers=headers,
        )
        assert stock_in.status_code == 200, stock_in.text
        assert stock_in.json()["status"] == "applied"
        component = next(item for item in client.get("/api/inventory/components").json()["items"] if item["id"] == component_id)
        assert component["quantity"] == 10

        bom = "元件名称,制造商型号,封装,数量\n高压继电器,EV200AAANA,法兰安装,12\n".encode("utf-8")
        preview = client.post(
            "/api/inventory/bom-import",
            files={"file": ("bom.csv", bom, "text/csv")},
            data={"mode": "compare", "production_count": "1", "apply": "false"},
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        item = preview.json()["items"][0]
        assert item["component_id"] == component_id and item["shortage"] == 2


def test_v240_story_covers_and_new_public_history() -> None:
    with TestClient(app) as client:
        stories = client.get("/api/stories").json()["items"]
        profile = next(item for item in stories if item["id"] == "story_history_2013_xia_huaicheng_profile")
        assert "科学网" in profile["body"]
        official = next(item for item in stories if item["id"] == "story_history_2023_e09")
        cover = next(asset for asset in official["assets"] if asset["asset_role"] == "cover")
        assert cover["url"].endswith("story-2023-e09-48.png")
        fallback = client.get("/api/stories/fallback-cover/story_history_2013_xia_huaicheng_profile.svg")
        assert fallback.status_code == 200 and fallback.headers["content-type"].startswith("image/svg+xml")


def test_v240_snapshot_restores_plan_and_inventory() -> None:
    with TestClient(app) as client:
        _, headers = login(client, "admin", "YXRT@2026")
        task = client.post(
            "/api/plans/tasks",
            json={"title": "回溯验证任务", "status": "todo", "priority": "medium", "progress": 10},
            headers=headers,
        )
        component = client.post(
            "/api/inventory/components",
            json={"name": "回溯验证元件", "manufacturer_part_no": "ROLLBACK-240", "unit": "个"},
            headers=headers,
        )
        assert task.status_code == component.status_code == 200
        snapshot = client.post("/api/admin/snapshots", json={"label": "V2.4.0 模块回溯"}, headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        updated = client.put(
            f"/api/plans/tasks/{task.json()['id']}",
            json={"title": "回溯验证任务", "status": "doing", "priority": "high", "progress": 90},
            headers=headers,
        )
        moved = client.post(
            "/api/inventory/movements",
            json={"component_id": component.json()["id"], "movement_type": "in", "quantity": 8},
            headers=headers,
        )
        assert updated.status_code == moved.status_code == 200
        restored = client.post(f"/api/admin/snapshots/{snapshot.json()['id']}/restore", headers=headers)
        assert restored.status_code == 200, restored.text
        restored_task = next(item for item in client.get("/api/plans/tasks").json()["items"] if item["id"] == task.json()["id"])
        restored_component = next(item for item in client.get("/api/inventory/components").json()["items"] if item["id"] == component.json()["id"])
        assert restored_task["status"] == "todo" and restored_task["progress"] == 10
        assert restored_component["quantity"] == 0
