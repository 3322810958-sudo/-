from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import current_season_id, new_id, setting, utc_now


ISSUE_LABELS = {
    "low_confidence": "识别结果待复核",
    "import_failed": "导入失败",
    "ocr_failed": "离线识别失败",
    "export_failed": "PDF 导出失败",
    "conversion_failed": "附件转换失败",
    "attachment_missing": "本机附件缺失",
    "other": "其他问题",
}


def list_issues(conn: sqlite3.Connection, *, status: str = "open", limit: int = 300) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT q.*,i.invoice_no,i.vendor,i.total_amount_cents,a.original_name AS attachment_name
        FROM invoice_quality_issues q
        LEFT JOIN invoices i ON i.id=q.invoice_id
        LEFT JOIN attachments a ON a.id=i.attachment_id
        WHERE q.season_id=? AND q.status=?
        ORDER BY CASE q.severity WHEN 'error' THEN 0 ELSE 1 END,q.created_at DESC LIMIT ?""",
        (current_season_id(conn), status, max(1, min(int(limit), 1000))),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["label"] = ISSUE_LABELS.get(item["issue_type"], "问题")
        item["total_amount"] = round(int(item.pop("total_amount_cents") or 0) / 100, 2)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        items.append(item)
    return items


def record_issue(
    conn: sqlite3.Connection,
    issue_type: str,
    message: str,
    *,
    invoice_id: str | None = None,
    field_name: str = "",
    severity: str = "warning",
    details: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> str:
    now = utc_now()
    season_id = current_season_id(conn)
    existing = conn.execute(
        """SELECT id FROM invoice_quality_issues
        WHERE season_id=? AND invoice_id=? AND issue_type=?
        AND field_name=? AND status='open' ORDER BY created_at DESC LIMIT 1""",
        (season_id, invoice_id, issue_type, field_name),
    ).fetchone() if invoice_id else None
    payload = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    if existing:
        conn.execute(
            """UPDATE invoice_quality_issues SET message=?,severity=?,details_json=?,updated_at=? WHERE id=?""",
            (str(message)[:1000], severity, payload, now, existing["id"]),
        )
        return str(existing["id"])
    issue_id = new_id("issue")
    conn.execute(
        """INSERT INTO invoice_quality_issues(
        id,season_id,invoice_id,issue_type,severity,field_name,message,details_json,status,created_by,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?, 'open',?,?,?)""",
        (
            issue_id, season_id, invoice_id, issue_type, severity, field_name,
            str(message)[:1000], payload, user_id, now, now,
        ),
    )
    return issue_id


def resolve_issue(conn: sqlite3.Connection, issue_id: str) -> bool:
    return bool(conn.execute(
        """UPDATE invoice_quality_issues SET status='resolved',updated_at=?
        WHERE id=? AND season_id=? AND status='open'""",
        (utc_now(), issue_id, current_season_id(conn)),
    ).rowcount)


def resolve_invoice_issue_type(conn: sqlite3.Connection, invoice_id: str, issue_type: str) -> None:
    conn.execute(
        """UPDATE invoice_quality_issues SET status='resolved',updated_at=?
        WHERE invoice_id=? AND issue_type=? AND status='open'""",
        (utc_now(), invoice_id, issue_type),
    )


def sync_ocr_issues(
    conn: sqlite3.Connection,
    invoice_id: str,
    result: dict[str, Any],
    *,
    user_id: str | None = None,
) -> None:
    # A completed recognition supersedes earlier import/OCR failures.
    for issue_type in ("ocr_failed", "import_failed"):
        resolve_invoice_issue_type(conn, invoice_id, issue_type)
    try:
        threshold = max(0.1, min(0.99, float(setting(conn, "ocr_confidence_threshold", "0.80"))))
    except ValueError:
        threshold = 0.8
    confidence = float(result.get("ocr_confidence") or 0)
    uncertain = [str(value) for value in result.get("uncertain_fields", []) if str(value)]
    if confidence < threshold or uncertain:
        record_issue(
            conn,
            "low_confidence",
            f"离线识别置信度 {round(confidence * 100)}%，请复核高亮字段",
            invoice_id=invoice_id,
            severity="warning",
            details={
                "confidence": confidence,
                "threshold": threshold,
                "uncertain_fields": uncertain,
                "field_confidences": result.get("field_confidences") or {},
            },
            user_id=user_id,
        )
    else:
        resolve_invoice_issue_type(conn, invoice_id, "low_confidence")
