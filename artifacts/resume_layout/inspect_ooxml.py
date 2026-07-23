from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"


def main() -> None:
    source = Path(sys.argv[1])
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        parts = {}
        for name in names:
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            texts = [node.text or "" for node in root.iter(f"{W}t")]
            if texts:
                parts[name] = {
                    "text": "".join(texts),
                    "text_nodes": texts,
                    "textboxes": sum(1 for _ in root.iter(f"{W}txbxContent")),
                    "vml_shapes": sum(1 for _ in root.iter(f"{V}shape")),
                    "anchors": sum(1 for _ in root.iter(f"{WP}anchor")),
                    "inline": sum(1 for _ in root.iter(f"{WP}inline")),
                }

        result = {
            "parts": parts,
            "media": [name for name in names if name.startswith("word/media/")],
            "embeddings": [name for name in names if name.startswith("word/embeddings/")],
            "custom_xml": [name for name in names if name.startswith("customXml/")],
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
