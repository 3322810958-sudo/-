from __future__ import annotations

import json
import re
import sqlite3
import uuid
from copy import deepcopy
from typing import Any

from .database import current_season_id, setting


PRODUCT_TYPES = [
    "继电器/接触器", "高压电器件", "低压电器件", "线束/连接器", "传感器",
    "电子元器件", "电芯/电池材料", "紧固件", "轴承/标准件", "3D打印耗材",
    "胶粘/化学耗材", "一般耗材", "碳纤维/复合材料", "金属/工程材料", "机械加工",
    "车架/悬架/转向零件", "制动/传动/轮胎", "冷却/热管理", "工具/仪器",
    "软件/技术服务", "办公/资料", "物流/快递", "差旅/交通", "餐饮", "其他",
    # 保留 V2.0 的已有类型，升级旧数据库后仍可正常编辑历史记录。
    "3D打印/耗材", "电池/电气材料", "传感器/电子元件", "紧固件/标准件",
    "材料/板材", "碳纤维/复材", "机械加工/零件", "车架/悬架/转向",
    "胶粘/辅料耗材", "软件/服务",
]
PRODUCT_TYPES = list(dict.fromkeys(PRODUCT_TYPES))


DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "rule_relay", "name": "继电器与接触器", "keywords": ["继电器", "固态继电器", "接触器", "RELAY"], "category_id": "cat_electrical", "product_type": "继电器/接触器", "priority": 180, "active": True},
    {"id": "rule_hv", "name": "高压电器件", "keywords": ["高压互锁", "预充", "绝缘监测", "IMD", "AIR+", "AIR-", "高压连接器", "熔断器"], "category_id": "cat_electrical", "product_type": "高压电器件", "priority": 165, "active": True},
    {"id": "rule_lv", "name": "低压电器件", "keywords": ["开关", "按钮", "断路器", "保险丝", "保险盒", "蜂鸣器", "指示灯", "电源模块"], "category_id": "cat_electrical", "product_type": "低压电器件", "priority": 145, "active": True},
    {"id": "rule_harness", "name": "线束与连接器", "keywords": ["线束", "连接器", "接插件", "端子", "插头", "插座", "屏蔽线", "压线鼻", "航空插"], "category_id": "cat_electrical", "product_type": "线束/连接器", "priority": 155, "active": True},
    {"id": "rule_sensor", "name": "传感器", "keywords": ["传感器", "编码器", "热电偶", "压力变送器", "霍尔", "应变片", "IMU"], "category_id": "cat_electrical", "product_type": "传感器", "priority": 150, "active": True},
    {"id": "rule_electronic", "name": "电子元器件", "keywords": ["电阻", "电容", "二极管", "三极管", "MOS管", "芯片", "PCB", "电路板", "单片机"], "category_id": "cat_electrical", "product_type": "电子元器件", "priority": 145, "active": True},
    {"id": "rule_battery", "name": "电芯与电池材料", "keywords": ["电芯", "电池", "铜排", "镍片", "绝缘纸", "青稞纸", "BMS"], "category_id": "cat_electrical", "product_type": "电芯/电池材料", "priority": 150, "active": True},
    {"id": "rule_fastener", "name": "紧固件", "keywords": ["螺栓", "螺钉", "螺母", "垫圈", "垫片", "铆钉", "卡簧", "开口销", "紧固件"], "category_id": "cat_material", "product_type": "紧固件", "priority": 160, "active": True},
    {"id": "rule_bearing", "name": "轴承与标准件", "keywords": ["轴承", "关节轴承", "直线轴承", "鱼眼轴承", "标准件"], "category_id": "cat_material", "product_type": "轴承/标准件", "priority": 145, "active": True},
    {"id": "rule_print", "name": "3D 打印耗材", "keywords": ["3D打印", "PLA", "PETG", "ABS", "尼龙耗材", "光敏树脂", "打印耗材"], "category_id": "cat_material", "product_type": "3D打印耗材", "priority": 155, "active": True},
    {"id": "rule_chemical", "name": "胶粘与化学耗材", "keywords": ["结构胶", "环氧胶", "螺纹胶", "胶带", "清洗剂", "脱模剂", "润滑脂", "切削液", "密封胶"], "category_id": "cat_material", "product_type": "胶粘/化学耗材", "priority": 150, "active": True},
    {"id": "rule_consumable", "name": "一般耗材", "keywords": ["耗材", "砂纸", "扎带", "热缩管", "手套", "抹布", "磨片", "锯片", "钻头"], "category_id": "cat_material", "product_type": "一般耗材", "priority": 110, "active": True},
    {"id": "rule_composite", "name": "复合材料", "keywords": ["碳纤维", "预浸料", "玻璃纤维", "蜂窝芯", "真空袋", "复合材料"], "category_id": "cat_material", "product_type": "碳纤维/复合材料", "priority": 145, "active": True},
    {"id": "rule_metal", "name": "金属与工程材料", "keywords": ["铝板", "钢板", "管材", "棒材", "型材", "铝合金", "钢材", "尼龙板", "亚克力板"], "category_id": "cat_material", "product_type": "金属/工程材料", "priority": 125, "active": True},
    {"id": "rule_machine", "name": "机械加工", "keywords": ["数控加工", "CNC", "车削", "铣削", "线切割", "激光切割", "水切割", "精加工", "加工费"], "category_id": "cat_machining", "product_type": "机械加工", "priority": 150, "active": True},
    {"id": "rule_chassis", "name": "底盘转向零件", "keywords": ["车架", "悬架", "转向", "立柱", "摇臂", "转向节", "横拉杆"], "category_id": "cat_material", "product_type": "车架/悬架/转向零件", "priority": 120, "active": True},
    {"id": "rule_brake", "name": "制动传动轮胎", "keywords": ["制动", "刹车", "制动盘", "卡钳", "链轮", "链条", "轮胎", "传动轴"], "category_id": "cat_material", "product_type": "制动/传动/轮胎", "priority": 125, "active": True},
    {"id": "rule_thermal", "name": "冷却与热管理", "keywords": ["散热器", "冷却", "水泵", "风扇", "水管", "热管理", "导热垫"], "category_id": "cat_material", "product_type": "冷却/热管理", "priority": 125, "active": True},
    {"id": "rule_tool", "name": "工具与仪器", "keywords": ["工具", "万用表", "示波器", "量具", "卡尺", "扭力扳手", "电烙铁"], "category_id": "cat_material", "product_type": "工具/仪器", "priority": 120, "active": True},
    {"id": "rule_software", "name": "软件与技术服务", "keywords": ["软件", "许可证", "授权", "云服务", "技术服务", "仿真服务"], "category_id": "cat_office", "product_type": "软件/技术服务", "priority": 120, "active": True},
    {"id": "rule_office", "name": "办公资料", "keywords": ["文具", "办公用品", "资料打印", "复印", "装订", "书籍"], "category_id": "cat_office", "product_type": "办公/资料", "priority": 115, "active": True},
    {"id": "rule_logistics", "name": "物流快递", "keywords": ["物流", "快递", "运费", "运输费", "货运"], "category_id": "cat_logistics", "product_type": "物流/快递", "priority": 140, "active": True},
    {"id": "rule_travel", "name": "差旅交通", "keywords": ["火车票", "机票", "住宿", "酒店", "出租车", "高速通行", "停车费", "燃油费"], "category_id": "cat_travel", "product_type": "差旅/交通", "priority": 140, "active": True},
    {"id": "rule_food", "name": "餐饮", "keywords": ["餐饮", "饭店", "餐厅", "外卖", "食品"], "category_id": "cat_other", "product_type": "餐饮", "priority": 105, "active": True},
]


def default_rules() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_RULES)


def load_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    raw = setting(conn, "classification_rules", "")
    if not raw or raw == "[]":
        return default_rules()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return default_rules()
    if isinstance(parsed, dict) and isinstance(parsed.get("rules"), list):
        return sanitize_rules(parsed["rules"])
    if isinstance(parsed, list):
        return sanitize_rules(parsed) or default_rules()
    return default_rules()


def serialize_rules(rules: list[dict[str, Any]]) -> str:
    return json.dumps({"version": 1, "rules": sanitize_rules(rules)}, ensure_ascii=False, separators=(",", ":"))


def sanitize_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rules[:500]:
        if not isinstance(raw, dict):
            continue
        rule_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(raw.get("id") or ""))[:80] or f"rule_{uuid.uuid4().hex}"
        if rule_id in seen:
            rule_id = f"rule_{uuid.uuid4().hex}"
        seen.add(rule_id)
        keywords_raw = raw.get("keywords") or []
        if isinstance(keywords_raw, str):
            keywords_raw = re.split(r"[,，;；\n]", keywords_raw)
        keywords = list(dict.fromkeys(str(value).strip()[:80] for value in keywords_raw if str(value).strip()))[:80]
        if not keywords:
            continue
        try:
            priority = max(1, min(999, int(raw.get("priority", 100))))
        except (TypeError, ValueError):
            priority = 100
        clean.append({
            "id": rule_id,
            "name": str(raw.get("name") or keywords[0]).strip()[:80],
            "keywords": keywords,
            "category_id": str(raw.get("category_id") or "")[:100],
            "product_type": str(raw.get("product_type") or "其他").strip()[:80] or "其他",
            "priority": priority,
            "active": bool(raw.get("active", True)),
        })
    return clean


def _compact(value: str) -> str:
    return re.sub(r"[\s\-_/·•,，.。:：;；()（）\[\]【】]+", "", str(value or "")).upper()


def classify_invoice(
    conn: sqlite3.Connection,
    text: str,
    *,
    vendor: str = "",
    detected_product_type: str = "",
) -> dict[str, Any]:
    haystack = _compact("\n".join([text or "", vendor or "", detected_product_type or ""]))
    candidates: list[dict[str, Any]] = []
    category_ids = {row[0] for row in conn.execute("SELECT id FROM categories WHERE deleted_at IS NULL").fetchall()}

    for rule in load_rules(conn):
        if not rule.get("active"):
            continue
        matched = [keyword for keyword in rule["keywords"] if _compact(keyword) and _compact(keyword) in haystack]
        if not matched:
            continue
        category_id = rule.get("category_id") if rule.get("category_id") in category_ids else ""
        score = int(rule.get("priority", 100)) + min(24, len(matched) * 4) + min(16, max(len(_compact(word)) for word in matched))
        candidates.append({
            "score": score,
            "category_id": category_id,
            "product_type": rule.get("product_type") or "其他",
            "reason": f"规则“{rule.get('name') or matched[0]}”",
            "matched_keywords": matched,
        })

    vendor_key = str(vendor or "").strip()
    if vendor_key:
        learned = conn.execute(
            """SELECT category_id,product_type,COUNT(*) AS uses
            FROM invoices WHERE season_id=? AND deleted_at IS NULL AND is_demo=0 AND trim(vendor)=? AND category_id IS NOT NULL
            GROUP BY category_id,product_type ORDER BY uses DESC,MAX(updated_at) DESC LIMIT 1""",
            (current_season_id(conn), vendor_key),
        ).fetchone()
        if learned and learned["category_id"] in category_ids:
            candidates.append({
                "score": 135 + min(30, int(learned["uses"]) * 5),
                "category_id": learned["category_id"],
                "product_type": learned["product_type"] or detected_product_type or "其他",
                "reason": f"已学习商家“{vendor_key[:28]}”的历史选择",
                "matched_keywords": [vendor_key],
            })

    if not candidates:
        return {
            "category_id": "", "category_name": "", "product_type": detected_product_type or "其他",
            "classification_confidence": 0.0, "classification_reason": "未找到可靠匹配，请人工确认",
            "matched_keywords": [],
        }

    best = max(candidates, key=lambda item: (item["score"], len(item["matched_keywords"])))
    category = conn.execute("SELECT name FROM categories WHERE id=?", (best["category_id"],)).fetchone() if best["category_id"] else None
    return {
        "category_id": best["category_id"],
        "category_name": str(category[0]) if category else "",
        "product_type": best["product_type"],
        "classification_confidence": round(min(0.98, 0.48 + best["score"] / 360), 4),
        "classification_reason": best["reason"],
        "matched_keywords": best["matched_keywords"],
    }


def detect_product_type(text: str) -> str:
    haystack = _compact(text)
    matches: list[tuple[int, str]] = []
    for rule in DEFAULT_RULES:
        matched = [word for word in rule["keywords"] if _compact(word) in haystack]
        if matched:
            score = int(rule["priority"]) + max(len(_compact(word)) for word in matched)
            matches.append((score, str(rule["product_type"])))
    return max(matches, default=(0, "其他"))[1]
