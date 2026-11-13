"""Invoices and waybills (delivery notes), laid out like the company's paper forms.

The documents are drawn from scratch with python-docx (no template needed). Company
details (address, phones, TIN, bank) are passed in, so the admin can change them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from io import BytesIO

import docx
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor, Twips
from num2words import num2words

RED = "B83A26"          # table headers and totals, as on the paper invoice
GREEN = RGBColor(0x2E, 0x5E, 0x1E)
BLUE = RGBColor(0x1F, 0x4E, 0xD8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BODY_FONT = "Calibri"

DEFAULT_COMPANY = {
    "name": "Rehoboth Systems and Project Limited",
    "address": "21, Shasha Road, Ishiwo Bus stop Shasha Akowonjo Lagos.",
    "phones": "08022256518, 08093607273, 09068562560",
    "email": "info@resypl.com",
    "tin": "24280259-0001",
    "account_name": "REHOBOTH SYSTEMS AND PROJECT LIMITED",
    "bank": "ZENITH INTERNATIONAL BANK",
    "bank_address": "CAMPBEL BRANCH LAGOS ISLAND",
    "account_number": "1224641552",
    "signatory": "For: Rehoboth Systems Ltd",
}


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
@dataclass
class InvoiceLine:
    model: str
    qty: int
    desc: str
    unit: float  # client price (after markup)

    @property
    def total(self) -> float:
        return round(self.qty * self.unit, 2)


@dataclass
class Invoice:
    number: str
    date: dt.date
    terms: str
    lpo_number: str
    lpo_date: str
    attention: str
    client_name: str
    client_address: str
    lines: list[InvoiceLine]
    discount_pct: float = 0.0
    extra_discount: float = 0.0  # optional fixed naira amount off, after the % discount
    vat_enabled: bool = True
    vat_pct: float = 7.5
    # part payment: leave both empty for a full invoice
    part_percent: float | None = None   # e.g. 60 -> 60% of the contract total
    part_amount: float | None = None    # or a fixed naira amount
    previously_invoiced: float = 0.0    # earlier part invoices for the same job
    totals: dict = field(default_factory=dict, init=False)

    def compute(self) -> dict:
        subtotal = round(sum(line.total for line in self.lines), 2)
        discount = round(subtotal * self.discount_pct / 100, 2)
        extra = round(min(max(self.extra_discount or 0.0, 0.0), subtotal - discount), 2)
        after_discount = round(subtotal - discount - extra, 2)
        vat = round(after_discount * self.vat_pct / 100, 2) if self.vat_enabled else 0.0
        contract_total = round(after_discount + vat, 2)
        if self.part_percent:
            due = round(contract_total * self.part_percent / 100, 2)
        elif self.part_amount:
            due = round(self.part_amount, 2)
        else:
            due = round(contract_total - self.previously_invoiced, 2)
        balance = round(contract_total - self.previously_invoiced - due, 2)
        self.totals = {
            "subtotal": subtotal, "discount": discount, "extra_discount": extra, "after_discount": after_discount,
            "vat": vat, "contract_total": contract_total, "due": due, "balance": balance,
        }
        return self.totals

    @property
    def is_part(self) -> bool:
        return bool(self.part_percent or self.part_amount or self.previously_invoiced)

    def to_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "totals"}
        out["date"] = self.date.isoformat()
        out["lines"] = [line.__dict__ for line in self.lines]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Invoice":
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "totals"}
        values["date"] = dt.date.fromisoformat(data["date"])
        values["lines"] = [InvoiceLine(**line) for line in data["lines"]]
        return cls(**values)


def extract_signature(template: bytes) -> bytes | None:
    """Find the signature picture inside the quotation template: a small, wide image."""
    import zipfile

    from PIL import Image

    try:
        archive = zipfile.ZipFile(BytesIO(template))
    except zipfile.BadZipFile:
        return None
    best = None
    for name in archive.namelist():
        if not name.startswith("word/media/") or not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        data = archive.read(name)
        try:
            width, height = Image.open(BytesIO(data)).size
        except OSError:
            continue
        if len(data) < 60_000 and height and width / height >= 3 and (best is None or len(data) < len(best)):
            best = data
    return best


def naira(amount: float) -> str:
    return f"{amount:,.2f}"


def words(amount: float) -> str:
    """EIGHT HUNDRED AND FIFTY ONE THOUSAND, FOUR HUNDRED NAIRA ONLY"""
    naira_part = int(amount)
    kobo = int(round((amount - naira_part) * 100))
    if kobo == 100:
        naira_part, kobo = naira_part + 1, 0
    text = num2words(naira_part, lang="en") + " naira"
    if kobo:
        text += ", " + num2words(kobo, lang="en") + " kobo"
    return (text + " only").replace("-", " ").upper()


def format_date(d: dt.date) -> str:
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {d:%B %Y}"


# ---------------------------------------------------------------------------
# Word helpers
# ---------------------------------------------------------------------------
# Word files must list table settings in a fixed order. Word forgives mistakes;
# LibreOffice (used to make PDFs) does not, so insert each setting in its place.
_TBL_PR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
                 "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar",
                 "tblLook", "tblCaption", "tblDescription"]
_TC_PR_ORDER = ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar",
                "textDirection", "tcFitText", "vAlign", "hideMark"]


def _put(parent, child, order: list[str]) -> None:
    """Insert child into parent in schema order, replacing an existing one of the same kind."""
    name = child.tag.split("}")[1]
    for old in parent.findall(qn(f"w:{name}")):
        parent.remove(old)
    position = order.index(name)
    for i, existing in enumerate(list(parent)):
        tag = existing.tag.split("}")[1]
        if tag in order and order.index(tag) > position:
            existing.addprevious(child)
            return
    parent.append(child)


def _borders(table, outer: int = 12, inner: int = 4, color: str = "000000") -> None:
    tbl_pr = table._tbl.tblPr
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge, size in (("top", outer), ("left", outer), ("bottom", outer), ("right", outer),
                       ("insideH", inner), ("insideV", inner)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single" if size else "nil")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    _put(tbl_pr, borders, _TBL_PR_ORDER)


def _cell_lines(document) -> None:
    """Copy each table's border settings onto its cells.

    LibreOffice (used to make the PDFs) ignores borders set only on the whole table, so
    every cell gets its own top/left/bottom/right lines: thick on the table's outside,
    thin (or none) inside, exactly as the table settings say.
    """
    for tbl in document.element.body.iter(qn("w:tbl")):
        tbl_borders = tbl.tblPr.find(qn("w:tblBorders"))
        if tbl_borders is None:
            continue
        spec = {el.tag.split("}")[1]: (el.get(qn("w:val")), el.get(qn("w:sz"))) for el in tbl_borders}
        n_cols = len(tbl.tblGrid.findall(qn("w:gridCol")))
        rows = tbl.findall(qn("w:tr"))
        for r, tr in enumerate(rows):
            col = 0
            for tc in tr.findall(qn("w:tc")):
                tc_pr = tc.get_or_add_tcPr()
                span_el = tc_pr.find(qn("w:gridSpan"))
                span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
                edges = {
                    "top": spec["top"] if r == 0 else spec["insideH"],
                    "left": spec["left"] if col == 0 else spec["insideV"],
                    "bottom": spec["bottom"] if r == len(rows) - 1 else spec["insideH"],
                    "right": spec["right"] if col + span >= n_cols else spec["insideV"],
                }
                borders = OxmlElement("w:tcBorders")
                for edge, (val, size) in edges.items():
                    el = OxmlElement(f"w:{edge}")
                    el.set(qn("w:val"), val)
                    el.set(qn("w:sz"), size)
                    el.set(qn("w:space"), "0")
                    el.set(qn("w:color"), "000000")
                    borders.append(el)
                _put(tc_pr, borders, _TC_PR_ORDER)
                col += span


def _fill(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    _put(tc_pr, shd, _TC_PR_ORDER)


def _widths(table, twips: list[int]) -> None:
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    _put(tbl_pr, layout, _TBL_PR_ORDER)
    grid = table._tbl.tblGrid
    for col, width in zip(grid.findall(qn("w:gridCol")), twips):
        col.set(qn("w:w"), str(width))
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Twips(twips[min(i, len(twips) - 1)])


def _write(cell, text: str = "", *, bold=False, size=9, align="left", color=None, font=None,
           italic=False, underline=False, new_paragraph=False):
    para = cell.add_paragraph() if new_paragraph else cell.paragraphs[0]
    para.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                      "right": WD_ALIGN_PARAGRAPH.RIGHT}[align]
    fmt = para.paragraph_format
    fmt.space_before = Pt(1)
    fmt.space_after = Pt(1)
    fmt.line_spacing = 1.0
    run = para.add_run(text)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    run.font.size = Pt(size)
    run.font.name = font or BODY_FONT
    if color is not None:
        run.font.color.rgb = color
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    return run


def _row_height(row, twips: int) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(twips))
    h.set(qn("w:hRule"), "atLeast")
    tr_pr.append(h)


def _spacer(document, points: float = 2) -> None:
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = Pt(points)


def _new_document():
    document = docx.Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Twips(11906), Twips(16838)  # A4
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Inches(0.55))
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    style = document.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(9)
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = 1.0
    return document


WIDTH = 11906 - 2 * 792  # A4 width minus 0.55" margins, in twips


# ---------------------------------------------------------------------------
# Shared blocks
# ---------------------------------------------------------------------------
def _letterhead(document, company: dict, logo: bytes | None) -> None:
    t = document.add_table(rows=1, cols=2)
    _borders(t, outer=12, inner=0)
    _widths(t, [WIDTH - 2700, 2700])
    left, right = t.rows[0].cells
    _write(left, company["name"], bold=True, size=22, align="center", color=GREEN, font="Times New Roman")
    _write(left, company["address"], bold=True, size=9, align="center", font="Times New Roman", new_paragraph=True)
    _write(left, f"Phone: {company['phones']}.", bold=True, size=8.5, align="center",
           font="Times New Roman", new_paragraph=True)
    _write(left, f"Email : {company['email']}", bold=True, size=10, align="center", color=BLUE,
           underline=True, new_paragraph=True)
    if logo:
        para = right.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.add_run().add_picture(BytesIO(logo), width=Twips(2550))
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _details(document, title: str, date_label: str, inv: Invoice, company: dict) -> None:
    """INVOICE / WAYBILL title, date and terms, client on the left, numbers on the right."""
    cols = [3000, 3400, 1500, WIDTH - 7900]
    address_lines = [line.strip() for line in inv.client_address.replace(",\n", "\n").splitlines() if line.strip()]
    left_lines = [inv.client_name] + address_lines
    right_rows = [("Invoice Number", inv.number), ("LPO Number", inv.lpo_number),
                  ("LPO Date", inv.lpo_date), ("TIN", company["tin"])]
    body_rows = max(len(right_rows), len(left_lines) + 1)
    t = document.add_table(rows=2 + body_rows, cols=4)
    _borders(t, outer=12, inner=4)
    _widths(t, cols)

    for r, (label, value) in enumerate([(date_label, format_date(inv.date)), (
            "Payment Terms:" if title.startswith("WAYBILL") else "Terms:", inv.terms)]):
        row = t.rows[r]
        title_cell = row.cells[0].merge(row.cells[1])
        if r == 0:
            _write(title_cell, title, bold=True, size=13, align="center")
        _write(row.cells[2], label, bold=True, size=9, align="right")
        # long payment terms ("70% with order, 30% after delivery") get a smaller size to fit the box
        _write(row.cells[3], value, size=9 if len(value) <= 24 else 7.5 if len(value) <= 48 else 6.5, align="right")

    for i in range(body_rows):
        row = t.rows[2 + i]
        if i == 0:
            _write(row.cells[0], f"ATTN TO: {inv.attention.upper()}" if inv.attention else "", bold=True, size=10)
        elif i - 1 < len(left_lines):
            _write(row.cells[0], left_lines[i - 1], size=9)
        label_cell = row.cells[1].merge(row.cells[2])
        if i < len(right_rows):
            label, value = right_rows[i]
            _write(label_cell, label, bold=True, size=9.5, align="center")
            _write(row.cells[3], value, size=9, align="right")


def _footer_signatures(document, company: dict, signature: bytes | None, bank: bool) -> None:
    t = document.add_table(rows=1, cols=3)
    _borders(t, outer=12, inner=0)
    _widths(t, [2250, WIDTH - 4400, 2150])
    left, mid, right = t.rows[0].cells
    if signature:
        para = left.paragraphs[0]
        para.add_run().add_picture(BytesIO(signature), width=Twips(1500))
    _write(left, "-" * 28, size=7, new_paragraph=True)
    _write(left, company["signatory"], bold=True, italic=True, size=8, new_paragraph=True)
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    if bank:
        _write(mid, f"ACCOUNT NAME: {company['account_name']}", bold=True, size=9.5, align="center")
        _write(mid, f"BANKER: {company['bank']}", bold=True, size=11.5, align="center", new_paragraph=True)
        _write(mid, f"ADDRESS: {company['bank_address']}", size=7.5, align="center", new_paragraph=True)
        _write(mid, f"ACCOUNT NUMBER: {company['account_number']}", bold=True, size=11.5, align="center",
               new_paragraph=True)
    else:
        _write(mid, "Received the above items in good condition", italic=True, size=9, align="center")
        _write(mid, "Name: ______________________   Date: ____________", size=9, align="center",
               new_paragraph=True)
    _write(right, "-" * 24, size=7, align="center")
    _write(right, "Client's Signature", bold=True, italic=True, size=8, align="center", new_paragraph=True)
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    _row_height(t.rows[0], 1250)


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def _total_row(t, label: str, amount: float | str, *, red=False, label_col_span=4, bold=True) -> None:
    row = t.add_row()
    label_cell = row.cells[0].merge(row.cells[label_col_span - 1])
    _write(label_cell, label, bold=bold, size=10, color=WHITE if red else None)
    _write(row.cells[4], amount if isinstance(amount, str) else naira(amount), bold=bold, size=10,
           align="right", color=WHITE if red else None)
    if red:
        _fill(label_cell, RED)
        _fill(row.cells[4], RED)


def build_invoice(inv: Invoice, company: dict | None = None, logo: bytes | None = None,
                  signature: bytes | None = None, min_rows: int = 24) -> bytes:
    company = {**DEFAULT_COMPANY, **(company or {})}
    totals = inv.compute()
    document = _new_document()
    _letterhead(document, company, logo)
    _details(document, "INVOICE", "Date:", inv, company)

    cols = [1450, 650, WIDTH - 5300, 1550, 1650]
    t = document.add_table(rows=1, cols=5)
    _borders(t, outer=12, inner=4)
    _widths(t, cols)
    for cell, text in zip(t.rows[0].cells, ["MODEL NUMBERS", "QTY", "DESCRIPTIONS", "UNIT PRICE (NGN)",
                                            "TOTAL AMOUNT (NGN)"]):
        _write(cell, text, bold=True, size=9, align="center", color=WHITE)
        _fill(cell, RED)
    for line in inv.lines:
        row = t.add_row()
        _write(row.cells[0], line.model, size=9)
        _write(row.cells[1], str(line.qty), size=9, align="center")
        _write(row.cells[2], line.desc, size=9)
        _write(row.cells[3], naira(line.unit), size=9, align="right")
        _write(row.cells[4], naira(line.total), size=9, align="right")
    for _ in range(max(0, min_rows - len(inv.lines))):  # ruled empty rows, like the paper form
        _row_height(t.add_row(), 290)

    _total_row(t, "SUB TOTAL", totals["subtotal"], red=True)
    if totals["discount"]:
        _total_row(t, f"{inv.discount_pct:g}% DISCOUNT", totals["discount"], bold=False)
    if totals["extra_discount"]:
        _total_row(t, "ADDITIONAL DISCOUNT", totals["extra_discount"], bold=False)
    if totals["discount"] or totals["extra_discount"]:
        _total_row(t, "DISCOUNTED TOTAL", totals["after_discount"], red=True)
    if inv.vat_enabled:
        _total_row(t, f"{inv.vat_pct:g}% VAT", totals["vat"], bold=False)

    if inv.is_part:
        _total_row(t, "CONTRACT TOTAL", totals["contract_total"])
        if inv.previously_invoiced:
            _total_row(t, "LESS: PREVIOUSLY INVOICED", f"-{naira(inv.previously_invoiced)}", bold=False)
        if inv.part_percent:
            due_label = f"AMOUNT DUE ({inv.part_percent:g}% PART PAYMENT)"
        elif inv.part_amount:
            due_label = "AMOUNT DUE (PART PAYMENT)"
        else:
            due_label = "AMOUNT DUE (BALANCE)"
    else:
        due_label = "TOTAL"

    row = t.add_row()  # AMOUNT IN WORDS | words ...              | TOTAL | amount
    _write(row.cells[0], "AMOUNT IN WORDS:", bold=True, size=10)
    words_cell = row.cells[1].merge(row.cells[2])
    _write(words_cell, words(totals["due"]), size=9)
    _write(row.cells[3], due_label, bold=True, size=9 if inv.is_part else 10, color=WHITE)
    _write(row.cells[4], naira(totals["due"]), bold=True, size=11, align="right", color=WHITE)
    _fill(row.cells[3], RED)
    _fill(row.cells[4], RED)
    _row_height(row, 520)

    if inv.is_part and totals["balance"] > 0:
        _total_row(t, "BALANCE REMAINING AFTER THIS INVOICE", totals["balance"], bold=False)

    _footer_signatures(document, company, signature, bank=True)
    _cell_lines(document)
    bio = BytesIO()
    document.save(bio)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# Waybill / delivery note
# ---------------------------------------------------------------------------
def build_waybill(inv: Invoice, delivery_date: dt.date, company: dict | None = None,
                  logo: bytes | None = None, signature: bytes | None = None, min_rows: int = 30) -> bytes:
    company = {**DEFAULT_COMPANY, **(company or {})}
    document = _new_document()
    _letterhead(document, company, logo)
    waybill = replace(inv, date=delivery_date)
    _details(document, "WAYBILL/DELIVERY", "Date:", waybill, company)

    cols = [1450, 650, WIDTH - 5300, 1550, 1650]  # same grid as the invoice; last two left for stamps
    t = document.add_table(rows=1, cols=5)
    _borders(t, outer=12, inner=4)
    _widths(t, cols)
    header = t.rows[0].cells
    for cell, text in zip(header, ["MODEL NUMBERS", "QTY", "DESCRIPTIONS", "", ""]):
        _write(cell, text, bold=True, size=9, align="center", color=WHITE)
        _fill(cell, RED)
    for line in inv.lines:
        row = t.add_row()
        _write(row.cells[0], line.model, size=9)
        _write(row.cells[1], str(line.qty), size=9, align="center")
        _write(row.cells[2], line.desc, size=9)
    for _ in range(max(0, min_rows - len(inv.lines))):
        _row_height(t.add_row(), 290)

    _footer_signatures(document, company, signature, bank=False)
    _cell_lines(document)
    bio = BytesIO()
    document.save(bio)
    return bio.getvalue()
