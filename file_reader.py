"""Read item lists from files staff receive: PDF, Word, Excel, CSV, text and pictures.

Everything is free and stays on the server:
  * PDF made on a computer -> its text (pypdfium2); scanned PDF pages -> picture reader (OCR)
  * Word .docx -> paragraphs and tables (python-docx)
  * Excel .xlsx / .csv -> rows (openpyxl / csv)
  * old .doc / .xls -> converted with LibreOffice first
  * pictures -> image_reader (Tesseract OCR)

Tables with a heading row (Item Code / Description / Qty / Unit Price ...) are turned into
labelled lines ("Item Code: X | Description: Y | Quantity: 3 | Unit Price: 5,000"), which the
item reader understands exactly, so S/N or total columns are never mistaken for quantities.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
from pathlib import Path

import image_reader
import pdf_export

MAX_PDF_PAGES = 10
PICTURES = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff"}
ACCEPTED = sorted(PICTURES | {"pdf", "docx", "doc", "xlsx", "xlsm", "xls", "csv", "txt"})


class FileReadError(Exception):
    """A file that could not be read; the message is shown to staff."""


# ---------------------------------------------------------------------------
# Tables -> labelled lines
# ---------------------------------------------------------------------------
def _column_role(heading: str) -> str | None:
    h = re.sub(r"[^a-z ]", " ", str(heading or "").lower())
    h = re.sub(r"\s+", " ", h).strip()
    if not h:
        return None
    if h in {"s n", "sn", "s no", "no", "serial", "serial no", "item no", "line"}:
        return "skip"
    if "avail" in h or "delivery" in h or "lead time" in h:
        return "avail"
    if any(w in h for w in ("model", "code", "part no", "part number", "sku")):
        return "model"
    if "qty" in h or "quantit" in h or h in {"units", "no of units"}:
        return "qty"
    if "total" in h or "amount" in h:
        return "total"
    if "unit" in h and ("price" in h or "cost" in h) or h in {"rate", "price", "cost", "unit price", "unit cost"}:
        return "unit"
    if "price" in h or "cost" in h:
        return "unit"
    if any(w in h for w in ("desc", "item", "product", "particular", "name", "material")):
        return "desc"
    return None


_LABELS = {"model": "Item Code", "desc": "Description", "qty": "Quantity", "unit": "Unit Price",
           "total": "Total", "avail": "Availability"}
_SUMMARY_ROW = re.compile(
    r"^\s*(sub\s*total|grand\s*total|total|(?:[\d.]+\s*%\s*)?vat|discount|amount in words)\b", re.I)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"
    if isinstance(value, int):
        return f"{value:,}"
    return re.sub(r"\s+", " ", str(value)).strip()


def table_to_lines(rows: list[list]) -> list[str]:
    """Rows of cells -> item lines. Uses the heading row when there is one."""
    rows = [[_cell(c) for c in row] for row in rows]
    rows = [r for r in rows if any(r)]
    header_at, roles = None, []
    for i, row in enumerate(rows[:15]):
        guess = [_column_role(c) for c in row]
        useful = {r for r in guess if r in {"desc", "qty", "unit", "model"}}
        if len(useful) >= 2 and "desc" in useful | ({"model"} if "model" in useful else set()):
            header_at, roles = i, guess
            break
    if header_at is None:
        return ["  ".join(c for c in row if c) for row in rows]

    lines = []  # titles and client details above the table are not items
    for row in rows[header_at + 1:]:
        fields = {}
        for role, value in zip(roles, row):
            if not re.search(r"[A-Za-z0-9]", value):
                continue  # signature lines "-------", borders
            if role and role != "skip" and value and role not in fields:
                fields[role] = value
        desc = fields.get("desc", "")
        if not desc and not fields.get("model"):
            continue
        if any(_SUMMARY_ROW.match(cell) for cell in row if cell):
            continue  # SUB TOTAL / VAT / AMOUNT IN WORDS rows, wherever their label sits
        if not desc and not any(fields.get(k) for k in ("qty", "unit", "total")):
            continue  # a signature or footer row that ended up in the table
        if _SUMMARY_ROW.match(desc) or (not fields.get("qty") and not fields.get("unit") and not fields.get("total")
                                         and not fields.get("model")):
            continue  # SUB TOTAL / VAT rows, or a note under the table
        lines.append(" | ".join(f"{_LABELS[k]}: {fields[k]}" for k in _LABELS if fields.get(k)))
    return lines


_HEADING_WORDS = {
    "s", "n", "sn", "no", "item", "items", "code", "model", "models", "number", "numbers", "part", "description",
    "descriptions", "desc", "product", "qty", "quantity", "unit", "units", "price", "prices", "cost", "rate",
    "total", "amount", "availability", "availabilities", "ngn", "naira", "n", "uom", "remarks",
}
_ROLE_WORDS = [{"description", "descriptions", "desc", "product", "item"}, {"qty", "quantity"},
               {"price", "prices", "cost", "rate"}, {"model", "code"}]


def items_region(text: str) -> str:
    """In a whole document (letterhead, addresses, bank details...), keep only the item rows:
    the lines after the table heading, up to SUB TOTAL / VAT / TOTAL / AMOUNT IN WORDS."""
    lines = text.splitlines()

    def heading_only(line: str) -> bool:
        words = re.findall(r"[a-z]+", line.lower())
        return bool(words) and all(w in _HEADING_WORDS for w in words)

    i = 0
    while i < len(lines):
        if heading_only(lines[i]):
            j = i
            while j < len(lines) and (heading_only(lines[j]) or not lines[j].strip()):
                j += 1
            words = set(re.findall(r"[a-z]+", " ".join(lines[i:j]).lower()))
            if sum(bool(words & group) for group in _ROLE_WORDS) >= 2:
                body = []
                for line in lines[j:]:
                    if _SUMMARY_ROW.match(line):
                        break
                    body.append(line)
                if any(re.search(r"\d", line) for line in body):
                    return "\n".join(body)
            i = j
        i += 1
    return text


# ---------------------------------------------------------------------------
# Each kind of file
# ---------------------------------------------------------------------------
def _page_picture(page):
    """The scanned picture on a PDF page, at its original quality when possible."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        pictures = list(page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)))
        if len(pictures) == 1:  # one scan filling the page: use it as it is
            image = pictures[0].get_bitmap(render=False).to_pil()
            if image.width >= 600:
                return image
    except (pdfium.PdfiumError, AttributeError, ValueError):
        pass
    # otherwise draw the page large enough for letters to be clear (~2400 px wide)
    width_points = page.get_width() or 595
    return page.render(scale=max(2.0, min(4.0, 2400 / width_points))).to_pil()


def _pdf(data: bytes) -> str:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise FileReadError("This PDF could not be opened (it may be damaged or password-protected).") from exc
    texts, scanned_pages = [], 0
    for i in range(min(len(pdf), MAX_PDF_PAGES)):
        page = pdf[i]
        text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n")
        if len(re.sub(r"\s", "", text)) >= 20:
            texts.append(text)
            continue
        # no real text on the page: it is a scan or photo, so read it like a picture
        scanned_pages += 1
        if not image_reader.available():
            raise FileReadError("This PDF is a scan. Reading scans works on the online app (it needs Tesseract).")
        texts.append(image_reader.read_image(_page_picture(page)))
    if len(pdf) > MAX_PDF_PAGES:
        texts.append(f"(Only the first {MAX_PDF_PAGES} of {len(pdf)} pages were read.)")
    return "\n".join(texts)


def _docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several types for a bad file
        raise FileReadError("This Word file could not be opened.") from exc
    lines, item_tables = [], []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t")).strip()
            if text:
                lines.append(text)
        elif tag == "tbl":
            table = docx.table.Table(child, document)
            rows = []
            for row in table.rows:
                cells, previous = [], None
                for cell in row.cells:  # a merged cell repeats: blank the repeats so columns stay aligned
                    cells.append("" if cell._tc is previous else cell.text)
                    previous = cell._tc
                rows.append(cells)
            table_lines = table_to_lines(rows)
            item_tables.extend(line for line in table_lines if line.startswith(("Item Code:", "Description:")))
            lines.extend(table_lines)
    if not item_tables:
        # converted or older files may keep tables inside frames or other tables: look everywhere
        for element in body.iter():
            if element.tag.endswith("}tbl") and element.getparent() is not body:
                table_lines = table_to_lines(_table_rows(docx.table.Table(element, document)))
                item_tables.extend(line for line in table_lines if line.startswith(("Item Code:", "Description:")))
    # a document with an items table (a quotation, LPO...): only its item rows matter
    return "\n".join(item_tables) if item_tables else "\n".join(lines)


def _table_rows(table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:  # a merged cell repeats: blank the repeats so columns stay aligned
            cells.append("" if cell._tc is previous else cell.text)
            previous = cell._tc
        rows.append(cells)
    return rows


def _xlsx(data: bytes) -> str:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises several types for a bad file
        raise FileReadError("This Excel file could not be opened.") from exc
    lines = []
    for sheet in book.worksheets:
        rows = [list(r) for r in sheet.iter_rows(values_only=True, max_row=500)]
        lines.extend(table_to_lines(rows))
    return "\n".join(lines)


def _csv(data: bytes) -> str:
    text = _text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return "\n".join(table_to_lines(list(csv.reader(io.StringIO(text), dialect))))


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _convert_old(data: bytes, ext: str, target: str) -> bytes:
    """Old .doc / .xls -> .docx / .xlsx with LibreOffice."""
    exe = pdf_export.soffice_path()
    if not exe:
        raise FileReadError(f"Old .{ext} files can be read on the online app. Or save it as .{target} first.")
    with tempfile.TemporaryDirectory(prefix="rq_old_") as folder:
        work = Path(folder)
        source = work / f"file.{ext}"
        source.write_bytes(data)
        profile = (work / "profile").resolve().as_uri()
        try:
            subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                            "--convert-to", target, "--outdir", str(work), str(source)],
                           capture_output=True, timeout=120, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise FileReadError(f"This .{ext} file could not be converted.") from exc
        result = work / f"file.{target}"
        if not result.exists():
            raise FileReadError(f"This .{ext} file could not be converted.")
        return result.read_bytes()


def read_file(name: str, data: bytes) -> str:
    """Text of the items in a file. Raises FileReadError with a message for staff."""
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in PICTURES:
        try:
            return image_reader.read_picture(data)
        except RuntimeError as exc:
            raise FileReadError(str(exc)) from exc
        except OSError as exc:
            raise FileReadError("This picture could not be opened.") from exc
    if ext == "pdf":
        text = items_region(_pdf(data))
    elif ext == "docx":
        text = _docx(data)
    elif ext == "doc":
        text = _docx(_convert_old(data, "doc", "docx"))
    elif ext in ("xlsx", "xlsm"):
        text = _xlsx(data)
    elif ext == "xls":
        text = _xlsx(_convert_old(data, "xls", "xlsx"))
    elif ext == "csv":
        text = _csv(data)
    elif ext == "txt":
        text = items_region(_text(data))
    else:
        raise FileReadError(f"Files of type .{ext} cannot be read. Use PDF, Word, Excel, CSV, text or a picture.")
    return text.strip()
