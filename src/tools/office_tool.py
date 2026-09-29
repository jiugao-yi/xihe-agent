"""Office tool — offline docx/xlsx read+write (openpyxl / python-docx), the
deterministic file-deliverable channel: gateway chats deliver with
send_file, desktop lands them in the workspace (relative paths are
workspace-resolved like write_file's)."""

import importlib.util
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from tools import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_ACTIONS = ("excel_write", "excel_read", "word_write", "word_read")

# Column auto-width bounds for excel_write.
_COL_MIN, _COL_MAX = 8, 48


def _check() -> bool:
    return (importlib.util.find_spec("openpyxl") is not None
            or importlib.util.find_spec("docx") is not None
            or importlib.util.find_spec("pypdf") is not None
            or importlib.util.find_spec("fitz") is not None)


def _resolve(path: str, kw: dict) -> Path:
    from core.support.paths import resolve_path
    return Path(str(resolve_path(path, kw.get("parent_agent"))))


def _cell_out(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, Decimal):
        return float(v)
    return v


def _display_width(s: str) -> int:
    # CJK glyphs count double toward column width.
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _excel_write(args: dict, kw: dict) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return tool_error("openpyxl is not installed — excel unavailable in this environment")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required (e.g. report.xlsx; relative = workspace)")
    sheets = args.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        return tool_error("sheets must be a non-empty array")

    out = _resolve(path, kw)
    if out.suffix.lower() != ".xlsx":
        out = out.with_suffix(".xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    wb.remove(wb.active)
    for i, sh in enumerate(sheets):
        name = str((sh or {}).get("name") or f"Sheet{i + 1}")
        rows = (sh or {}).get("rows")
        if not isinstance(rows, list):
            return tool_error(f"sheet '{name}': rows must be an array of arrays")
        ws = wb.create_sheet(title=name[:31])
        for row in rows:
            if not isinstance(row, list):
                return tool_error(f"sheet '{name}': each row must be an array")
            ws.append([_cell_out(v) for v in row])
        if (sh or {}).get("header") and rows:
            for c in ws[1]:
                c.font = Font(bold=True)
            ws.freeze_panes = "A2"
        for col in ws.columns:
            width = max((_display_width(str(c.value or "")) for c in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, _COL_MIN), _COL_MAX)
    wb.save(out)

    logger.info("office [excel_write] -> %s", out)
    return tool_result(success=True, action="excel_write", path=str(out),
                       sheets=len(sheets))


def _excel_read(args: dict, kw: dict) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return tool_error("openpyxl is not installed — excel unavailable in this environment")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required")
    p = _resolve(path, kw)
    if not p.exists():
        return tool_error(f"File not found: {p}")

    try:
        max_rows = int(args.get("max_rows") or 200)
    except (TypeError, ValueError):
        max_rows = 200
    max_rows = max(1, min(max_rows, 5000))

    wb = load_workbook(p, data_only=True, read_only=True)
    sheet = args.get("sheet")
    if sheet is None:
        ws = wb.active
    elif isinstance(sheet, int):
        ws = wb.worksheets[sheet] if -len(wb.worksheets) <= sheet < len(wb.worksheets) else None
    else:
        ws = wb[sheet] if str(sheet) in wb.sheetnames else None
    if ws is None:
        return tool_error(f"sheet not found: {sheet} (available: {wb.sheetnames})")

    rows, total = [], 0
    for row in ws.iter_rows(values_only=True):
        total += 1
        if len(rows) < max_rows:
            rows.append([_cell_out(v) for v in row])
    wb.close()

    return tool_result(success=True, action="excel_read", path=str(p),
                       sheet=ws.title, total_rows=total, returned_rows=len(rows),
                       truncated=total > len(rows), rows=rows)


def _word_write(args: dict, kw: dict) -> str:
    try:
        from docx import Document
    except ImportError:
        return tool_error("python-docx is not installed — word unavailable in this environment")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required (e.g. report.docx; relative = workspace)")
    blocks = args.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return tool_error("blocks must be a non-empty array")

    out = _resolve(path, kw)
    if out.suffix.lower() != ".docx":
        out = out.with_suffix(".docx")
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    for b in blocks:
        b = b or {}
        kind = str(b.get("type") or "paragraph")
        if kind == "heading":
            try:
                level = max(1, min(int(b.get("level") or 1), 4))
            except (TypeError, ValueError):
                level = 1
            doc.add_heading(str(b.get("text") or ""), level=level)
        elif kind == "bullet":
            for item in ([b.get("text")] if b.get("text") else b.get("items") or []):
                doc.add_paragraph(str(item), style="List Bullet")
        elif kind == "table":
            rows = b.get("rows")
            if not isinstance(rows, list) or not rows:
                return tool_error("table block requires rows (array of arrays)")
            t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
            t.style = "Table Grid"
            for ri, row in enumerate(rows):
                for ci, val in enumerate(row):
                    cell = t.cell(ri, ci)
                    cell.text = str(val)
                    if ri == 0 and b.get("header", True):
                        for run in cell.paragraphs[0].runs or [cell.paragraphs[0].add_run("")]:
                            run.font.bold = True
        else:
            doc.add_paragraph(str(b.get("text") or ""))

    doc.save(out)
    logger.info("office [word_write] -> %s", out)
    return tool_result(success=True, action="word_write", path=str(out),
                       blocks=len(blocks))


def _read_legacy_doc(path: Path, max_chars: int) -> tuple[str, bool]:
    """Extract text from a Word 97-2003 .doc (OLE2 compound document).

    Pure-python via olefile: the document text lives in the WordDocument
    stream, stored as UTF-16LE or CP1252 runs. Rather than reimplementing
    the fragile FIB/piece-table walk, best-effort scan the stream for
    printable runs — plenty for reading prose back. RTF / HTML files that
    masquerade as .doc are sniffed and handled directly.
    """
    import olefile

    raw = path.read_bytes()
    head = raw[:1024].lstrip()

    # Some "old Word" files are actually RTF or single-file HTML with a .doc
    # extension — handle those before the OLE2 path.
    if head.lower().startswith(b"{\\rtf"):
        text = _strip_rtf(raw.decode("latin-1", "replace"))
        if text.strip():
            return _truncate_text(text, max_chars)
    if b"<html" in raw[:4096].lower():
        from html.parser import HTMLParser

        class _TextParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []

            def handle_data(self, data):
                if data.strip():
                    self.parts.append(data.strip())

        parser = _TextParser()
        try:
            parser.feed(raw.decode("utf-8", "replace"))
        except Exception:
            pass
        text = "\n".join(parser.parts)
        if text.strip():
            return _truncate_text(text, max_chars)

    if not olefile.isOleFile(path):
        raise ValueError("not an OLE2 compound document (no real .doc either)")
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists("WordDocument"):
            raise ValueError(".doc missing WordDocument stream")
        data = ole.openstream("WordDocument").read()
    finally:
        ole.close()

    text = _scan_utf16_text(data)
    ansi = _scan_ansi_text(data)
    # UTF-16 misreading ANSI text yields dense CJK-noise with almost no
    # whitespace; real text (CJK or Latin) has visible space/newline ratio.
    # Pick ANSI when it looks like spaced words and UTF-16 does not.
    if ansi and _ws_ratio(ansi) > 0.05 and _ws_ratio(ansi) > _ws_ratio(text) * 2:
        text = ansi
    if not text.strip():
        raise ValueError("no readable text found in WordDocument stream")
    return _truncate_text(text, max_chars)


def _ws_ratio(t: str) -> float:
    if not t:
        return 0.0
    return sum(1 for c in t if c in " \n\t") / len(t)


def _scan_utf16_text(data: bytes) -> str:
    """Collect printable UTF-16LE runs (>=4 chars) from a binary stream."""
    runs: list[str] = []
    cur: list[str] = []
    n = len(data)
    i = 0
    while i + 1 < n:
        code = data[i] | (data[i + 1] << 8)
        i += 2
        if code in (0x0D, 0x0A, 0x09):
            if len(cur) >= 4:
                runs.append("".join(cur))
            cur = []
            continue
        if 0x20 <= code <= 0x7E or (code >= 0x80 and not (0xD800 <= code <= 0xDFFF)
                                    and not 0xE000 <= code <= 0xF8FF):
            cur.append(chr(code))
        else:
            if len(cur) >= 4:
                runs.append("".join(cur))
            cur = []
    if len(cur) >= 4:
        runs.append("".join(cur))
    return "\n".join(runs)


def _scan_ansi_text(data: bytes) -> str:
    """Collect printable CP1252/latin-1 runs (>=6 chars) from a binary stream."""
    runs: list[str] = []
    cur: list[str] = []
    for b in data:
        if b in (0x0D, 0x0A, 0x09) or 0x20 <= b < 0x7F or b >= 0xA0:
            cur.append(chr(b))
        else:
            if len(cur) >= 6:
                runs.append("".join(cur))
            cur = []
    if len(cur) >= 6:
        runs.append("".join(cur))
    return "\n".join(runs)


def _strip_rtf(rtf: str) -> str:
    """Minimal RTF-to-text: drop control words/params/groups, keep text runs."""
    out: list[str] = []
    i, n = 0, len(rtf)
    while i < n:
        c = rtf[i]
        if c == "\\":
            j = i + 1
            if j < n and rtf[j] == "'":      # \'hh hex escape
                i = j + 3
                continue
            while j < n and rtf[j].isalpha():
                j += 1
            while j < n and (rtf[j].isdigit() or rtf[j] == "-"):
                j += 1
            if j < n and rtf[j] == " ":
                j += 1
            i = j
            continue
        if c in "{}":
            i += 1
            continue
        out.append(c)
        i += 1
    return "\n".join(line.strip() for line in "".join(out).splitlines() if line.strip())


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _pdf_read(args: dict, kw: dict) -> str:
    """office_read kind="pdf": 提取 PDF 文本（逐页，markdown 化分段）。

    只读——与 office_read 同档进 base，plan 模式可用。纯文字 PDF 提取
    文本；扫描件/图片页无文本层，返回空 + has_images（agent 用
    pdf_to_images 渲染成图后自行决定识别方式）。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return tool_error("pypdf is not installed — pdf unavailable in this environment")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required")
    p = _resolve(path, kw)
    if not p.exists():
        return tool_error(f"File not found: {p}")
    try:
        max_chars = int(args.get("max_chars") or 40000)
    except (TypeError, ValueError):
        max_chars = 40000
    max_chars = max(500, min(max_chars, 200000))

    try:
        reader = PdfReader(str(p))
        total_pages = len(reader.pages)
        parts = []
        total = 0
        truncated = False
        for i in range(min(total_pages, 200)):
            text = (reader.pages[i].extract_text() or "").strip()
            if not text:
                continue
            if total + len(text) > max_chars:
                truncated = True
                break
            parts.append(f"## 第 {i + 1} 页\n{text}")
            total += len(text)
        if not parts:
            # 无文本层（扫描件/纯图页）——不点名工具，只陈述"页面需渲染为
            # 图片"，识别方式交给 agent 自行决定。
            return tool_result(success=True, action="pdf_read", path=str(p),
                               text="", truncated=False,
                               has_images=True, pages=total_pages,
                               note="PDF 无文本层（扫描件或图片型页面）。"
                                    "可先把页面渲染为图片（pdf_to_images），"
                                    "再对图片自行决定识别方式")
    except Exception as e:
        return tool_error(f"cannot read pdf '{p}': {e}")
    return tool_result(success=True, action="pdf_read", path=str(p),
                       text="\n\n".join(parts), truncated=truncated,
                       pages=total_pages)


def _pdf_to_images(args: dict, kw: dict) -> str:
    """pdf_to_images: PDF 页渲染成 PNG（只读，进 base）。把"无文本层的
    页面"变成图片文件清单——后续用哪个图片工具由 agent 判断，本工具只
    陈述"第 N 页渲染到了哪个路径"。"""
    try:
        import fitz  # pymupdf
    except ImportError:
        return tool_error("pymupdf is not installed — pdf rendering unavailable")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required")
    p = _resolve(path, kw)
    if not p.exists():
        return tool_error(f"File not found: {p}")

    try:
        first = max(1, int(args.get("first_page") or 1))
        last = max(first, int(args.get("last_page") or first + 19))
    except (TypeError, ValueError):
        first, last = 1, 20
    last = first + max(0, last - first)  # 段上限最多 20 页/次，防爆
    last = min(last, first + 19)
    try:
        dpi = max(72, min(int(args.get("dpi") or 150), 400))
    except (TypeError, ValueError):
        dpi = 150

    from core.config import AGENT_HOME
    out_dir = Path(AGENT_HOME) / "charts" / f"pdf_{p.stem}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(p))
        total = doc.page_count
        last = min(last, total)
        images: list[dict] = []
        for i in range(first - 1, last):
            page = doc[i]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out = out_dir / f"page_{i + 1:03d}.png"
            pix.save(str(out))
            images.append({"page": i + 1, "path": str(out)})
        doc.close()
    except Exception as e:
        return tool_error(f"cannot render pdf '{p}': {e}")
    return tool_result(success=True, action="pdf_to_images", path=str(p),
                       pages=total, images=images)


def _word_read(args: dict, kw: dict) -> str:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        return tool_error("python-docx is not installed — word unavailable in this environment")

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required")
    p = _resolve(path, kw)
    if not p.exists():
        return tool_error(f"File not found: {p}")

    try:
        max_chars = int(args.get("max_chars") or 20000)
    except (TypeError, ValueError):
        max_chars = 20000
    max_chars = max(500, min(max_chars, 100000))

    # Legacy Word 97-2003 .doc (OLE2) — extract via olefile before the
    # python-docx path, which only understands .docx.
    if p.suffix.lower() == ".doc":
        try:
            text, truncated = _read_legacy_doc(p, max_chars)
        except Exception as e:
            return tool_error(f"cannot read legacy .doc '{p}': {e}")
        return tool_result(success=True, action="word_read", path=str(p),
                           text=text, truncated=truncated)

    doc = Document(str(p))
    lines = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            if not para.text.strip():
                continue
            if para.style.name.startswith("Heading"):
                try:
                    level = int(para.style.name.split()[-1])
                except ValueError:
                    level = 2
                lines.append("#" * max(1, min(level, 4)) + " " + para.text)
            elif para.style.name == "List Bullet":
                lines.append("- " + para.text)
            else:
                lines.append(para.text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            for row in table.rows:
                lines.append("| " + " | ".join(c.text.strip() for c in row.cells) + " |")
    text = "\n".join(lines)
    truncated = len(text) > max_chars
    return tool_result(success=True, action="word_read", path=str(p),
                       text=text[:max_chars], truncated=truncated)


def _office_read(args: dict, **kw) -> str:
    """office_read 入口：excel_read / word_read / pdf_read 只读动作。与
    _office 拆开注册——读能力进 base（plan 模式可用），写动作留在 office
    工具集。"""
    kind = str(args.get("kind") or "word").lower()
    if kind == "excel":
        return _excel_read(args, kw)
    if kind == "word":
        return _word_read(args, kw)
    if kind == "pdf":
        return _pdf_read(args, kw)
    return tool_error("kind must be 'word', 'excel' or 'pdf'")


def _office(args: dict, **kw) -> str:
    action = str(args.get("action") or "").lower()
    if action not in _ACTIONS:
        return tool_error(f"action must be one of: {', '.join(_ACTIONS)}")
    if action == "excel_write":
        return _excel_write(args, kw)
    if action == "word_write":
        return _word_write(args, kw)
    if action == "excel_read":
        return _excel_read(args, kw)
    return _word_read(args, kw)


_OFFICE_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "office_read",
        "description": (
            "Read office documents offline (xlsx via openpyxl, docx via "
            "python-docx, legacy .doc via olefile, pdf via pypdf) — excel "
            "rows back as JSON, word/pdf as markdown-ish text. READ-ONLY "
            "(no file mutation), so it is available in plan mode and on the "
            "base floor. For writing office artifacts use the 'office' tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["word", "excel", "pdf"],
                         "description": "word = .docx/.doc → text; excel = .xlsx → rows; "
                                        "pdf = text extraction (scan-only PDFs have no "
                                        "text layer — use image_ocr on pages instead)"},
                "path": {"type": "string",
                         "description": "File path (.xlsx/.docx/.doc/.pdf); relative = workspace"},
                "sheet": {"type": "string",
                          "description": "excel: sheet name or index (default active)"},
                "max_rows": {"type": "integer",
                             "description": "excel: row cap (default 200)"},
                "max_chars": {"type": "integer",
                              "description": "word/pdf: text cap (default word 20000 / pdf 40000)"},
                "max_pages": {"type": "integer",
                              "description": "pdf: page cap (default 100)"},
            },
            "required": ["kind", "path"],
        },
    },
}

registry.register(
    name="office_read",
    schema=_OFFICE_READ_SCHEMA,
    handler=lambda args, **kw: _office_read(args, **kw),
    check_fn=_check,
    toolset="office",
)

_PDF_IMAGES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "pdf_to_images",
        "description": (
            "Render PDF pages to PNG images (read-only). For scan-type PDFs "
            "that have no text layer (office_read returns has_images), "
            "rendering each page to an image lets the model then decide how "
            "to read it (OCR / vision) — this tool only states which page "
            "rendered to which path, never how to consume them. Images land "
            "under AGENT_HOME/charts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "PDF file path"},
                "first_page": {"type": "integer", "description": "First page (1-based, default 1)"},
                "last_page": {"type": "integer",
                              "description": "Last page inclusive (default first+19, ≤20 pages/call)"},
                "dpi": {"type": "integer", "description": "Render resolution (default 150, 72-400)"},
            },
            "required": ["path"],
        },
    },
}

registry.register(
    name="pdf_to_images",
    schema=_PDF_IMAGES_SCHEMA,
    handler=lambda args, **kw: _pdf_to_images(args, **kw),
    check_fn=_check,
    toolset="office",
)

registry.register(
    name="office",
    schema={
        "type": "function",
        "function": {
            "name": "office",
            "description": (
                "Create office documents offline (xlsx via openpyxl, docx "
                "via python-docx) — the deterministic way to deliver real "
                "file artifacts (reports, data exports, memos). Gateway "
                "chats deliver the file with send_file(file_path=...); on "
                "desktop a relative path lands in the conversation's "
                "workspace. Actions: excel_write (sheets of rows, optional "
                "header styling + column autofit) and word_write "
                "(heading/paragraph/bullet/table blocks). To READ office "
                "files use the 'office_read' tool (available even in plan "
                "mode); this tool only creates/mutates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["excel_write", "word_write"]},
                    "path": {"type": "string",
                             "description": "File path (.xlsx/.docx); relative = workspace"},
                    "sheets": {
                        "type": "array",
                        "description": "excel_write: [{name, rows: [[...]], header?: bool}]",
                        "items": {"type": "object"},
                    },
                    "blocks": {
                        "type": "array",
                        "description": "word_write: [{type: heading|paragraph|bullet|table, "
                                       "text, level, items, rows, header}]",
                        "items": {"type": "object"},
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=lambda args, **kw: _office(args, **kw),
    check_fn=_check,
    toolset="office",
)
