# File: ~/.config/orkesai/modules/notes_export.py
"""Render a scope's notes to downloadable documents — PDF, DOCX, XLSX, CSV —
using only the stdlib (no reportlab/openpyxl/python-docx). Consumed by
agent_service.export_notes() → GET /api/notes/export."""
import csv
import io
import re
import textwrap
import time
import zipfile

FORMATS = ("pdf", "docx", "xlsx", "csv")

_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def render(notes: list, fmt: str, scope: str = "", name: str = "") -> tuple:
    """(data_bytes, mime, filename) for the given format. `name` names the file
    after a single exported note (its own title is the heading, so the document
    heading is dropped). Raises ValueError on an unknown format so the API can
    400 instead of 500."""
    fmt = str(fmt or "").strip().lower()
    if fmt not in FORMATS:
        raise ValueError(f"unknown format '{fmt}' — use one of: {', '.join(FORMATS)}")
    heading = ("" if name else
               "OrkesAI notes" + (f" — {scope}" if scope and not scope.startswith("session:") else ""))
    data = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "csv": _csv}[fmt](notes, heading)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", name or scope).strip("-") or "notes"
    return data, _MIME[fmt], f"orkesai-note{'' if name else 's'}-{safe[:60]}.{fmt}"


def _ts(epoch) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(epoch)))
    except (TypeError, ValueError, OverflowError):
        return ""


def _clean(s) -> str:
    # control chars (other than \n and \t) are invalid in XML and useless in PDF
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s or ""))


# ── CSV ──────────────────────────────────────────────────────────────────────

def _csv(notes: list, heading: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Title", "Note", "Source", "Created", "Updated"])
    for n in notes:
        w.writerow([_clean(n.get("title")), _clean(n.get("body")),
                    n.get("source", ""), _ts(n.get("created")), _ts(n.get("updated"))])
    # BOM so Excel opens it as UTF-8 instead of mangling accents
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


# ── XLSX (minimal OOXML spreadsheet, inline strings) ─────────────────────────

def _x(s: str) -> str:
    return (_clean(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _xlsx(notes: list, heading: str) -> bytes:
    cols = "ABCDE"

    def row(r, values):
        cells = "".join(
            f'<c r="{cols[i]}{r}" t="inlineStr"><is><t xml:space="preserve">{_x(v)}</t></is></c>'
            for i, v in enumerate(values))
        return f'<row r="{r}">{cells}</row>'

    rows = [row(1, ["Title", "Note", "Source", "Created", "Updated"])]
    for i, n in enumerate(notes, start=2):
        rows.append(row(i, [n.get("title", ""), n.get("body", ""), n.get("source", ""),
                            _ts(n.get("created")), _ts(n.get("updated"))]))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<cols><col min="1" max="1" width="34" customWidth="1"/>'
             '<col min="2" max="2" width="90" customWidth="1"/>'
             '<col min="3" max="5" width="17" customWidth="1"/></cols>'
             f'<sheetData>{"".join(rows)}</sheetData></worksheet>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Notes" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')
    return _zip({"[Content_Types].xml": types, "_rels/.rels": rels,
                 "xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": wb_rels,
                 "xl/worksheets/sheet1.xml": sheet})


# ── DOCX (minimal OOXML word document) ───────────────────────────────────────

def _docx(notes: list, heading: str) -> bytes:
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []

    def para(text, bold=False, size=None, color=None, before=0, after=0):
        rpr = ""
        if bold:
            rpr += "<w:b/>"
        if color:
            rpr += f'<w:color w:val="{color}"/>'
        if size:
            rpr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        spacing = f'<w:pPr><w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
        return (f'<w:p>{spacing}<w:r>{rpr}'
                f'<w:t xml:space="preserve">{_x(text)}</w:t></w:r></w:p>')

    if heading:
        body.append(para(heading, bold=True, size="32", after=120))
    for n in notes:
        body.append(para(n.get("title") or "Note", bold=True, size="26", before=240, after=40))
        meta = " · ".join(x for x in (n.get("source", ""), _ts(n.get("updated"))) if x)
        if meta:
            body.append(para(meta, size="16", color="888888", after=60))
        for line in (_clean(n.get("body")).split("\n") or [""]):
            body.append(para(line))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{W}"><w:body>{"".join(body)}</w:body></w:document>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    return _zip({"[Content_Types].xml": types, "_rels/.rels": rels,
                 "word/document.xml": doc})


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


# ── PDF (minimal hand-built PDF, Helvetica) ──────────────────────────────────

_PAGE_W, _PAGE_H, _MARGIN = 612, 792, 54  # US Letter, 3/4" margins


def _pdf_esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf(notes: list, heading: str) -> bytes:
    # 1. flatten notes into (font, size, leading, text) lines
    lines = [("F2", 15, 24, heading)] if heading else []
    for n in notes:
        first = True
        for w in textwrap.wrap(_clean(n.get("title")) or "Note", 66) or ["Note"]:
            lines.append(("F2", 12, 22 if first else 16, w))
            first = False
        meta = " · ".join(x for x in (n.get("source", ""), _ts(n.get("updated"))) if x)
        if meta:
            lines.append(("F1", 8, 12, meta))
        for raw in _clean(n.get("body")).split("\n"):
            for w in (textwrap.wrap(raw, 92) or [""]):
                lines.append(("F1", 10, 14, w))
    # 2. paginate top-down
    pages, cur, y = [], [], _PAGE_H - _MARGIN
    for font, size, lead, text in lines:
        if y - lead < _MARGIN:
            pages.append(cur)
            cur, y = [], _PAGE_H - _MARGIN
        y -= lead
        if text:
            cur.append((font, size, y, text))
    if cur or not pages:
        pages.append(cur)
    # 3. objects: 1 catalog · 2 pages · 3 F1 · 4 F2 · then (content, page) pairs
    kids = " ".join(f"{6 + 2 * i} 0 R" for i in range(len(pages)))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    for i, page in enumerate(pages):
        stream = "".join(
            f"BT /{font} {size} Tf 1 0 0 1 {_MARGIN} {yy} Tm ({_pdf_esc(text)}) Tj ET\n"
            for font, size, yy, text in page).encode("cp1252", errors="replace")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                     f"/Contents {5 + 2 * i} 0 R >>").encode())
    # 4. assemble with xref
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write((f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
               f"startxref\n{xref}\n%%EOF\n").encode())
    return out.getvalue()
