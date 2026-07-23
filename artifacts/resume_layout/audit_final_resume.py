from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

from docx import Document


ROOT = Path(__file__).resolve().parents[2]
FINAL = ROOT / "artifacts" / "resume_layout" / "李旭日-Agent开发实习-简历.docx"
SOURCE = ROOT / "artifacts" / "resume_layout" / "resume_source.docx"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if not FINAL.is_file():
        fail(f"missing final document: {FINAL}")
    if not SOURCE.is_file():
        fail(f"missing preserved source document: {SOURCE}")

    document = Document(FINAL)
    if len(document.sections) != 1:
        fail(f"expected one section, got {len(document.sections)}")
    section = document.sections[0]
    geometry = {
        "page_width": round(section.page_width.cm, 1),
        "page_height": round(section.page_height.cm, 1),
        "top_margin": round(section.top_margin.cm, 1),
        "bottom_margin": round(section.bottom_margin.cm, 1),
        "left_margin": round(section.left_margin.cm, 1),
        "right_margin": round(section.right_margin.cm, 1),
    }
    expected = {
        "page_width": 21.0,
        "page_height": 29.7,
        "top_margin": 1.5,
        "bottom_margin": 1.5,
        "left_margin": 1.5,
        "right_margin": 1.5,
    }
    if geometry != expected:
        fail(f"unexpected page geometry: {geometry}")

    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    table_text = "\n".join(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    text = f"{table_text}\n{body_text}"
    required_once = (
        "李旭日",
        "15829368085",
        "2832502076@qq.com",
        "陕西师范大学",
        "计算机科学与技术（本科）",
        "教育背景",
        "Adaptive Milestone Planner",
        "专业技能",
        "44 项",
    )
    for value in required_once:
        count = text.count(value)
        if count != 1:
            fail(f"expected one occurrence of {value!r}, got {count}")
    if "Desktop 已完成" in text:
        fail("document claims unfinished Desktop work is complete")

    bullet_count = sum(
        paragraph.style is not None and paragraph.style.name == "List Bullet"
        for paragraph in document.paragraphs
    )
    if bullet_count < 8:
        fail(f"expected at least eight real bullet paragraphs, got {bullet_count}")

    previous_blank = False
    for paragraph in document.paragraphs:
        blank = not paragraph.text.strip()
        if blank and previous_blank:
            fail("found consecutive blank body paragraphs")
        previous_blank = blank

    sizes = [
        run.font.size.pt
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.text.strip() and run.font.size is not None
    ]
    if not sizes or min(sizes) < 8.6:
        fail(f"body font below 8.6 pt: {min(sizes) if sizes else 'missing'}")

    with ZipFile(FINAL) as archive:
        names = archive.namelist()
        xml = archive.read("word/document.xml")
        if b"txbxContent" in xml:
            fail("floating text box content remains in final document")
        if not any(name.startswith("word/media/") and not name.endswith("/") for name in names):
            fail("resume photo is missing")

    print("PASS")
    print(f"final={FINAL}")
    print(f"final_sha256={sha256(FINAL)}")
    print(f"source_sha256={sha256(SOURCE)}")
    print(f"bullets={bullet_count}")
    print(f"minimum_body_size_pt={min(sizes):.1f}")
    print(f"page_geometry={geometry}")


if __name__ == "__main__":
    main()
