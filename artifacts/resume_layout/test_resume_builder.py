from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from zipfile import ZipFile

from docx import Document

from build_resume import build_resume


ROOT = Path(__file__).resolve().parents[2]
PHOTO = ROOT / "artifacts" / "resume_layout" / "source_package" / "word" / "media" / "image2.jpeg"


def document_text(document: Document) -> str:
    values = [paragraph.text for paragraph in document.paragraphs]
    values.extend(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    return "\n".join(values)


class ResumeBuilderTest(TestCase):
    def test_builds_required_one_page_structure(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "resume.docx"

            result = build_resume(output, PHOTO)

            self.assertEqual(result, output)
            self.assertTrue(output.is_file())
            document = Document(output)
            self.assertEqual(len(document.sections), 1)
            section = document.sections[0]
            self.assertEqual(round(section.page_width.cm, 1), 21.0)
            self.assertEqual(round(section.page_height.cm, 1), 29.7)
            self.assertEqual(round(section.top_margin.cm, 1), 1.5)
            self.assertEqual(round(section.bottom_margin.cm, 1), 1.5)
            self.assertEqual(round(section.left_margin.cm, 1), 1.5)
            self.assertEqual(round(section.right_margin.cm, 1), 1.5)

            text = document_text(document)
            for required in (
                "李旭日",
                "Agent 开发",
                "教育背景",
                "Adaptive Milestone Planner",
                "专业技能",
                "44 项",
            ):
                self.assertIn(required, text)
            self.assertEqual(text.count("Adaptive Milestone Planner"), 1)

            list_paragraphs = [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.style is not None and paragraph.style.name == "List Bullet"
            ]
            self.assertGreaterEqual(len(list_paragraphs), 8)

            with ZipFile(output) as archive:
                xml = archive.read("word/document.xml")
                self.assertNotIn(b"txbxContent", xml)
                self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))


if __name__ == "__main__":
    main()
