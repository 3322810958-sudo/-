from __future__ import annotations

from app.business import distribute_equal, distribute_weighted, to_cents
from app.ocr_engine import parse_invoice_text


def test_cent_conversion_and_rounding():
    assert to_cents("12.345") == 1235
    assert sum(distribute_equal(100, ["a", "b", "c"]).values()) == 100
    assert distribute_equal(2, ["a", "b", "c"]) == {"a": 1, "b": 1, "c": 0}
    assert sum(distribute_weighted(1001, {"a": 1, "b": 2, "c": 3}).values()) == 1001


def test_invoice_text_parser():
    text = """增值税电子普通发票
    发票号码：26322000001717751491
    开票日期：2026年08月20日
    销售方名称：苏州测试电子科技有限公司
    项目名称：连接器与屏蔽线
    价税合计（小写）￥1380.00
    税额合计：￥12.50"""
    result = parse_invoice_text(text, 0.91)
    assert result["invoice_date"] == "2026-08-20"
    assert result["total_amount"] == 1380.0
    assert result["tax_amount"] == 12.5
    assert result["product_type"] == "线束/连接器"
    assert result["invoice_no"] == "26322000001717751491"
    assert result["vendor"] == "苏州测试电子科技有限公司"


def test_invoice_text_parser_handles_number_after_layout_text():
    text = "发票号码：\n开票日期：\n项目名称 金额\n26322000001717751491\n2026年03月06日\n价税合计（小写）￥27.10"
    result = parse_invoice_text(text)
    assert result["invoice_no"] == "26322000001717751491"
    assert result["invoice_date"] == "2026-03-06"
