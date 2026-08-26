from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Any

from .attachments import attachment_path
from .business import PRODUCT_TYPES, to_cents
from .classification import classify_invoice, detect_product_type as smart_detect_product_type
from .config import MODEL_DIR, OCR_CPU_THREADS, OCR_DETECTION_MAX_SIDE, OCR_WORKERS
from .database import audit, connect, current_season_id, enqueue_sync_event, get_device_id, new_id, transaction, utc_now
from .quality import record_issue, sync_ocr_issues


OCR_EXECUTOR = ThreadPoolExecutor(max_workers=OCR_WORKERS, thread_name_prefix="yxrt-ocr")
OCR_INSTANCE: Any = None
OCR_LOCK = threading.Lock()


def normalize_text(text: str) -> str:
    return str(text or "").replace("\r", "\n").replace("\u3000", " ")


def _first_match(patterns: list[str], text: str, flags: int = 0) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return str(match.group(1)).strip()
    return ""


def _money_value(raw: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", raw.replace(",", ""))
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return 0.0


def detect_product_type(text: str) -> str:
    return smart_detect_product_type(text)


def parse_invoice_text(text: str, confidence: float = 0.0) -> dict[str, Any]:
    raw = normalize_text(text)
    compact = re.sub(r"[ \t]+", " ", raw)
    single_line = re.sub(r"\s+", " ", compact)

    date_raw = _first_match([
        r"(?:开票日期|开具日期|日期)\s*[:：]?\s*((?:19|20)\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
        r"((?:19|20)\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
        r"((?:19|20)\d{6})",
    ], single_line)
    invoice_date = ""
    digits = re.findall(r"\d+", date_raw)
    if len(digits) >= 3:
        invoice_date = f"{int(digits[0]):04d}-{int(digits[1]):02d}-{int(digits[2]):02d}"
    elif len(digits) == 1 and len(digits[0]) == 8:
        value = digits[0]
        invoice_date = f"{value[:4]}-{value[4:6]}-{value[6:]}"

    amount_candidates: list[float] = []
    for pattern in [
        r"价税合计(?:\s*\(小写\))?\s*[:：]?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
        r"小写\s*[:：]?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
        r"合计金额\s*[:：]?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
        r"(?:应付|实付|支付)金额\s*[:：]?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
    ]:
        value = _first_match([pattern], single_line, re.IGNORECASE)
        if value:
            amount_candidates.append(_money_value(value))
    if not amount_candidates:
        amount_candidates = [_money_value(value) for value in re.findall(r"[¥￥]\s*([0-9,]+(?:\.\d{1,2})?)", single_line)]
    total_amount = max(amount_candidates, default=0.0)

    tax_raw = _first_match([
        r"税额合计\s*[:：]?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
        r"合计\s*[¥￥]?\s*[0-9,]+(?:\.\d{1,2})?\s*[¥￥]?\s*([0-9,]+(?:\.\d{1,2})?)",
    ], single_line)
    invoice_no = _first_match([
        r"(?:发票号码|发票号|票据号码)\s*[:：]?\s*([0-9A-Z]{6,30})",
        r"No\.?\s*[:：]?\s*([0-9A-Z]{6,30})",
    ], single_line, re.IGNORECASE)
    if not invoice_no:
        standalone_numbers = re.findall(r"(?<![0-9A-Z])([0-9]{20})(?![0-9A-Z])", single_line)
        invoice_no = standalone_numbers[0] if standalone_numbers else ""
    vendor = _first_match([
        r"销售方信息\s*名称\s*[:：]\s*([^\n]{2,60})",
        r"(?:销售方名称|销方名称|开票方|收款单位)\s*[:：]?\s*([^\n]{2,60})",
        r"名称\s*[:：]\s*([^\n]{2,60})\s+(?:纳税人识别号|统一社会信用代码)",
    ], compact)
    vendor = re.split(r"(?:纳税人|统一社会|地址|电话|开户行)", vendor)[0].strip(" ：:")

    confidence_value = float(confidence or 0)
    if total_amount and invoice_date:
        confidence_value = max(confidence_value, 0.78)
    elif total_amount or invoice_date:
        confidence_value = max(confidence_value, 0.55)
    bounded_confidence = round(min(1.0, confidence_value), 4)
    field_confidences = {
        "invoice_no": round(max(bounded_confidence, 0.86) if invoice_no else 0.0, 4),
        "vendor": round(max(bounded_confidence, 0.80) if vendor else 0.0, 4),
        "invoice_date": round(max(bounded_confidence, 0.90) if invoice_date else 0.0, 4),
        "total_amount": round(max(bounded_confidence, 0.90) if total_amount > 0 else 0.0, 4),
        "tax_amount": round(max(bounded_confidence, 0.72) if tax_raw else 0.0, 4),
    }
    uncertain_fields = [
        key for key in ("invoice_no", "vendor", "invoice_date", "total_amount")
        if field_confidences[key] < 0.75
    ]
    return {
        "invoice_no": invoice_no,
        "vendor": vendor,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
        "tax_amount": _money_value(tax_raw),
        "product_type": detect_product_type(single_line),
        "ocr_text": raw[:30000],
        "ocr_confidence": bounded_confidence,
        "ocr_status": "recognized" if raw.strip() else "no_text",
        "field_confidences": field_confidences,
        "uncertain_fields": uncertain_fields,
    }


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _runtime_path_argument(path: Path) -> str:
    """Prefer an ASCII relative path for Paddle's Windows native runtime."""
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
        if str(relative).isascii():
            return str(relative)
    except ValueError:
        pass
    return str(path)


def _get_ocr() -> Any:
    global OCR_INSTANCE
    if OCR_INSTANCE is not None:
        return OCR_INSTANCE
    with OCR_LOCK:
        if OCR_INSTANCE is not None:
            return OCR_INSTANCE
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODEL_DIR / "paddlex"))
        # Paddle 3.3.1 + Windows can fail on a oneDNN/PIR conversion path for
        # PP-OCRv5. The standard CPU runtime is stable and still fully offline.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        from paddleocr import PaddleOCR
        det_dir = MODEL_DIR / "paddlex" / "official_models" / "PP-OCRv5_mobile_det"
        rec_dir = MODEL_DIR / "paddlex" / "official_models" / "PP-OCRv5_mobile_rec"

        OCR_INSTANCE = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            text_detection_model_dir=_runtime_path_argument(det_dir) if (det_dir / "inference.json").exists() else None,
            text_recognition_model_dir=_runtime_path_argument(rec_dir) if (rec_dir / "inference.json").exists() else None,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            cpu_threads=OCR_CPU_THREADS,
        )
        return OCR_INSTANCE


def _result_data(result: Any) -> dict[str, Any]:
    data = getattr(result, "json", None)
    if callable(data):
        data = data()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    if isinstance(data, dict) and isinstance(data.get("res"), dict):
        data = data["res"]
    return data if isinstance(data, dict) else {}


def _paddle_text(path: Path) -> tuple[str, float]:
    ocr = _get_ocr()
    texts: list[str] = []
    scores: list[float] = []
    if hasattr(ocr, "predict"):
        for result in ocr.predict(
            _runtime_path_argument(path),
            text_det_limit_side_len=OCR_DETECTION_MAX_SIDE,
            text_det_limit_type="max",
        ):
            data = _result_data(result)
            page_texts = data.get("rec_texts") or data.get("texts") or []
            page_scores = data.get("rec_scores") or data.get("scores") or []
            texts.extend(str(item) for item in page_texts if str(item).strip())
            scores.extend(float(item) for item in page_scores if item is not None)
    else:
        legacy = ocr.ocr(str(path), cls=False)
        for page in legacy or []:
            for line in page or []:
                if len(line) > 1 and line[1]:
                    texts.append(str(line[1][0]))
                    scores.append(float(line[1][1]))
    return "\n".join(texts), mean(scores) if scores else 0.0


def recognize_attachment(attachment: dict[str, Any]) -> dict[str, Any]:
    path = attachment_path(attachment["stored_name"])
    if not path.exists():
        raise FileNotFoundError("附件文件不存在，可能尚未完成云端同步")
    extension = path.suffix.lower()
    text = ""
    confidence = 0.0
    engine = "paddleocr"
    if extension == ".txt":
        text = path.read_text("utf-8", errors="ignore")
        confidence = 1.0
        engine = "text"
    elif extension == ".pdf":
        text = _extract_pdf_text(path)
        if len(re.sub(r"\s+", "", text)) >= 30:
            confidence = 0.96
            engine = "pdf_text"
        else:
            text, confidence = _paddle_text(path)
    elif extension == ".ofd":
        raise RuntimeError("OFD 文件已保存；当前离线 OCR 请先转换为 PDF 或图片")
    else:
        text, confidence = _paddle_text(path)
    result = parse_invoice_text(text, confidence)
    result["ocr_engine"] = engine
    return result


def create_ocr_job(attachment_id: str, user_id: str, invoice_id: str | None = None) -> str:
    job_id = new_id("ocrjob")
    now = utc_now()
    with transaction() as conn:
        if not conn.execute(
            "SELECT 1 FROM attachments WHERE id=? AND season_id=? AND deleted_at IS NULL",
            (attachment_id, current_season_id(conn)),
        ).fetchone():
            raise ValueError("附件不存在")
        conn.execute(
            "INSERT INTO ocr_jobs(id,attachment_id,invoice_id,status,result_json,error,created_by,created_at,updated_at) VALUES(?,?,?,?,'{}','',?,?,?)",
            (job_id, attachment_id, invoice_id, "queued", user_id, now, now),
        )
    OCR_EXECUTOR.submit(_run_job, job_id)
    return job_id


def warmup_ocr() -> None:
    """Load the local mobile OCR models in the background before first use."""
    OCR_EXECUTOR.submit(_get_ocr)


def _run_job(job_id: str) -> None:
    job = None
    try:
        with transaction() as conn:
            job = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return
            conn.execute("UPDATE ocr_jobs SET status='processing',updated_at=? WHERE id=?", (utc_now(), job_id))
            attachment = conn.execute("SELECT * FROM attachments WHERE id=?", (job["attachment_id"],)).fetchone()
            cached = conn.execute(
                """SELECT result_json FROM ocr_jobs
                WHERE attachment_id=? AND id<>? AND status='done' AND result_json<>'{}'
                ORDER BY updated_at DESC LIMIT 1""",
                (job["attachment_id"], job_id),
            ).fetchone()
        if not attachment:
            raise FileNotFoundError("附件记录不存在")
        if cached:
            try:
                result = json.loads(cached["result_json"] or "{}")
            except json.JSONDecodeError:
                result = recognize_attachment(dict(attachment))
            result["ocr_engine"] = f"{result.get('ocr_engine') or 'paddleocr'}_cache"
        else:
            result = recognize_attachment(dict(attachment))
        with transaction() as conn:
            result.update(classify_invoice(
                conn,
                str(result.get("ocr_text") or ""),
                vendor=str(result.get("vendor") or ""),
                detected_product_type=str(result.get("product_type") or "其他"),
            ))
            conn.execute(
                "UPDATE ocr_jobs SET status='done',result_json=?,error='',updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), utc_now(), job_id),
            )
            if job["invoice_id"]:
                invoice_row = conn.execute("SELECT * FROM invoices WHERE id=? AND deleted_at IS NULL", (job["invoice_id"],)).fetchone()
                if invoice_row:
                    invoice = dict(invoice_row)
                    now = utc_now()
                    amount_cents = to_cents(result.get("total_amount", 0))
                    if amount_cents > 0:
                        invoice["total_amount_cents"] = amount_cents
                    if result.get("invoice_date"):
                        invoice["invoice_date"] = result["invoice_date"]
                    for key in ("invoice_no", "vendor", "product_type"):
                        if result.get(key):
                            invoice[key] = result[key]
                    if not invoice.get("category_id") and result.get("category_id"):
                        invoice["category_id"] = result["category_id"]
                    invoice.update({
                        "tax_amount_cents": to_cents(result.get("tax_amount", 0)),
                        "ocr_text": result.get("ocr_text", "")[:30000],
                        "ocr_confidence": float(result.get("ocr_confidence") or 0),
                        "ocr_status": "recognized", "updated_at": now,
                        "version": int(invoice["version"]) + 1, "device_id": get_device_id(conn),
                    })
                    columns = [key for key in invoice if key != "id"]
                    conn.execute(f"UPDATE invoices SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
                                 tuple(invoice[key] for key in columns) + (invoice["id"],))
                    enqueue_sync_event(conn, "invoices", invoice["id"], "upsert", invoice)
                    splits = [dict(row) for row in conn.execute(
                        "SELECT * FROM invoice_splits WHERE invoice_id=? AND deleted_at IS NULL ORDER BY id", (invoice["id"],)
                    ).fetchall()]
                    if splits and amount_cents > 0:
                        base, remainder = divmod(amount_cents, len(splits))
                        for index, split in enumerate(splits):
                            split.update({"share_cents": base + (1 if index < remainder else 0), "updated_at": now,
                                          "version": int(split["version"]) + 1, "device_id": invoice["device_id"]})
                            conn.execute("UPDATE invoice_splits SET share_cents=?,updated_at=?,version=?,device_id=? WHERE id=?",
                                         (split["share_cents"], now, split["version"], split["device_id"], split["id"]))
                            enqueue_sync_event(conn, "invoice_splits", split["id"], "upsert", split)
                    audit(conn, job["created_by"], "ocr_complete", "invoice", invoice["id"], {
                        "confidence": result.get("ocr_confidence", 0),
                        "category": result.get("category_name", ""),
                        "classification_reason": result.get("classification_reason", ""),
                    })
                    sync_ocr_issues(conn, invoice["id"], result, user_id=job["created_by"])
    except Exception as exc:
        with transaction() as conn:
            conn.execute(
                "UPDATE ocr_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                (str(exc)[:1000], utc_now(), job_id),
            )
            if job and job["invoice_id"]:
                record_issue(
                    conn, "ocr_failed", f"离线识别失败：{str(exc)[:500]}",
                    invoice_id=job["invoice_id"], severity="error", user_id=job["created_by"],
                )


def get_ocr_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT j.* FROM ocr_jobs j JOIN attachments a ON a.id=j.attachment_id
            WHERE j.id=? AND a.season_id=? AND a.deleted_at IS NULL""",
            (job_id, current_season_id(conn)),
        ).fetchone()
        job = dict(row) if row else None
    if not job:
        return None
    try:
        job["result"] = json.loads(job.pop("result_json") or "{}")
    except json.JSONDecodeError:
        job["result"] = {}
    return job
