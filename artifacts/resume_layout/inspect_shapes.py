from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}


def attr(node, namespace: str, name: str):
    return None if node is None else node.get(f"{{{NS[namespace]}}}{name}")


def emu_inches(value: str | None) -> float | None:
    return None if value is None else round(int(value) / 914400, 3)


def run_info(run: ET.Element) -> dict[str, object]:
    props = run.find("w:rPr", NS)
    fonts = props.find("w:rFonts", NS) if props is not None else None
    size = props.find("w:sz", NS) if props is not None else None
    color = props.find("w:color", NS) if props is not None else None
    return {
        "text": "".join(node.text or "" for node in run.findall("w:t", NS)),
        "font_ascii": attr(fonts, "w", "ascii"),
        "font_east_asia": attr(fonts, "w", "eastAsia"),
        "size_pt": (int(attr(size, "w", "val")) / 2 if attr(size, "w", "val") else None),
        "color": attr(color, "w", "val"),
        "bold": props.find("w:b", NS) is not None if props is not None else False,
    }


def main() -> None:
    source = Path(sys.argv[1])
    with zipfile.ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        rel_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in rel_root
            if rel.get("Id") and rel.get("Target")
        }
        shapes = []
        for index, anchor in enumerate(root.findall(".//wp:anchor", NS)):
            pos_h = anchor.find("wp:positionH", NS)
            pos_v = anchor.find("wp:positionV", NS)
            off_h = pos_h.find("wp:posOffset", NS) if pos_h is not None else None
            off_v = pos_v.find("wp:posOffset", NS) if pos_v is not None else None
            extent = anchor.find("wp:extent", NS)
            doc_pr = anchor.find("wp:docPr", NS)
            runs = [run_info(run) for run in anchor.findall(".//w:r", NS)]
            image = anchor.find(".//a:blip", NS)
            rid = attr(image, "r", "embed")
            shapes.append(
                {
                    "index": index,
                    "name": doc_pr.get("name") if doc_pr is not None else None,
                    "description": doc_pr.get("descr") if doc_pr is not None else None,
                    "x_in": emu_inches(off_h.text if off_h is not None else None),
                    "y_in": emu_inches(off_v.text if off_v is not None else None),
                    "x_relative": pos_h.get("relativeFrom") if pos_h is not None else None,
                    "y_relative": pos_v.get("relativeFrom") if pos_v is not None else None,
                    "width_in": emu_inches(extent.get("cx") if extent is not None else None),
                    "height_in": emu_inches(extent.get("cy") if extent is not None else None),
                    "behind_doc": anchor.get("behindDoc"),
                    "wrap": [child.tag.rsplit("}", 1)[-1] for child in anchor if child.tag.startswith("{" + NS["wp"] + "}wrap")],
                    "image_target": rels.get(rid),
                    "text": "".join(item["text"] for item in runs),
                    "runs": [item for item in runs if item["text"]],
                }
            )
    payload = json.dumps({"shapes": shapes}, ensure_ascii=False, indent=2)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
