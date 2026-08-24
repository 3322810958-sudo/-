from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "燕翔车队经费管理系统_V2.1_使用教程.pdf"
LOGO = ROOT / "app" / "static" / "assets" / "team-logo.png"
WORDMARK = ROOT / "app" / "static" / "assets" / "team-wordmark.png"

PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#07111E")
NAVY_2 = colors.HexColor("#0D1C2D")
CYAN = colors.HexColor("#00C8FF")
RED = colors.HexColor("#F04444")
TEXT = colors.HexColor("#172333")
MUTED = colors.HexColor("#657284")
PALE = colors.HexColor("#EDF7FA")
BORDER = colors.HexColor("#D7E2E8")
WHITE = colors.white


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("YaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("YaHeiBold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("Mono", r"C:\Windows\Fonts\consola.ttf"))
    pdfmetrics.registerFontFamily("YaHei", normal="YaHei", bold="YaHeiBold")


register_fonts()
styles = getSampleStyleSheet()
body = ParagraphStyle(
    "BodyCN", parent=styles["BodyText"], fontName="YaHei", fontSize=9.2,
    leading=15, textColor=TEXT, spaceAfter=3 * mm,
)
small = ParagraphStyle(
    "SmallCN", parent=body, fontSize=7.7, leading=11, textColor=MUTED,
)
h1 = ParagraphStyle(
    "H1CN", parent=styles["Heading1"], fontName="YaHeiBold", fontSize=19,
    leading=25, textColor=NAVY, spaceBefore=2 * mm, spaceAfter=5 * mm,
)
h2 = ParagraphStyle(
    "H2CN", parent=styles["Heading2"], fontName="YaHeiBold", fontSize=11.5,
    leading=16, textColor=NAVY_2, spaceBefore=4 * mm, spaceAfter=2 * mm,
)
caption = ParagraphStyle(
    "CaptionCN", parent=small, fontSize=7.2, leading=10, alignment=TA_CENTER,
)
code = ParagraphStyle(
    "Code", parent=body, fontName="Mono", fontSize=7.7, leading=11,
    textColor=colors.HexColor("#DDF7FF"), leftIndent=2 * mm, rightIndent=2 * mm,
)
white_body = ParagraphStyle(
    "WhiteBody", parent=body, textColor=WHITE, fontSize=9.5, leading=15,
)


def p(text: str, style: ParagraphStyle = body) -> Paragraph:
    return Paragraph(text, style)


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(f"<font color='#00A9D6'>●</font>&nbsp; {item}", body) for item in items]


def callout(title: str, text: str, color: colors.Color = CYAN) -> Table:
    content = [
        p(f"<b>{title}</b>", ParagraphStyle("CallTitle", parent=body, fontName="YaHeiBold", textColor=NAVY, spaceAfter=1 * mm)),
        p(text, small),
    ]
    box = Table([[content]], colWidths=[166 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 4, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    return box


def grid(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[p(value, ParagraphStyle("TH", parent=small, fontName="YaHeiBold", textColor=WHITE)) for value in headers]]
    data += [[p(str(value), small) for value in row] for row in rows]
    table = Table(data, colWidths=[value * mm for value in widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_2),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
    ]
    for row in range(1, len(data)):
        if row % 2 == 0:
            commands.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#F5F8FA")))
    table.setStyle(TableStyle(commands))
    return table


def process(steps: list[tuple[str, str]]) -> Table:
    cells = []
    widths = []
    for index, (title, text) in enumerate(steps, start=1):
        cells.append([
            p(f"<font color='#00A9D6'><b>{index:02d}</b></font>", ParagraphStyle("StepNo", parent=body, fontName="YaHeiBold", fontSize=13, leading=15)),
            p(f"<b>{title}</b><br/><font color='#657284'>{text}</font>", small),
        ])
        widths.extend([8 * mm, 45 * mm])
    flat = []
    for pair in cells:
        flat.extend(pair)
    table = Table([flat], colWidths=widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F9FB")),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ("LINEAFTER", (1, 0), (1, 0), 0.5, BORDER),
        ("LINEAFTER", (3, 0), (3, 0), 0.5, BORDER),
    ]))
    return table


def cover_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#0A2238"))
    canvas.circle(PAGE_W + 8 * mm, PAGE_H - 45 * mm, 75 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(CYAN)
    canvas.setLineWidth(0.8)
    for offset in range(0, 170, 13):
        canvas.line(PAGE_W - (offset + 20) * mm, 0, PAGE_W - offset * mm, 82 * mm)
    if LOGO.exists():
        canvas.drawImage(str(LOGO), 23 * mm, PAGE_H - 60 * mm, 38 * mm, 38 * mm, preserveAspectRatio=True, mask="auto")
    if WORDMARK.exists():
        canvas.drawImage(str(WORDMARK), 65 * mm, PAGE_H - 50 * mm, 75 * mm, 20 * mm, preserveAspectRatio=True, mask="auto")
    canvas.setFillColor(CYAN)
    canvas.rect(23 * mm, PAGE_H - 92 * mm, 38 * mm, 1.4 * mm, fill=1, stroke=0)
    canvas.setFont("YaHeiBold", 26)
    canvas.setFillColor(WHITE)
    canvas.drawString(23 * mm, PAGE_H - 116 * mm, "车队经费管理系统")
    canvas.setFont("YaHeiBold", 16)
    canvas.setFillColor(CYAN)
    canvas.drawString(23 * mm, PAGE_H - 132 * mm, "V2.1  使用教程")
    canvas.setFont("YaHei", 9.5)
    canvas.setFillColor(colors.HexColor("#B7C9D8"))
    canvas.drawString(23 * mm, PAGE_H - 151 * mm, "Windows 软件 · 智能分类 · 动态壁纸 · AA 分摊 · 版本回溯")
    badges = ["OFFLINE FIRST", "SMART CLASSIFY", "WALLPAPER", "ADMIN CONTROL"]
    x = 23 * mm
    for badge in badges:
        width = canvas.stringWidth(badge, "YaHeiBold", 7) + 8 * mm
        canvas.setStrokeColor(colors.HexColor("#31506A"))
        canvas.roundRect(x, 51 * mm, width, 8 * mm, 2 * mm, stroke=1, fill=0)
        canvas.setFont("YaHeiBold", 7)
        canvas.setFillColor(colors.HexColor("#D8EAF5"))
        canvas.drawCentredString(x + width / 2, 53.5 * mm, badge)
        x += width + 3 * mm
    canvas.setFont("YaHei", 8)
    canvas.setFillColor(colors.HexColor("#7590A6"))
    canvas.drawString(23 * mm, 22 * mm, "燕翔车队 Racing Team  ·  2026.08")
    canvas.restoreState()


def content_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(WHITE)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 16 * mm, PAGE_W, 16 * mm, fill=1, stroke=0)
    canvas.setFillColor(CYAN)
    canvas.rect(0, PAGE_H - 16 * mm, 4 * mm, 16 * mm, fill=1, stroke=0)
    canvas.setFont("YaHeiBold", 7.5)
    canvas.setFillColor(WHITE)
    canvas.drawString(15 * mm, PAGE_H - 10 * mm, "YANXIANG RACING · EXPENSE CONTROL V2.1")
    canvas.setStrokeColor(BORDER)
    canvas.line(15 * mm, 14 * mm, PAGE_W - 15 * mm, 14 * mm)
    canvas.setFont("YaHei", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 8.5 * mm, "燕翔车队经费管理系统 V2.1 使用教程")
    canvas.drawRightString(PAGE_W - 15 * mm, 8.5 * mm, f"{doc.page - 1:02d}")
    canvas.restoreState()


def make_document() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=25 * mm, bottomMargin=20 * mm,
        title="燕翔车队经费管理系统 V2.1 使用教程",
        author="燕翔车队 Racing Team",
        subject="经费管理系统运行、智能分类、动态壁纸、AA、同步与回溯说明",
    )
    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover-frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    content_frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="content-frame")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=cover_page),
        PageTemplate(id="content", frames=[content_frame], onPage=content_page),
    ])

    story = [Spacer(1, PAGE_H - 2 * mm), NextPageTemplate("content"), PageBreak()]

    story += [
        p("01  快速开始", h1),
        p("本系统是面向车队日常采购、垫付、报销和 AA 结算的 Windows 软件。没有服务器时可完整离线运行；以后增加 Linux 服务器即可启用双向同步。"),
        process([
            ("启动", "双击软件主程序"),
            ("登录", "先使用管理员账号"),
            ("改密", "首次登录立即修改"),
        ]),
        Spacer(1, 5 * mm),
        p("默认账号", h2),
        grid(
            ["身份", "账号", "初始密码", "主要权限"],
            [
                ["管理员", "admin", "YXRT@2026", "全部功能；版本回溯；账号与同步管理"],
                ["成员 01～08", "member01～member08", "Member@2026", "维护账目、附件、AA 与结算"],
                ["公共查看", "viewer", "View@2026", "只读查看与导出"],
            ],
            [27, 38, 38, 63],
        ),
        Spacer(1, 5 * mm),
        callout("首次使用必须做", "管理员首次登录会自动打开修改登录信息窗口。请设置车队自用账号和高强度密码，再为每位成员建立或调整独立账号。公共查看账号只适合展示数据，不要授予管理员权限。", RED),
        p("导航概览", h2),
        grid(
            ["页面", "用途", "建议使用人"],
            [
                ["数据驾驶舱", "总支出、待报销、发票量、分类与成员垫付", "全员 / 公共查看"],
                ["发票台账", "上传、OCR、编辑、筛选与批量导入", "管理员 / 成员"],
                ["AA 结算", "成员净额、建议转账、还款记录", "管理员 / 成员"],
                ["日志与回溯", "审计、保护点、恢复历史版本", "仅管理员"],
            ],
            [34, 87, 45],
        ),
        PageBreak(),

        p("02  录入发票、离线 OCR 与智能分类", h1),
        p("支持 JPG、PNG、BMP、TIFF、WEBP、PDF、TXT 和 ZIP。文字识别、费用分类和物品类型判断均在本机完成；发票图片与数据库保存在软件文件夹内。"),
        process([
            ("上传", "选择图片或 PDF"),
            ("识别", "点击离线 OCR"),
            ("核对", "校验号码、日期、金额"),
        ]),
        p("单张录入", h2),
        *bullets([
            "进入“发票台账”，点击“新增发票”，先选择附件。",
            "点击“离线 OCR”。文本型 PDF 会优先直接读取；扫描件和图片使用 PP-OCRv5 中文轻量模型。",
            "确认发票号、销售方、开票日期、金额、税额，以及系统建议的分类、物品类型和资金来源。",
            "选择付款成员和分摊方式；报销金额、报销日期、备注均可留空。",
            "保存后驾驶舱、统计和 AA 净额立即更新。",
        ]),
        p("ZIP 批量导入", h2),
        *bullets([
            "将多份发票文件直接放入 ZIP；可包含子文件夹。",
            "上传 ZIP 后，系统逐份建立草稿，并行执行识别。",
            "识别完成后逐条核对并补充付款人、分类和分摊成员。",
            "系统会拦截目录穿越和异常压缩比文件，避免恶意压缩包写出附件目录。",
        ]),
        callout("容量与速度", "软件没有人为设置的固定附件大小或数量上限。实际批量规模取决于磁盘、内存和 CPU。建议按月或按采购批次打包；OCR 工作线程默认 2 个，可通过运行参数调整为 1～4 个。"),
        PageBreak(),
        p("03  智能分类与识别核对", h1),
        p("系统在 OCR 完成后给出费用分类和物品类型建议。建议用于加快录入，最终账务口径由人工确认。"),
        *bullets([
            "先匹配管理员维护的关键词规则，再参考已保存的同销售方和同类历史记录。",
            "内置继电器、紧固件、耗材、电器件、线束连接器、传感器、电池、复材、金属、加工、制动和传动等常用类型。",
            "人工修改并保存后的结果会在本机参与后续判断；金额、发票号和分类仍必须人工核对。",
            "管理员可在“系统设置 → 智能分类规则”调整关键词、分类、物品类型、优先级和启用状态。",
        ]),
        p("常见匹配示例", h2),
        grid(
            ["票面关键词", "建议物品类型", "建议费用分类"],
            [
                ["继电器、接触器、保险丝", "继电器与接触器", "电气与电子"],
                ["螺栓、螺母、垫圈、卡簧", "紧固件", "标准件与紧固件"],
                ["扎带、胶带、砂纸、手套", "通用耗材", "耗材"],
                ["线束、端子、航空插头", "线束与连接器", "电气与电子"],
            ],
            [55, 55, 56],
        ),
        Spacer(1, 4 * mm),
        callout("规则维护建议", "关键词应具体且彼此可区分；优先级数字越大越先匹配。出现误判时，先人工修正并保存，再由管理员补充明确关键词，避免使用“材料”“配件”等过宽词。"),
        p("识别核对重点", h2),
        grid(
            ["字段", "常见风险", "检查方法"],
            [
                ["发票号码", "二维码、下载次数或税号被误判", "与票面右上角号码逐位核对"],
                ["价税合计", "将不含税金额或税额当成总额", "优先核对“小写”后的金额"],
                ["销售方", "购买方名称被误识别", "核对销售方信息区域"],
                ["日期", "文件名日期与开票日期混淆", "以票面开票日期为准"],
            ],
            [31, 70, 65],
        ),
        PageBreak(),

        p("04  AA 分摊与金额规则", h1),
        p("每笔发票可独立决定是否 AA、由哪些成员承担以及分配权重。系统用整数“分”计算，避免浮点误差。"),
        grid(
            ["方式", "适用场景", "系统动作"],
            [
                ["全队均摊", "全车队共同使用的耗材或服务", "所有启用成员等额承担"],
                ["指定成员均摊", "仅某项目组或某次测试参与者使用", "只在勾选成员之间等额分配"],
                ["按权重分摊", "各组用量、人数或责任不同", "按输入权重比例分配"],
                ["付款人自付", "个人承担、不产生 AA 往来", "全部金额归付款成员"],
                ["不启用 AA", "只跟踪报销或车队公账支出", "不生成成员份额"],
            ],
            [34, 72, 60],
        ),
        p("尾差处理示例", h2),
        p("100.01 元由 3 人等额承担时，系统得到 10001 分，基础份额为 3333 分，余 2 分依成员顺序补入。最终份额为 33.34、33.34、33.33 元，总和严格等于 100.01 元。"),
        grid(
            ["成员", "权重", "应承担", "说明"],
            [
                ["成员 A", "1", "33.34 元", "获得第 1 个尾差分"],
                ["成员 B", "1", "33.34 元", "获得第 2 个尾差分"],
                ["成员 C", "1", "33.33 元", "基础份额"],
                ["合计", "3", "100.01 元", "与发票总额一致"],
            ],
            [38, 28, 42, 58],
        ),
        Spacer(1, 5 * mm),
        callout("推荐录入顺序", "先确认“谁实际付款”，再决定“谁最终承担”。付款人和承担人不是同一概念：付款人形成垫付，承担人形成应付。系统用二者差额计算成员净额。", RED),
        p("权重分摊", h2),
        p("权重只表示相对比例。例如三名成员权重为 1、2、3，则分别承担总额的 1/6、2/6、3/6。权重可以使用人数、材料用量或其他车队内部认可的口径。"),
        PageBreak(),

        p("05  报销、结算与成员往来", h1),
        p("报销用于记录外部经费返还；AA 结算用于记录成员之间的转账。两类资金流应分别维护。"),
        process([
            ("登记支出", "保存付款人与份额"),
            ("查看净额", "系统生成建议转账"),
            ("登记还款", "录入实际转账"),
        ]),
        p("报销状态", h2),
        grid(
            ["状态", "条件", "建议"],
            [
                ["待报销", "已报销金额为 0", "保留报销来源，待到账后更新"],
                ["部分报销", "已报销金额大于 0 且小于总额", "记录实际到账额与日期"],
                ["已报销", "已报销金额等于总额", "确认到账账户及对应材料"],
                ["不报销", "无需外部资金返还", "资金来源可选车队账目或成员 AA"],
            ],
            [30, 73, 63],
        ),
        p("AA 结算页面", h2),
        *bullets([
            "正净额表示成员垫付多于本人承担，应收回资金；负净额表示应向其他成员付款。",
            "“建议转账”会用较少的转账路径撮合应收与应付成员。",
            "实际转账完成后再登记还款；转出人、收款人、金额和日期必须与支付记录一致。",
            "登记错误时可删除结算记录，系统会恢复对应净额。",
        ]),
        callout("账务核对", "每次集中结算前导出 CSV，并核对：发票总额、付款人、参与成员、已报销金额、已登记还款。任何字段有误都应先修正发票，再执行结算。"),
        p("示例流程", h2),
        p("成员 03 垫付 1,380.00 元，全队 8 人均摊。成员 03 本人承担 172.50 元，因此在未发生其他账目时应收 1,207.50 元；其余 7 人各应付 172.50 元。若该发票随后全额报销，应同时更新报销金额，再按车队实际规则确认是否仍需成员间结算。"),
        PageBreak(),

        p("06  统计、成员与自定义项目", h1),
        p("驾驶舱用于快速判断当前支出和报销压力；分类统计用于按口径复核与汇报。"),
        p("统计维度", h2),
        grid(
            ["维度", "可回答的问题", "维护重点"],
            [
                ["分类", "材料、加工、三电、差旅分别支出多少", "分类名称保持稳定，避免同义重复"],
                ["物品类型", "连接器、加工件、紧固件等具体花费", "OCR 结果需人工确认"],
                ["资金来源", "车队、大创、教师经费、赞助分别承担多少", "每笔发票只选一个主来源"],
                ["成员垫付", "谁垫付最多、还有多少待收回", "付款成员必须真实准确"],
            ],
            [31, 82, 53],
        ),
        p("成员与账号", h2),
        *bullets([
            "成员资料用于 AA 和垫付统计；登录账号用于权限控制，两者可以关联。",
            "新增成员后，可另建成员账号；账号显示名称与成员姓名可不同。",
            "停用成员会从新发票选择列表移除，但历史份额保留。",
            "管理员可重设账号密码、改变角色或停用账号；不可停用自己的管理员身份。",
        ]),
        p("分类与资金来源", h2),
        *bullets([
            "系统提供常用默认项；管理员和成员可在“系统设置 → 分类与资金来源”增加或修改项目。",
            "报销来源是非必填项。没有明确来源时可留空，确认后再补充。",
            "不再使用的项目可停用，必要时可重新启用；历史发票和统计仍会保留。",
        ]),
        callout("公共展示", "使用 viewer 账号投屏或给指导教师查看。该账号能浏览驾驶舱、发票、AA 和分类统计，也能导出 CSV，但不能修改、删除、回溯或管理账号。"),
        p("筛选与导出", h2),
        p("在发票台账可按关键词、分类、报销状态和日期检索；在分类统计可设置起止日期。CSV 使用 UTF-8 BOM，可直接在 Excel 中打开中文内容。"),
        PageBreak(),

        p("07  动态外观、数据与备份", h1),
        p("系统默认使用深色赛车数据风格和燕翔车队标识。管理员可更换背景、强调色、车队名称、背景遮罩和登录页轮播。"),
        p("系统背景与登录轮播", h2),
        process([
            ("打开设置", "进入系统外观"),
            ("选择媒体", "图片、GIF 或视频"),
            ("预览保存", "调遮罩保证文字清晰"),
        ]),
        *bullets([
            "登录轮播可维护标题、顺序、单张时长，并选择淡入淡出或滑动切换。",
            "密码框右侧可显示或隐藏密码；退出登录后自动恢复隐藏。",
            "Wallpaper Engine 扫描会查找 Steam 创意工坊和本地项目；图片、GIF、MP4、WEBM 可直接播放。",
            "场景、网页和应用型壁纸使用预览图；导入不会修改 Steam 或创意工坊文件。",
        ]),
        p("本地数据位置", h2),
        grid(
            ["位置", "内容", "迁移要求"],
            [
                ["data/yanxiang_expense.db", "账号、发票、分摊、结算、日志和版本", "必须保留"],
                ["uploads/", "发票、票据、背景和登录轮播媒体", "必须与数据库同时复制"],
                ["models/", "PP-OCRv5 离线模型", "建议保留，避免重新下载"],
                ["tmp/", "临时解压与运行文件", "可在软件关闭后清理"],
            ],
            [55, 67, 44],
        ),
        p("完整备份", h2),
        *bullets([
            "管理员进入“系统设置”，点击导出完整备份。",
            "备份包同时包含数据库和附件，文件名带导出时间。",
            "每周至少一次；赛前、集中采购后、批量回溯前额外备份。",
            "恢复备份会替换当前业务数据，请先再导出一份现状备份。",
        ]),
        callout("迁移电脑", "先关闭软件，再复制整个“Windows软件”文件夹。不要只复制 EXE；数据库、附件和 OCR 模型均为独立目录。复制完成后从新文件夹启动即可。", RED),
        p("演示数据", h2),
        p("首次启动含 5 条演示发票和 8 位示例成员。熟悉功能后，管理员可在系统设置中一键清除演示发票。成员与账号不会随演示发票一起删除，可继续修改后使用。"),
        PageBreak(),

        p("08  日志、版本与管理员回溯", h1),
        p("审计日志记录登录、增删改、上传、OCR、同步、备份与回溯等关键动作。版本用于恢复业务数据，只有管理员可查看和执行。"),
        process([
            ("建立版本", "重大修改前手动保存"),
            ("检查标签", "确认时间与说明"),
            ("执行回溯", "系统先建保护点再恢复"),
        ]),
        p("自动保护点", h2),
        *bullets([
            "首次初始化时建立初始演示版本。",
            "接收云端变更前建立同步保护点。",
            "恢复历史版本前建立“回溯前自动保护点”。",
            "最多保留最近 100 个版本，旧版本按时间自动淘汰。",
        ]),
        p("回溯范围", h2),
        grid(
            ["会恢复", "不会恢复", "回溯后动作"],
            [
                ["成员、分类、资金来源、附件记录、发票、分摊、结算、外观", "登录账号和密码、同步密钥、会话", "生成新的同步事件，使其他端接收恢复结果"],
            ],
            [62, 52, 52],
        ),
        Spacer(1, 5 * mm),
        callout("回溯前检查", "先导出完整备份，确认目标版本的时间与标签，并通知正在录入的成员暂停修改。回溯完成后检查发票总数、总金额、成员余额和附件可用性，再恢复协作。", RED),
        p("推荐版本命名", h2),
        grid(
            ["时机", "标签示例", "原因"],
            [
                ["月度关账", "2026-08 月度确认版", "形成稳定月报基线"],
                ["集中报销前", "大创第二批提交前", "便于核对提交清单"],
                ["批量导入前", "8 月票据批量导入前", "错误导入可快速恢复"],
                ["赛季切换", "2026 赛季封存", "保留完整赛季状态"],
            ],
            [39, 67, 60],
        ),
        PageBreak(),

        p("09  云端部署与双向同步", h1),
        p("当前可直接使用本地模式。以后准备 Linux 服务器后，可通过 Docker 部署同一系统，Windows 端填入服务器地址和共享密钥即可同步。"),
        p("同步机制", h2),
        grid(
            ["机制", "作用"],
            [
                ["离线队列", "断网时继续录入，联网后按队列上传"],
                ["双向增量", "只传输发生变化的业务记录"],
                ["附件校验", "使用 SHA-256 校验，重复附件不重复传输"],
                ["冲突处理", "更新时间较新的记录优先；相同时间用设备标识决胜"],
                ["同步保护点", "应用远端变更前自动保存当前版本"],
            ],
            [44, 122],
        ),
        p("Docker 部署步骤", h2),
        *bullets([
            "准备 Ubuntu 24.04 LTS 或同等 Linux，建议至少 2 核 CPU、4 GB 内存。",
            "上传项目，进入 deploy 目录，将 .env.example 复制为 .env。",
            "把同步密钥改为至少 32 位随机字符串。",
            "执行：docker compose --env-file .env up -d --build",
            "使用域名和 HTTPS 反向代理；不要将 8765 端口直接暴露到公网。",
            "Windows 管理员在系统设置中填写 HTTPS 地址和相同密钥，点击“立即同步”。",
        ]),
        Table([[p("docker compose --env-file .env up -d --build", code)]], colWidths=[166 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY_2),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#31506A")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ])),
        Spacer(1, 4 * mm),
        callout("生产安全", "必须使用 HTTPS；同步密钥只保存在服务器环境文件和管理员设置中。定期备份 deploy/runtime。服务器尚未准备好时，保持本地模式，不影响任何核心功能。", RED),
        PageBreak(),

        p("10  故障处理与交付检查", h1),
        grid(
            ["现象", "处理"],
            [
                ["首次 OCR 较慢", "模型首次载入需要数秒；后续识别会加快"],
                ["OCR 字段有误", "使用清晰原图，转正票面；保存前人工核对关键字段"],
                ["分类建议不准", "人工修改后保存；管理员补充明确关键词或提高优先级"],
                ["壁纸列表为空", "确认 Steam 订阅已下载，再从系统外观重新扫描"],
                ["视频背景卡顿", "换用较短的 1080p MP4/WEBM，或使用静态预览图"],
                ["桌面窗口未打开", "检查安全软件；浏览器访问 http://127.0.0.1:8765"],
                ["默认端口被占用", "桌面软件会自动选择另一个本机端口"],
                ["同步提示连接失败", "检查 HTTPS 地址、容器状态、证书和同步密钥"],
                ["成员忘记密码", "管理员在成员与账号页面重设密码"],
                ["管理员忘记密码", "使用最近完整备份恢复；不要直接改数据库"],
                ["数据录入错误较多", "先导出备份，再由管理员选择合适版本回溯"],
            ],
            [55, 111],
        ),
        p("正式启用检查表", h2),
        *bullets([
            "管理员账号和密码已修改，默认公共账号是否继续启用已确认。",
            "真实成员姓名、部门和账号已建立，未参与成员已停用。",
            "分类和资金来源符合本赛季财务口径。",
            "演示发票已清除，驾驶舱金额归零或与真实数据一致。",
            "完成一张发票的上传、OCR、智能分类、指定成员分摊和保存测试。",
            "完成背景更换和登录页轮播测试，确保文字在动态媒体上清晰。",
            "完成一条 AA 还款登记与撤销测试。",
            "导出并妥善保存第一份完整备份。",
            "若启用云端，已完成 Windows 与服务器双向修改测试。",
        ]),
        Spacer(1, 5 * mm),
        callout("核心原则", "OCR 用于加速录入，最终账务仍以人工核对为准；回溯用于纠错，完整备份用于灾难恢复；公共账号用于查看，管理员权限只授予少数负责人。"),
        Spacer(1, 8 * mm),
        p("— END OF GUIDE —", ParagraphStyle("End", parent=caption, fontName="YaHeiBold", textColor=CYAN, fontSize=8)),
    ]

    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    make_document()
