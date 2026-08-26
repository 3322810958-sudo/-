from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .classification import PRODUCT_TYPES
from .database import (
    audit,
    create_snapshot,
    current_season_id,
    enqueue_sync_event,
    get_device_id,
    new_id,
    row_dict,
    transaction,
    utc_now,
)


class BusinessError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def to_cents(value: Any) -> int:
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, ValueError):
        raise BusinessError("金额格式不正确") from None
    if amount < 0:
        raise BusinessError("金额不能为负数")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def yuan(cents: int | None) -> float:
    return round(int(cents or 0) / 100, 2)


def distribute_equal(total_cents: int, member_ids: list[str]) -> dict[str, int]:
    if not member_ids:
        raise BusinessError("请至少选择一名分摊成员")
    base, remainder = divmod(total_cents, len(member_ids))
    return {member_id: base + (1 if index < remainder else 0) for index, member_id in enumerate(member_ids)}


def distribute_weighted(total_cents: int, weights: dict[str, Any]) -> dict[str, int]:
    clean: list[tuple[str, Decimal]] = []
    for member_id, raw in weights.items():
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise BusinessError("分摊比例格式不正确") from None
        if value > 0:
            clean.append((member_id, value))
    if not clean:
        raise BusinessError("分摊比例必须大于 0")
    weight_total = sum((item[1] for item in clean), Decimal("0"))
    result: dict[str, int] = {}
    used = 0
    for index, (member_id, weight) in enumerate(clean):
        if index == len(clean) - 1:
            share = total_cents - used
        else:
            share = int((Decimal(total_cents) * weight / weight_total).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            used += share
        result[member_id] = share
    return result


def invoice_payload(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any] | None:
    season_id = current_season_id(conn)
    invoice = conn.execute(
        """SELECT i.*,c.name AS category_name,c.color AS category_color,
        f.name AS funding_source_name,f.color AS funding_source_color,
        m.name AS payer_name,a.original_name AS attachment_name,a.mime_type AS attachment_mime,
        a.size_bytes AS attachment_size,a.sha256 AS attachment_sha256,
        COALESCE(a.created_at,i.created_at) AS uploaded_at,
        COALESCE(creator.display_name,uploader.display_name,'系统任务') AS created_by_name,
        creator.username AS created_by_username
        FROM invoices i
        LEFT JOIN categories c ON c.id=i.category_id
        LEFT JOIN funding_sources f ON f.id=i.funding_source_id
        LEFT JOIN members m ON m.id=i.payer_member_id
        LEFT JOIN attachments a ON a.id=i.attachment_id
        LEFT JOIN users creator ON creator.id=i.created_by
        LEFT JOIN users uploader ON uploader.id=a.uploaded_by
        WHERE i.id=? AND i.season_id=?""",
        (invoice_id, season_id),
    ).fetchone()
    if not invoice:
        return None
    item = dict(invoice)
    splits = [dict(row) for row in conn.execute(
        """SELECT s.*,m.name AS member_name,m.avatar_color FROM invoice_splits s
        JOIN members m ON m.id=s.member_id WHERE s.invoice_id=? AND s.deleted_at IS NULL ORDER BY m.sort_order""",
        (invoice_id,),
    ).fetchall()]
    item["total_amount"] = yuan(item.pop("total_amount_cents"))
    item["tax_amount"] = yuan(item.pop("tax_amount_cents"))
    item["reimbursed_amount"] = yuan(item.pop("reimbursed_amount_cents"))
    for split in splits:
        split["share_amount"] = yuan(split.pop("share_cents"))
        split["paid_amount"] = yuan(split.pop("paid_cents"))
    item["splits"] = splits
    item["supporting_attachments"] = [dict(row) for row in conn.execute(
        """SELECT r.id AS relation_id,r.attachment_kind,r.label,r.sort_order,
        a.id AS attachment_id,a.original_name,a.mime_type,a.size_bytes,a.created_at
        FROM invoice_supporting_attachments r JOIN attachments a ON a.id=r.attachment_id
        WHERE r.invoice_id=? AND a.deleted_at IS NULL ORDER BY r.sort_order,r.created_at""",
        (invoice_id,),
    ).fetchall()]
    return item


def list_invoices(
    conn: sqlite3.Connection,
    *,
    search: str = "",
    status: str = "",
    category_id: str = "",
    source_id: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses = ["i.deleted_at IS NULL", "i.season_id=?"]
    params: list[Any] = [current_season_id(conn)]
    if search:
        pattern = f"%{search.strip()}%"
        clauses.append("(i.vendor LIKE ? OR i.invoice_no LIKE ? OR i.note LIKE ? OR i.product_type LIKE ?)")
        params.extend([pattern] * 4)
    if status:
        clauses.append("i.reimbursement_status=?")
        params.append(status)
    if category_id:
        clauses.append("i.category_id=?")
        params.append(category_id)
    if source_id:
        clauses.append("i.funding_source_id=?")
        params.append(source_id)
    if date_from:
        clauses.append("i.invoice_date>=?")
        params.append(date_from)
    if date_to:
        clauses.append("i.invoice_date<=?")
        params.append(date_to)
    rows = conn.execute(
        f"SELECT i.id FROM invoices i WHERE {' AND '.join(clauses)} ORDER BY i.invoice_date DESC,i.created_at DESC LIMIT ?",
        (*params, min(max(int(limit), 1), 100000)),
    ).fetchall()
    return [item for row in rows if (item := invoice_payload(conn, row["id"]))]


def _active_member_ids(conn: sqlite3.Connection) -> list[str]:
    season_id = current_season_id(conn)
    return [row[0] for row in conn.execute(
        "SELECT id FROM members WHERE season_id=? AND active=1 AND deleted_at IS NULL ORDER BY sort_order,name",
        (season_id,),
    ).fetchall()]


def _validated_splits(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    total_cents: int,
    payer_member_id: str,
    burden_type: str,
) -> dict[str, int]:
    active_ids = _active_member_ids(conn)
    active_set = set(active_ids)
    requested = [str(value) for value in payload.get("split_member_ids", []) if str(value) in active_set]
    if burden_type == "self_paid":
        requested = [payer_member_id]
    elif burden_type == "team_aa":
        requested = active_ids
    if not requested:
        raise BusinessError("请至少选择一名有效分摊成员")
    requested = list(dict.fromkeys(requested))

    split_mode = str(payload.get("split_mode", "equal"))
    if split_mode == "custom":
        custom = payload.get("custom_splits") or {}
        result = {member_id: to_cents(custom.get(member_id, 0)) for member_id in requested}
        if sum(result.values()) != total_cents:
            raise BusinessError("自定义分摊金额合计必须与发票总额完全一致")
        return result
    if split_mode == "weighted":
        weights = payload.get("split_weights") or {}
        return distribute_weighted(total_cents, {member_id: weights.get(member_id, 0) for member_id in requested})
    return distribute_equal(total_cents, requested)


def save_invoice(payload: dict[str, Any], user: dict[str, Any], invoice_id: str | None = None) -> dict[str, Any]:
    total_cents = to_cents(payload.get("total_amount"))
    if total_cents <= 0:
        raise BusinessError("发票金额必须大于 0")
    tax_cents = to_cents(payload.get("tax_amount", 0))
    reimbursed_cents = to_cents(payload.get("reimbursed_amount", 0))
    if reimbursed_cents > total_cents:
        raise BusinessError("已报销金额不能超过发票总额")
    invoice_date = str(payload.get("invoice_date") or "")[:10]
    if len(invoice_date) != 10:
        raise BusinessError("请选择开票日期")

    burden_type = str(payload.get("burden_type") or "team_aa")
    if burden_type not in {"team_aa", "self_paid", "specified_split"}:
        raise BusinessError("费用承担方式不正确")
    status = "pending" if reimbursed_cents == 0 else ("reimbursed" if reimbursed_cents == total_cents else "partial")
    now = utc_now()

    with transaction() as conn:
        season_id = current_season_id(conn)
        payer_id = str(payload.get("payer_member_id") or "")
        payer = conn.execute(
            "SELECT id FROM members WHERE id=? AND season_id=? AND active=1 AND deleted_at IS NULL", (payer_id, season_id)
        ).fetchone()
        if not payer:
            raise BusinessError("请选择有效垫付成员")
        splits = _validated_splits(conn, payload, total_cents, payer_id, burden_type)
        category_id = str(payload.get("category_id") or "") or None
        source_id = str(payload.get("funding_source_id") or "") or None
        attachment_id = str(payload.get("attachment_id") or "") or None
        for table, value, label in (("categories", category_id, "分类"), ("funding_sources", source_id, "资金来源")):
            if value and not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND deleted_at IS NULL", (value,)).fetchone():
                raise BusinessError(f"所选{label}不存在")
        if attachment_id and not conn.execute(
            "SELECT 1 FROM attachments WHERE id=? AND season_id=? AND deleted_at IS NULL",
            (attachment_id, season_id),
        ).fetchone():
            raise BusinessError("所选附件不存在或不属于当前赛季")

        create_snapshot(conn, user["id"], "发票编辑前", f"{user['display_name']} 编辑经费记录")
        if invoice_id:
            current = conn.execute(
                "SELECT * FROM invoices WHERE id=? AND season_id=? AND deleted_at IS NULL", (invoice_id, season_id)
            ).fetchone()
            if not current:
                raise BusinessError("未找到该发票记录", 404)
            expected_version = int(payload["version"]) if "version" in payload else int(current["version"])
            if expected_version != int(current["version"]):
                raise BusinessError("记录已被其他成员修改，请刷新后重试", 409)
            version = int(current["version"]) + 1
            created_at = current["created_at"]
            created_by = current["created_by"]
            action = "update"
        else:
            invoice_id = new_id("invoice")
            version = 1
            created_at = now
            created_by = user["id"]
            action = "create"

        device_id = get_device_id(conn)
        values = {
            "id": invoice_id, "season_id": season_id,
            "invoice_no": str(payload.get("invoice_no") or "").strip()[:80],
            "vendor": str(payload.get("vendor") or "").strip()[:160],
            "invoice_date": invoice_date,
            "total_amount_cents": total_cents,
            "tax_amount_cents": tax_cents,
            "category_id": category_id,
            "product_type": str(payload.get("product_type") or "其他").strip()[:80] or "其他",
            "payer_member_id": payer_id,
            "burden_type": burden_type,
            "reimbursement_status": status,
            "reimbursed_amount_cents": reimbursed_cents,
            "reimbursement_date": str(payload.get("reimbursement_date") or "")[:10] or None,
            "funding_source_id": source_id,
            "note": str(payload.get("note") or "").strip()[:2000],
            "attachment_id": attachment_id,
            "ocr_text": str(payload.get("ocr_text") or "")[:30000],
            "ocr_confidence": max(0.0, min(1.0, float(payload.get("ocr_confidence") or 0))),
            "ocr_status": str(payload.get("ocr_status") or "manual")[:30],
            "is_demo": int(bool(payload.get("is_demo", False))),
            "created_by": created_by,
            "created_at": created_at,
            "updated_at": now,
            "version": version,
            "device_id": device_id,
            "deleted_at": None,
        }
        columns = list(values)
        conn.execute(
            f"INSERT OR REPLACE INTO invoices({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(values[column] for column in columns),
        )
        enqueue_sync_event(conn, "invoices", invoice_id, "upsert", values)

        previous_splits = [dict(row) for row in conn.execute("SELECT * FROM invoice_splits WHERE invoice_id=?", (invoice_id,)).fetchall()]
        previous_by_member = {item["member_id"]: item for item in previous_splits}
        desired_ids: set[str] = set()
        for member_id, share_cents in splits.items():
            desired_ids.add(member_id)
            existing = previous_by_member.get(member_id)
            split_id = existing["id"] if existing else new_id("split")
            paid_cents = min(int(existing["paid_cents"]), share_cents) if existing else 0
            split_values = {
                "id": split_id, "season_id": season_id, "invoice_id": invoice_id,
                "member_id": member_id, "share_cents": share_cents,
                "paid_cents": paid_cents,
                "status": "paid" if paid_cents >= share_cents else ("partial" if paid_cents else "pending"),
                "created_at": existing["created_at"] if existing else now, "updated_at": now,
                "version": int(existing["version"]) + 1 if existing else 1, "device_id": device_id, "deleted_at": None,
            }
            split_columns = list(split_values)
            conn.execute(
                f"INSERT OR REPLACE INTO invoice_splits({','.join(split_columns)}) VALUES({','.join('?' for _ in split_columns)})",
                tuple(split_values[column] for column in split_columns),
            )
            enqueue_sync_event(conn, "invoice_splits", split_id, "upsert", split_values)

        for old in previous_splits:
            if old["member_id"] not in desired_ids and not old.get("deleted_at"):
                old["deleted_at"] = now
                old["updated_at"] = now
                old["version"] = int(old["version"]) + 1
                old["device_id"] = device_id
                conn.execute(
                    "UPDATE invoice_splits SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
                    (now, now, old["version"], device_id, old["id"]),
                )
                enqueue_sync_event(conn, "invoice_splits", old["id"], "delete", old)
        audit(conn, user["id"], action, "invoice", invoice_id, {"amount": yuan(total_cents), "vendor": values["vendor"]})
        return invoice_payload(conn, invoice_id) or {}


def delete_invoice(invoice_id: str, user: dict[str, Any]) -> None:
    with transaction() as conn:
        season_id = current_season_id(conn)
        current = conn.execute(
            "SELECT * FROM invoices WHERE id=? AND season_id=? AND deleted_at IS NULL", (invoice_id, season_id)
        ).fetchone()
        if not current:
            raise BusinessError("未找到该发票记录", 404)
        create_snapshot(conn, user["id"], "删除发票前", str(current["vendor"] or current["invoice_no"] or invoice_id))
        now = utc_now()
        device_id = get_device_id(conn)
        conn.execute(
            "UPDATE invoices SET deleted_at=?,updated_at=?,version=version+1,device_id=? WHERE id=?",
            (now, now, device_id, invoice_id),
        )
        invoice = row_dict(conn, "invoices", invoice_id)
        enqueue_sync_event(conn, "invoices", invoice_id, "delete", invoice or {})
        for row in conn.execute("SELECT * FROM invoice_splits WHERE invoice_id=? AND deleted_at IS NULL", (invoice_id,)).fetchall():
            split = dict(row)
            split.update({"deleted_at": now, "updated_at": now, "version": int(split["version"]) + 1, "device_id": device_id})
            conn.execute(
                "UPDATE invoice_splits SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
                (now, now, split["version"], device_id, split["id"]),
            )
            enqueue_sync_event(conn, "invoice_splits", split["id"], "delete", split)
        audit(conn, user["id"], "delete", "invoice", invoice_id, {"vendor": current["vendor"]})


def batch_update_invoices(payload: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    raw_ids = payload.get("ids") or []
    if not isinstance(raw_ids, list):
        raise BusinessError("批量操作记录格式不正确")
    invoice_ids = list(dict.fromkeys(str(value) for value in raw_ids if str(value).strip()))
    if not invoice_ids:
        raise BusinessError("请至少选择一张发票")
    if len(invoice_ids) > 10000:
        raise BusinessError("单次最多处理 10000 张发票")

    action = str(payload.get("action") or "")
    if action not in {"delete", "category", "status"}:
        raise BusinessError("不支持该批量操作")

    placeholders = ",".join("?" for _ in invoice_ids)
    with transaction() as conn:
        season_id = current_season_id(conn)
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM invoices WHERE id IN ({placeholders}) AND season_id=? AND deleted_at IS NULL",
            (*invoice_ids, season_id),
        ).fetchall()]
        if not rows:
            raise BusinessError("所选发票不存在或已删除", 404)

        now = utc_now()
        device_id = get_device_id(conn)
        skipped = 0
        category_id: str | None = None
        target_status = ""
        ratio = 50
        if action == "category":
            category_id = str(payload.get("category_id") or "") or None
            if category_id and not conn.execute(
                "SELECT 1 FROM categories WHERE id=? AND deleted_at IS NULL", (category_id,)
            ).fetchone():
                raise BusinessError("所选费用分类不存在")
        elif action == "status":
            target_status = str(payload.get("status") or "")
            if target_status not in {"pending", "partial", "reimbursed"}:
                raise BusinessError("请选择正确的报销状态")
            try:
                ratio = max(1, min(99, int(payload.get("reimbursement_ratio", 50))))
            except (TypeError, ValueError):
                raise BusinessError("部分报销比例格式不正确") from None

        labels = {"delete": "批量删除发票前", "category": "批量修改分类前", "status": "批量修改报销状态前"}
        create_snapshot(conn, user["id"], labels[action], f"共选择 {len(rows)} 张发票")
        changed = 0
        total_cents = 0
        for row in rows:
            total_cents += int(row["total_amount_cents"] or 0)
            row["updated_at"] = now
            row["version"] = int(row["version"]) + 1
            row["device_id"] = device_id
            sync_action = "upsert"
            if action == "delete":
                row["deleted_at"] = now
                sync_action = "delete"
            elif action == "category":
                row["category_id"] = category_id
            else:
                total = int(row["total_amount_cents"] or 0)
                if target_status == "partial" and total <= 1:
                    skipped += 1
                    continue
                if target_status == "pending":
                    row["reimbursed_amount_cents"] = 0
                    row["reimbursement_date"] = None
                elif target_status == "reimbursed":
                    row["reimbursed_amount_cents"] = total
                    row["reimbursement_date"] = str(payload.get("reimbursement_date") or now[:10])[:10]
                else:
                    row["reimbursed_amount_cents"] = max(1, min(total - 1, int(round(total * ratio / 100))))
                    row["reimbursement_date"] = str(payload.get("reimbursement_date") or now[:10])[:10]
                row["reimbursement_status"] = target_status

            columns = [key for key in row if key != "id"]
            conn.execute(
                f"UPDATE invoices SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
                tuple(row[key] for key in columns) + (row["id"],),
            )
            enqueue_sync_event(conn, "invoices", row["id"], sync_action, row)
            if action == "delete":
                for split_row in conn.execute(
                    "SELECT * FROM invoice_splits WHERE invoice_id=? AND deleted_at IS NULL", (row["id"],)
                ).fetchall():
                    split = dict(split_row)
                    split.update({"deleted_at": now, "updated_at": now, "version": int(split["version"]) + 1, "device_id": device_id})
                    conn.execute(
                        "UPDATE invoice_splits SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
                        (now, now, split["version"], device_id, split["id"]),
                    )
                    enqueue_sync_event(conn, "invoice_splits", split["id"], "delete", split)
            changed += 1

        audit(conn, user["id"], f"batch_{action}", "invoice", None, {
            "requested": len(invoice_ids), "changed": changed, "skipped": skipped,
            "amount": yuan(total_cents), "status": target_status, "category_id": category_id,
        })
        return {"ok": True, "changed_count": changed, "skipped_count": skipped, "total_amount": yuan(total_cents)}


def dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    season_id = current_season_id(conn)
    totals = conn.execute(
        """SELECT COUNT(*) AS invoice_count,COALESCE(SUM(total_amount_cents),0) AS total,
        COALESCE(SUM(total_amount_cents-reimbursed_amount_cents),0) AS pending,
        COALESCE(SUM(reimbursed_amount_cents),0) AS reimbursed
        FROM invoices WHERE season_id=? AND deleted_at IS NULL""",
        (season_id,),
    ).fetchone()
    categories = [dict(row) for row in conn.execute(
        """SELECT COALESCE(c.name,'未分类') AS name,COALESCE(c.color,'#9aa7b7') AS color,
        COUNT(i.id) AS count,COALESCE(SUM(i.total_amount_cents),0) AS amount_cents
        FROM invoices i LEFT JOIN categories c ON c.id=i.category_id
        WHERE i.season_id=? AND i.deleted_at IS NULL GROUP BY i.category_id,c.name,c.color ORDER BY amount_cents DESC""",
        (season_id,),
    ).fetchall()]
    sources = [dict(row) for row in conn.execute(
        """SELECT COALESCE(f.name,'未选择') AS name,COALESCE(f.color,'#9aa7b7') AS color,
        COUNT(i.id) AS count,COALESCE(SUM(i.total_amount_cents),0) AS amount_cents
        FROM invoices i LEFT JOIN funding_sources f ON f.id=i.funding_source_id
        WHERE i.season_id=? AND i.deleted_at IS NULL GROUP BY i.funding_source_id,f.name,f.color ORDER BY amount_cents DESC""",
        (season_id,),
    ).fetchall()]
    monthly = [dict(row) for row in conn.execute(
        """SELECT substr(invoice_date,1,7) AS month,COALESCE(SUM(total_amount_cents),0) AS amount_cents
        FROM invoices WHERE season_id=? AND deleted_at IS NULL GROUP BY substr(invoice_date,1,7) ORDER BY month DESC LIMIT 12""",
        (season_id,),
    ).fetchall()][::-1]
    recent = list_invoices(conn, limit=6)
    for group in (categories, sources, monthly):
        for item in group:
            item["amount"] = yuan(item.pop("amount_cents"))
    settlement = settlement_summary(conn)
    return {
        "invoice_count": int(totals["invoice_count"]),
        "total_amount": yuan(totals["total"]),
        "pending_amount": yuan(totals["pending"]),
        "reimbursed_amount": yuan(totals["reimbursed"]),
        "aa_outstanding": settlement["outstanding_amount"],
        "categories": categories,
        "sources": sources,
        "monthly": monthly,
        "recent": recent,
    }


def settlement_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    season_id = current_season_id(conn)
    members = [dict(row) for row in conn.execute(
        """SELECT id,name,department,avatar_color FROM members
        WHERE season_id=? AND active=1 AND deleted_at IS NULL ORDER BY sort_order,name""",
        (season_id,),
    ).fetchall()]
    balances = {member["id"]: 0 for member in members}
    paid_out = {member["id"]: 0 for member in members}
    owed = {member["id"]: 0 for member in members}
    invoices = conn.execute(
        """SELECT id,payer_member_id,total_amount_cents,reimbursed_amount_cents FROM invoices
        WHERE season_id=? AND deleted_at IS NULL AND burden_type IN ('team_aa','specified_split')
        AND total_amount_cents>reimbursed_amount_cents""",
        (season_id,),
    ).fetchall()
    for invoice in invoices:
        total = int(invoice["total_amount_cents"])
        remaining = total - int(invoice["reimbursed_amount_cents"])
        payer_id = invoice["payer_member_id"]
        if payer_id in balances:
            balances[payer_id] += remaining
            paid_out[payer_id] += remaining
        splits = conn.execute(
            "SELECT member_id,share_cents FROM invoice_splits WHERE invoice_id=? AND deleted_at IS NULL",
            (invoice["id"],),
        ).fetchall()
        allocated = 0
        for index, split in enumerate(splits):
            if index == len(splits) - 1:
                share = remaining - allocated
            else:
                share = int((int(split["share_cents"]) * remaining + total // 2) // total)
                allocated += share
            member_id = split["member_id"]
            if member_id in balances:
                balances[member_id] -= share
                owed[member_id] += share

    for transfer in conn.execute(
        """SELECT from_member_id,to_member_id,amount_cents FROM settlements
        WHERE season_id=? AND status='paid' AND deleted_at IS NULL""",
        (season_id,),
    ).fetchall():
        amount = int(transfer["amount_cents"])
        if transfer["from_member_id"] in balances:
            balances[transfer["from_member_id"]] += amount
        if transfer["to_member_id"] in balances:
            balances[transfer["to_member_id"]] -= amount

    creditors = [[member_id, amount] for member_id, amount in balances.items() if amount > 0]
    debtors = [[member_id, -amount] for member_id, amount in balances.items() if amount < 0]
    creditors.sort(key=lambda item: item[1], reverse=True)
    debtors.sort(key=lambda item: item[1], reverse=True)
    recommendations: list[dict[str, Any]] = []
    creditor_index = 0
    debtor_index = 0
    while creditor_index < len(creditors) and debtor_index < len(debtors):
        creditor_id, credit = creditors[creditor_index]
        debtor_id, debt = debtors[debtor_index]
        amount = min(credit, debt)
        if amount > 0:
            recommendations.append({"from_member_id": debtor_id, "to_member_id": creditor_id, "amount": yuan(amount)})
        creditors[creditor_index][1] -= amount
        debtors[debtor_index][1] -= amount
        if creditors[creditor_index][1] == 0:
            creditor_index += 1
        if debtors[debtor_index][1] == 0:
            debtor_index += 1

    name_map = {member["id"]: member["name"] for member in members}
    rows = []
    for member in members:
        member_id = member["id"]
        rows.append({
            **member,
            "paid_out": yuan(paid_out[member_id]),
            "owed": yuan(owed[member_id]),
            "balance": yuan(balances[member_id]),
        })
    for item in recommendations:
        item["from_name"] = name_map.get(item["from_member_id"], "未知成员")
        item["to_name"] = name_map.get(item["to_member_id"], "未知成员")
    return {
        "members": rows,
        "recommendations": recommendations,
        "outstanding_amount": yuan(sum(max(0, -value) for value in balances.values())),
    }


def record_settlement(payload: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    from_id = str(payload.get("from_member_id") or "")
    to_id = str(payload.get("to_member_id") or "")
    amount_cents = to_cents(payload.get("amount"))
    if amount_cents <= 0:
        raise BusinessError("结算金额必须大于 0")
    if from_id == to_id:
        raise BusinessError("付款人与收款人不能相同")
    with transaction() as conn:
        season_id = current_season_id(conn)
        valid = {row[0] for row in conn.execute(
            "SELECT id FROM members WHERE season_id=? AND active=1 AND deleted_at IS NULL", (season_id,)
        )}
        if from_id not in valid or to_id not in valid:
            raise BusinessError("请选择有效成员")
        create_snapshot(conn, user["id"], "登记结算前", "成员AA结算")
        now = utc_now()
        entity_id = new_id("settlement")
        row = {
            "id": entity_id, "season_id": season_id, "from_member_id": from_id, "to_member_id": to_id,
            "amount_cents": amount_cents, "status": str(payload.get("status") or "paid"),
            "settled_at": str(payload.get("settled_at") or now[:10]), "note": str(payload.get("note") or "")[:500],
            "is_demo": 0, "created_by": user["id"], "created_at": now, "updated_at": now,
            "version": 1, "device_id": get_device_id(conn), "deleted_at": None,
        }
        columns = list(row)
        conn.execute(
            f"INSERT INTO settlements({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        enqueue_sync_event(conn, "settlements", entity_id, "upsert", row)
        audit(conn, user["id"], "create", "settlement", entity_id, {"amount": yuan(amount_cents)})
        return {**row, "amount": yuan(row.pop("amount_cents"))}


def delete_demo_data(user: dict[str, Any]) -> int:
    with transaction() as conn:
        season_id = current_season_id(conn)
        create_snapshot(conn, user["id"], "清除演示数据前", "管理员清理演示记录")
        invoice_rows = conn.execute(
            "SELECT * FROM invoices WHERE season_id=? AND is_demo=1 AND deleted_at IS NULL", (season_id,)
        ).fetchall()
        settlement_rows = conn.execute(
            "SELECT * FROM settlements WHERE season_id=? AND is_demo=1 AND deleted_at IS NULL", (season_id,)
        ).fetchall()
        now = utc_now()
        device_id = get_device_id(conn)
        count = 0
        for row in list(invoice_rows) + list(settlement_rows):
            table = "invoices" if "invoice_no" in row.keys() else "settlements"
            payload = dict(row)
            payload.update({"deleted_at": now, "updated_at": now, "version": int(payload["version"]) + 1, "device_id": device_id})
            conn.execute(
                f"UPDATE {table} SET deleted_at=?,updated_at=?,version=?,device_id=? WHERE id=?",
                (now, now, payload["version"], device_id, payload["id"]),
            )
            enqueue_sync_event(conn, table, payload["id"], "delete", payload)
            count += 1
        audit(conn, user["id"], "delete_demo", "system", None, {"count": count})
        return count
