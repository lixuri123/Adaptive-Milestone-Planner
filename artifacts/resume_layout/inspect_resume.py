from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document


def twips(value) -> int | None:
    return None if value is None else int(value.twips)


def main() -> None:
    source = Path(sys.argv[1])
    document = Document(source)
    result: dict[str, object] = {
        "source": str(source),
        "paragraphs": [],
        "tables": [],
        "sections": [],
        "inline_shapes": len(document.inline_shapes),
    }

    paragraphs = result["paragraphs"]
    assert isinstance(paragraphs, list)
    for index, paragraph in enumerate(document.paragraphs):
        paragraphs.append(
            {
                "index": index,
                "style": paragraph.style.name if paragraph.style else None,
                "alignment": str(paragraph.alignment),
                "text": paragraph.text,
                "runs": [
                    {
                        "text": run.text,
                        "bold": run.bold,
                        "italic": run.italic,
                        "font": run.font.name,
                        "size_pt": run.font.size.pt if run.font.size else None,
                        "color": (
                            str(run.font.color.rgb)
                            if run.font.color is not None and run.font.color.rgb is not None
                            else None
                        ),
                    }
                    for run in paragraph.runs
                ],
            }
        )

    tables = result["tables"]
    assert isinstance(tables, list)
    for table_index, table in enumerate(document.tables):
        rows = []
        for row_index, row in enumerate(table.rows):
            rows.append(
                {
                    "index": row_index,
                    "cells": [
                        {
                            "text": cell.text,
                            "paragraphs": [
                                {
                                    "style": paragraph.style.name if paragraph.style else None,
                                    "alignment": str(paragraph.alignment),
                                    "text": paragraph.text,
                                    "runs": [
                                        {
                                            "text": run.text,
                                            "bold": run.bold,
                                            "font": run.font.name,
                                            "size_pt": run.font.size.pt if run.font.size else None,
                                        }
                                        for run in paragraph.runs
                                    ],
                                }
                                for paragraph in cell.paragraphs
                            ],
                        }
                        for cell in row.cells
                    ],
                }
            )
        tables.append(
            {
                "index": table_index,
                "style": table.style.name if table.style else None,
                "rows": rows,
            }
        )

    sections = result["sections"]
    assert isinstance(sections, list)
    for index, section in enumerate(document.sections):
        sections.append(
            {
                "index": index,
                "page_width_twips": twips(section.page_width),
                "page_height_twips": twips(section.page_height),
                "top_margin_twips": twips(section.top_margin),
                "bottom_margin_twips": twips(section.bottom_margin),
                "left_margin_twips": twips(section.left_margin),
                "right_margin_twips": twips(section.right_margin),
                "header_distance_twips": twips(section.header_distance),
                "footer_distance_twips": twips(section.footer_distance),
            }
        )

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
