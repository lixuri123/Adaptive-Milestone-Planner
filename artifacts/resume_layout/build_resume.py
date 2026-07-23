from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


PAGE_MARGIN_CM = 1.5
BODY_PT = 9.2
SMALL_PT = 8.6
PROJECT_TITLE_PT = 10.5
SECTION_PT = 13.0
NAME_PT = 20.0
ACCENT = "1F4E79"
TEXT = "333F50"
MUTED = "666666"
CHINESE_FONT = "微软雅黑"
LATIN_FONT = "Arial"


PROJECT_BULLETS: tuple[tuple[tuple[str, bool], ...], ...] = (
    (
        (
            "基于 Hermes 插件体系设计多专家 Agent 工作流，通过稀疏路由按需调用 "
            "Research、Career、Communication 专家，将通知、研究任务和求职目标转化为"
            "结构化 WorkPackage；利用 JSON Schema 与业务校验约束任务依赖、完成标准、"
            "截止时间及 P50/P90 工作量，降低模型幻觉和非法输出进入执行链路的风险。",
            False,
        ),
    ),
    (
        (
            "实现确定性里程碑规划器：基于 DAG 拓扑依赖、任务可拆分性、风险等级、"
            "内外部截止时间与每日可用容量，将长期任务分配为 1–42 天的日级目标；"
            "自动扣除日历占用、固定事项、恢复时间和容量缓冲，并在资源不足时输出延期、"
            "缩减范围或增加容量等可审批方案。",
            False,
        ),
    ),
    (
        (
            "构建执行反馈与自适应重规划闭环，支持 done / partial / blocked / skipped "
            "等 check-in；根据实际耗时动态修正剩余工作量，并在同领域积累至少 3 个样本后，"
            "使用中位数对估时进行 0.75–1.5 倍有界校准，偏差发生时生成新的 replan 草案"
            "而非静默覆盖原计划。",
            False,
        ),
    ),
    (
        (
            "设计可审计、Human-in-the-loop 的状态系统，使用 SQLite 持久化事件来源、"
            "专家输出、WorkPackage、计划版本、审批记录、Token/成本和执行反馈；实现计划"
            "确认/替换的原子状态迁移、提醒去重及两阶段投递回执，并通过有界临时上下文接入 "
            "Agent 对话，避免破坏长期会话的 Prompt Cache。",
            False,
        ),
    ),
    (
        (
            "编写并实际通过 44 项单元及集成测试，覆盖结构化输出校验、依赖环检测、"
            "稀疏路由、容量不足、计划确定性、审批替换、部分完成后的重规划、提醒合并与"
            "运行时上下文隔离等关键场景。",
            False,
        ),
    ),
)


SKILL_BULLETS: tuple[tuple[tuple[str, bool], ...], ...] = (
    (
        ("编程语言与框架：", True),
        ("Python、C++、C，了解常用 Python 类库与开发框架。", False),
    ),
    (
        ("数据与中间件：", True),
        ("熟悉 MySQL 基础数据结构、事务等，具有 Redis 使用经验。", False),
    ),
    (
        ("工程工具：", True),
        ("了解 Linux 常用命令和 Git 版本控制。", False),
    ),
)


def set_run_font(
    run,
    *,
    size: float,
    bold: bool = False,
    color: str = TEXT,
) -> None:
    run.bold = bold
    run.font.name = LATIN_FONT
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    run_properties = run._element.get_or_add_rPr()
    fonts = run_properties.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        run_properties.insert(0, fonts)
    fonts.set(qn("w:ascii"), LATIN_FONT)
    fonts.set(qn("w:hAnsi"), LATIN_FONT)
    fonts.set(qn("w:eastAsia"), CHINESE_FONT)


def set_cell_margins(cell, *, top: int, start: int, bottom: int, end: int) -> None:
    cell_properties = cell._tc.get_or_add_tcPr()
    margins = cell_properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        cell_properties.append(margins)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def remove_table_borders(table) -> None:
    table_properties = table._tbl.tblPr
    borders = table_properties.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table_properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "nil")


def set_table_geometry(table, widths_twips: tuple[int, ...]) -> None:
    total = sum(widths_twips)
    table.autofit = False
    properties = table._tbl.tblPr
    width = properties.first_child_found_in("w:tblW")
    if width is None:
        width = OxmlElement("w:tblW")
        properties.insert(0, width)
    width.set(qn("w:w"), str(total))
    width.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for value in widths_twips:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(value))
        grid.append(column)
    for row in table.rows:
        for cell, value in zip(row.cells, widths_twips, strict=True):
            cell.width = Cm(value / 567)
            properties = cell._tc.get_or_add_tcPr()
            cell_width = properties.first_child_found_in("w:tcW")
            if cell_width is None:
                cell_width = OxmlElement("w:tcW")
                properties.append(cell_width)
            cell_width.set(qn("w:w"), str(value))
            cell_width.set(qn("w:type"), "dxa")


def add_bottom_border(paragraph, *, color: str = ACCENT, size: str = "8") -> None:
    paragraph_properties = paragraph._p.get_or_add_pPr()
    borders = paragraph_properties.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        paragraph_properties.append(borders)
    bottom = borders.find(qn("w:bottom"))
    if bottom is None:
        bottom = OxmlElement("w:bottom")
        borders.append(bottom)
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)


def configure_paragraph(
    paragraph,
    *,
    before: float = 0,
    after: float = 0,
    line: float = 1.0,
    keep_with_next: bool = False,
) -> None:
    formatting = paragraph.paragraph_format
    formatting.space_before = Pt(before)
    formatting.space_after = Pt(after)
    formatting.line_spacing_rule = WD_LINE_SPACING.SINGLE
    formatting.line_spacing = line
    formatting.keep_with_next = keep_with_next
    formatting.widow_control = True


def add_section_heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    configure_paragraph(paragraph, before=4, after=3, keep_with_next=True)
    run = paragraph.add_run(text)
    set_run_font(run, size=SECTION_PT, bold=True, color=ACCENT)
    add_bottom_border(paragraph)


def add_resume_bullet(
    document: Document,
    segments: tuple[tuple[str, bool], ...],
) -> None:
    paragraph = document.add_paragraph(style="List Bullet")
    formatting = paragraph.paragraph_format
    formatting.left_indent = Cm(0.48)
    formatting.first_line_indent = Cm(-0.24)
    configure_paragraph(paragraph, after=1.2, line=0.98)
    for text, bold in segments:
        run = paragraph.add_run(text)
        set_run_font(run, size=BODY_PT, bold=bold)


def configure_document_styles(document: Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.font.size = Pt(BODY_PT)
    normal._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), CHINESE_FONT)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0
    bullet = document.styles["List Bullet"]
    bullet.font.name = LATIN_FONT
    bullet.font.size = Pt(BODY_PT)
    bullet._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
    bullet._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    bullet._element.rPr.rFonts.set(qn("w:eastAsia"), CHINESE_FONT)


def add_header_block(document: Document, photo_path: Path) -> None:
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    set_table_geometry(table, (8400, 1806))
    remove_table_borders(table)
    left, right = table.rows[0].cells
    set_cell_margins(left, top=0, start=0, bottom=0, end=120)
    set_cell_margins(right, top=0, start=60, bottom=0, end=0)
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    name_paragraph = left.paragraphs[0]
    configure_paragraph(name_paragraph, after=1, keep_with_next=True)
    name = name_paragraph.add_run("李旭日")
    set_run_font(name, size=NAME_PT, bold=True, color=TEXT)

    direction_paragraph = left.add_paragraph()
    configure_paragraph(direction_paragraph, after=2, keep_with_next=True)
    direction = direction_paragraph.add_run("Agent 开发")
    set_run_font(direction, size=11, bold=True, color=ACCENT)

    contact_paragraph = left.add_paragraph()
    configure_paragraph(contact_paragraph)
    contact = contact_paragraph.add_run("手机：15829368085    邮箱：2832502076@qq.com")
    set_run_font(contact, size=BODY_PT, color=TEXT)

    photo_paragraph = right.paragraphs[0]
    photo_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    configure_paragraph(photo_paragraph)
    photo_paragraph.add_run().add_picture(str(photo_path), width=Cm(2.2))


def add_education(document: Document) -> None:
    add_section_heading(document, "教育背景")
    first = document.add_paragraph()
    configure_paragraph(first, after=1.2, keep_with_next=True)
    tabs = first.paragraph_format.tab_stops
    tabs.add_tab_stop(Cm(5.4))
    tabs.add_tab_stop(Cm(12.8))
    for text in (
        "2022.09–2026.06",
        "\t陕西师范大学",
        "\t计算机科学与技术（本科）",
    ):
        run = first.add_run(text)
        set_run_font(run, size=BODY_PT, bold=True, color=ACCENT)

    second = document.add_paragraph()
    configure_paragraph(second, after=1.5)
    for text, bold in (
        ("成绩排名：", True),
        ("前 30%  ｜  ", False),
        ("绩点：", True),
        ("3.60/4  ｜  ", False),
        ("主修课程：", True),
        ("数据结构、操作系统、计算机网络、人工智能", False),
    ):
        run = second.add_run(text)
        set_run_font(run, size=BODY_PT, bold=bold)


def add_project(document: Document) -> None:
    add_section_heading(document, "项目经历")
    title = document.add_paragraph()
    configure_paragraph(title, after=0.8, keep_with_next=True)
    run = title.add_run("Adaptive Milestone Planner")
    set_run_font(run, size=PROJECT_TITLE_PT, bold=True, color=TEXT)

    stack = document.add_paragraph()
    configure_paragraph(stack, after=1.6, keep_with_next=True)
    label = stack.add_run("技术栈：")
    set_run_font(label, size=SMALL_PT, bold=True, color=MUTED)
    values = stack.add_run(
        "Python、SQLite、LLM Structured Output、JSON Schema、Multi-Agent Routing、"
        "DAG、Human-in-the-loop、Cron"
    )
    set_run_font(values, size=SMALL_PT, color=MUTED)

    for bullet in PROJECT_BULLETS:
        add_resume_bullet(document, bullet)


def add_skills(document: Document) -> None:
    add_section_heading(document, "专业技能")
    for bullet in SKILL_BULLETS:
        add_resume_bullet(document, bullet)


def build_resume(output_path: Path, photo_path: Path) -> Path:
    output_path = Path(output_path)
    photo_path = Path(photo_path)
    if not photo_path.is_file():
        raise FileNotFoundError(f"resume photo not found: {photo_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(PAGE_MARGIN_CM)
    section.bottom_margin = Cm(PAGE_MARGIN_CM)
    section.left_margin = Cm(PAGE_MARGIN_CM)
    section.right_margin = Cm(PAGE_MARGIN_CM)
    section.header_distance = Cm(0.6)
    section.footer_distance = Cm(0.6)

    document.core_properties.title = "李旭日 - Agent 开发实习简历"
    document.core_properties.subject = "Agent 开发实习"
    document.core_properties.author = ""
    document.core_properties.last_modified_by = ""

    configure_document_styles(document)
    add_header_block(document, photo_path)
    add_education(document)
    add_project(document)
    add_skills(document)

    document.save(output_path)
    return output_path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output = root / "artifacts" / "resume_layout" / "李旭日-Agent开发实习-简历.docx"
    photo = root / "artifacts" / "resume_layout" / "source_package" / "word" / "media" / "image2.jpeg"
    build_resume(output, photo)
    print(output)


if __name__ == "__main__":
    main()
