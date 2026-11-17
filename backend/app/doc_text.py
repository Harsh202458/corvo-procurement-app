"""Plain text from an uploaded document: PDF, DOCX, TXT/MD/CSV/JSON. No disk writes."""
from __future__ import annotations

import io
import re
import zipfile


class UnsupportedDocument(ValueError):
    pass


def document_text(data: bytes, filename: str = "") -> str:
    name = filename.lower()
    if data.startswith(b"%PDF") or name.endswith(".pdf"):
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            parts = []
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
                for t in page.extract_tables() or []:  # tables as pipe rows, so limits in tables survive
                    parts.append("\n".join(" | ".join((c or "").strip() for c in row) for row in t))
            return _clean("\n".join(parts))
    if data.startswith(b"PK") or name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, KeyError):
            raise UnsupportedDocument("not a readable .docx file")
        xml = re.sub(r"</w:p>|<w:br/>|</w:tr>", "\n", xml)
        xml = re.sub(r"</w:tc>", " | ", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        import html

        return _clean(html.unescape(text))
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return _clean(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocument("could not read the document as text")


def _clean(s: str) -> str:
    s = s.replace("\r\n", "\n").replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
