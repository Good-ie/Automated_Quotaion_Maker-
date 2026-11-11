"""Core quotation logic: pricing, history and Word document generation.

Kept free of Streamlit so it can be tested and reused on its own.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Pt, Twips
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docxtpl import DocxTemplate
from num2words import num2words

COMPANY_PREFIX = "RSL"

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@dataclass
class Pricing:
    markup_pct: float = 0.0
    discount_pct: float = 0.0
    vat_enabled: bool = False
    vat_pct: float = 7.5


@dataclass
class QuoteOption:
    title: str
    lines: list[dict] = field(default_factory=list)  # priced lines for the document
    subtotal: float = 0.0
    discount: float = 0.0
    vat: float = 0.0
    total: float = 0.0

    @property
    def after_discount(self) -> float:
        return self.subtotal - self.discount


def price_option(title: str, rows: list[dict], pricing: Pricing) -> QuoteOption:
    """Apply markup per unit (rounded to kobo so unit x qty always matches the line total)."""
    opt = QuoteOption(title=title)
    for row in rows:
        desc = str(row.get("Description") or "").strip()
        model = str(row.get("Model no.") or "").strip()
        if not desc and not model:
            continue
        qty = int(row.get("Qty") or 0)
        base = float(row.get("Unit cost (₦)") or 0.0)
        unit = round(base * (1 + pricing.markup_pct / 100), 2)
        line_total = round(unit * qty, 2)
        opt.lines.append(
            {
                "sn": len(opt.lines) + 1,
                "model": model,
                "desc": desc,
                "qty": qty,
                "unit": unit,
                "total": line_total,
                "availability": str(row.get("Availability") or "").strip(),
            }
        )
        opt.subtotal += line_total
    opt.subtotal = round(opt.subtotal, 2)
    opt.discount = round(opt.subtotal * pricing.discount_pct / 100, 2)
    opt.vat = round(opt.after_discount * pricing.vat_pct / 100, 2) if pricing.vat_enabled else 0.0
    opt.total = round(opt.after_discount + opt.vat, 2)
    return opt


def naira(amount: float) -> str:
    return f"{amount:,.2f}"


def amount_in_words(amount: float) -> str:
    naira_part = int(amount)
    kobo = int(round((amount - naira_part) * 100))
    if kobo == 100:
        naira_part, kobo = naira_part + 1, 0
    words = num2words(naira_part, lang="en").title() + " Naira"
    if kobo:
        words += ", " + num2words(kobo, lang="en").title() + " Kobo"
    return words.replace(" And ", " and ") + " Only"


def format_date(d: dt.date) -> str:
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {d.strftime('%B, %Y')}"


# ---------------------------------------------------------------------------
# Quote history and reference numbers
# ---------------------------------------------------------------------------


def reference_prefix(initials: str, on: dt.date) -> str:
    return f"{COMPANY_PREFIX}/{initials.strip().upper() or 'XX'}/{on:%Y/%m}/"


def next_reference(initials: str, on: dt.date, used_refs) -> str:
    """RSL/<initials>/<YYYY>/<MM>/<NNN>, numbered per staff member per month."""
    prefix = reference_prefix(initials, on)
    used = [
        int(ref.rsplit("/", 1)[-1])
        for ref in used_refs
        if ref.upper().startswith(prefix) and ref.rsplit("/", 1)[-1].isdigit()
    ]
    return f"{prefix}{(max(used) + 1) if used else 1:03d}"


# ---------------------------------------------------------------------------
# Saving a quotation as data, and rebuilding its document later
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if value is None or value != value:  # None or NaN (empty cell)
        return ""
    return str(value).strip()


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number


def clean_rows(rows: list[dict]) -> list[dict]:
    """Plain, JSON-safe copies of the reviewed item rows (empty rows dropped)."""
    out = []
    for row in rows:
        item = {
            "Model no.": _text(row.get("Model no.")),
            "Description": _text(row.get("Description")),
            "Qty": int(_number(row.get("Qty"), 1)),
            "Unit cost (₦)": _number(row.get("Unit cost (₦)")),
            "Availability": _text(row.get("Availability")),
            "Note": _text(row.get("Note")),
        }
        if item["Description"] or item["Model no."]:
            out.append(item)
    return out


def quote_payload(header: dict, quote_date: dt.date, pricing: Pricing, titles: list[str], option_rows: list[list[dict]]) -> dict:
    return {
        "version": 1,
        "header": header,
        "quote_date": quote_date.isoformat(),
        "pricing": asdict(pricing),
        "options": [{"title": t, "rows": clean_rows(r)} for t, r in zip(titles, option_rows)],
    }


def options_from_payload(payload: dict) -> tuple[list[QuoteOption], Pricing]:
    pricing = Pricing(**payload["pricing"])
    return [price_option(o["title"], o["rows"], pricing) for o in payload["options"]], pricing


def rebuild_quotation(template: bytes | Path, payload: dict) -> tuple[bytes, list[str]]:
    """Recreate the Word document of a saved quotation."""
    options, pricing = options_from_payload(payload)
    return build_quotation(template, payload["header"], options, pricing)


# ---------------------------------------------------------------------------
# Word document generation
# ---------------------------------------------------------------------------

_ALIGN = [
    WD_ALIGN_PARAGRAPH.CENTER,  # S/N
    WD_ALIGN_PARAGRAPH.CENTER,  # model
    WD_ALIGN_PARAGRAPH.LEFT,  # description
    WD_ALIGN_PARAGRAPH.RIGHT,  # unit cost
    WD_ALIGN_PARAGRAPH.CENTER,  # qty
    WD_ALIGN_PARAGRAPH.RIGHT,  # total
    WD_ALIGN_PARAGRAPH.CENTER,  # availability
]


def _set_cell(cell, text: str, align, bold: bool = False) -> None:
    cell.text = ""
    para = cell.paragraphs[0]
    para.alignment = align
    fmt = para.paragraph_format  # same tight spacing in every item table
    fmt.space_before = Pt(1)
    fmt.space_after = Pt(1)
    fmt.line_spacing = 1.0
    run = para.add_run(text)
    run.bold = bold


def _shade(cell, fill_hex: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tc_pr.append(shd)


def _row_flag(row, tag: str) -> None:
    """Add a row property such as w:tblHeader (repeat on each page) or w:cantSplit."""
    order = [
        "w:cnfStyle", "w:divId", "w:gridBefore", "w:gridAfter", "w:wBefore", "w:wAfter",
        "w:cantSplit", "w:trHeight", "w:tblHeader", "w:tblCellSpacing", "w:jc", "w:hidden",
    ]
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn(tag)) is not None:
        return
    new = OxmlElement(tag)
    later = {qn(t) for t in order[order.index(tag) + 1 :]}
    for child in tr_pr:
        if child.tag in later:  # Word requires this child order
            child.addprevious(new)
            return
    tr_pr.append(new)


def _add_total_row(table, label: str, amount: str, highlight: bool = False) -> None:
    row = table.add_row()
    _row_flag(row, "w:cantSplit")
    cells = row.cells
    label_cell = cells[0].merge(cells[4])
    _set_cell(label_cell, label, WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    _set_cell(cells[5], amount, WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    _set_cell(cells[6], "", WD_ALIGN_PARAGRAPH.CENTER)
    if highlight:
        for c in (label_cell, cells[5], cells[6]):
            _shade(c, "D1FAE5")  # emerald-100, matches resypl.com


def _fill_table(table, opt: QuoteOption, pricing: Pricing) -> None:
    _row_flag(table.rows[0], "w:tblHeader")
    for line in opt.lines:
        row = table.add_row()
        _row_flag(row, "w:cantSplit")
        values = [
            str(line["sn"]),
            line["model"] or "-",
            line["desc"],
            naira(line["unit"]),
            str(line["qty"]),
            naira(line["total"]),
            line["availability"] or "-",
        ]
        for cell, value, align in zip(row.cells, values, _ALIGN):
            _set_cell(cell, value, align)

    has_adjustments = opt.discount > 0 or pricing.vat_enabled
    if has_adjustments:
        _add_total_row(table, "SUB TOTAL", naira(opt.subtotal))
    if opt.discount > 0:
        _add_total_row(table, f"DISCOUNT ({pricing.discount_pct:g}%)", f"-{naira(opt.discount)}")
        if pricing.vat_enabled:
            _add_total_row(table, "TOTAL AFTER DISCOUNT", naira(opt.after_discount))
    if pricing.vat_enabled:
        _add_total_row(table, f"VAT ({pricing.vat_pct:g}%)", naira(opt.vat))
    _add_total_row(table, "GRAND TOTAL", naira(opt.total), highlight=True)

    # keep the last item and the whole totals block on the same page
    n_totals = 1 + int(has_adjustments) + (2 if opt.discount > 0 and pricing.vat_enabled else int(opt.discount > 0)) + int(pricing.vat_enabled)
    for row in table.rows[-(n_totals + 1) : -1]:
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.keep_with_next = True


def _item_tables(document) -> list:
    tables = []
    for t in document.tables:
        if t.rows and len(t.rows[0].cells) >= 7 and "DESCRIPTION" in t.rows[0].cells[2].text.upper():
            tables.append(t)
    return tables


# Column share of the page width: S/N, model, description, unit cost, qty, total, availability
_COLUMN_SHARES = [620, 1250, 3400, 1450, 600, 1600, 1880]
_OPTION2_MARKER = "⁣OPTION2_REMOVE⁣"  # invisible marker for Option 2 text to delete


def _text_width_twips(document) -> int:
    section = document.sections[-1]
    return int((section.page_width - section.left_margin - section.right_margin) / 635)


def _fit_table(table, total_twips: int) -> None:
    """Make an item table exactly as wide as the page text area, centred."""
    widths = [round(total_twips * s / sum(_COLUMN_SHARES)) for s in _COLUMN_SHARES]
    widths[2] += total_twips - sum(widths)  # absorb rounding in the description column
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(total_twips))
    tbl_w.set(qn("w:type"), "dxa")
    ind = tbl_pr.find(qn("w:tblInd"))
    if ind is not None:
        tbl_pr.remove(ind)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for col, w in zip(tbl.tblGrid.findall(qn("w:gridCol")), widths):
        col.set(qn("w:w"), str(w))
    for tr in tbl.tr_lst:
        idx = 0
        for tc in tr.tc_lst:
            span = tc.grid_span
            tc.width = Twips(sum(widths[idx : idx + span]))
            idx += span


def _remove(element) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _is_empty_paragraph(el) -> bool:
    if el is None or el.tag != qn("w:p"):
        return False
    has_text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
    has_drawing = any(True for _ in el.iter(qn("w:drawing"))) or any(True for _ in el.iter(qn("w:pict")))
    return not has_text and not has_drawing


def _remove_option_2(document, option2_table) -> None:
    """Delete the 'OPTION 2' heading, its table and its amount-in-words line."""
    for p in document.paragraphs:
        if p.text.strip().upper().startswith("OPTION 2"):
            prev = p._p.getprevious()
            _remove(p._p)
            if _is_empty_paragraph(prev):  # the old {% if %} line
                _remove(prev)
            break
    if option2_table is not None:
        _remove(option2_table._tbl)
    for table in document.tables:
        for cell in (c for row in table.rows for c in row.cells):
            for p in list(cell.paragraphs):
                if _OPTION2_MARKER in p.text:
                    nxt = p._p.getnext()
                    _remove(p._p)
                    if _is_empty_paragraph(nxt):  # the old {% endif %} line
                        _remove(nxt)
                    # drop blank lines left at the top of that box
                    tc = cell._tc
                    while True:
                        first = next((c for c in tc if c.tag != qn("w:tcPr")), None)
                        following = first.getnext() if first is not None else None
                        if _is_empty_paragraph(first) and following is not None and following.tag == qn("w:p"):
                            _remove(first)
                        else:
                            break


def _fix_reference_line(document, ref: str, date_text: str, width_twips: int) -> None:
    """Reference on the left, date on the right, always on one line."""
    for p in document.paragraphs:
        if ref in p.text and date_text in p.text:
            runs = p.runs
            if len(runs) < 3:
                return
            runs[0].text, runs[1].text, runs[2].text = ref, "\t", date_text
            for r in runs[3:]:
                r.text = ""
            indent = p.paragraph_format.left_indent or 0
            p.paragraph_format.tab_stops.add_tab_stop(
                Twips(width_twips - int(indent / 635)), WD_TAB_ALIGNMENT.RIGHT
            )
            return


def _fit_layout_tables(document, width_twips: int, item_tables) -> None:
    """Shrink any other template table (e.g. the terms box) that is wider than the page."""
    item_ids = {id(t._tbl) for t in item_tables}
    for table in document.tables:
        tbl = table._tbl
        if id(tbl) in item_ids:
            continue
        tbl_w = tbl.tblPr.find(qn("w:tblW"))
        cols = tbl.tblGrid.findall(qn("w:gridCol"))
        current = sum(int(c.get(qn("w:w")) or 0) for c in cols)
        if not current or current <= width_twips:
            continue
        scale = width_twips / current
        widths = [int(int(c.get(qn("w:w")) or 0) * scale) for c in cols]
        for c, w in zip(cols, widths):
            c.set(qn("w:w"), str(w))
        if tbl_w is not None:
            tbl_w.set(qn("w:w"), str(width_twips))
            tbl_w.set(qn("w:type"), "dxa")
        for tr in tbl.tr_lst:
            idx = 0
            for tc in tr.tc_lst:
                span = tc.grid_span
                tc.width = Twips(sum(widths[idx : idx + span]))
                idx += span


def _tidy_closing(document) -> None:
    """Keep the sign-off, signature and bank box together; drop stray template text."""
    for p in document.paragraphs:
        text = p.text.strip()
        if text.startswith(("Yours faithfully", "For Rehoboth", "OFFER VALIDITY")):
            p.paragraph_format.keep_with_next = True
    # keep the terms block (payment, validity, delivery, warranty, note) on one page
    for table in document.tables:
        cell_text = table.rows[0].cells[0].text.upper() if table.rows else ""
        if "PAYMENT TERM" in cell_text:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        p.paragraph_format.keep_with_next = True
        if "DELIVERY" in cell_text and "WARRANTY" in cell_text:
            for row in table.rows:
                _row_flag(row, "w:cantSplit")
    def removable(p_el) -> bool:
        # Word requires every table cell to END with a paragraph, so never remove a
        # paragraph whose previous sibling is not another paragraph (e.g. a nested table).
        prev = p_el.getprevious()
        return prev is not None and prev.tag == qn("w:p")

    for table in document.tables:
        for cell in (c for row in table.rows for c in row.cells):
            last = cell._tc.findall(qn("w:p"))
            if last and last[-1] is cell._tc[-1] and last[-1].getprevious() is not None:
                text = "".join(t.text or "" for t in last[-1].iter(qn("w:t"))).strip().upper()
                if text == "DELIVERY":  # stray copy of the DELIVERY label
                    if removable(last[-1]):
                        _remove(last[-1])
                    else:
                        for t in last[-1].iter(qn("w:t")):
                            t.text = ""
            # trim empty lines at the bottom of the box
            while True:
                tail = cell._tc[-1]
                if tail.tag == qn("w:p") and _is_empty_paragraph(tail) and removable(tail):
                    _remove(tail)
                else:
                    break


def build_quotation(
    template: bytes | Path,
    header: dict,
    options: list[QuoteOption],
    pricing: Pricing,
) -> tuple[bytes, list[str]]:
    """Render the template and return (docx bytes, warnings)."""
    warnings = []
    tpl = DocxTemplate(BytesIO(template) if isinstance(template, bytes) else str(template))
    opt1 = options[0]
    opt2 = options[1] if len(options) > 1 else None
    context = {
        **header,
        "proposal_title_1": opt1.title,
        "amount_in_words_1": amount_in_words(opt1.total),
        # Always keep the template's Option 2 block while rendering: its {% if %} starts
        # outside a table and its {% endif %} sits inside one, so letting it remove the
        # block corrupts the rest of the document (terms, signature, bank details).
        # Unused Option 2 parts are deleted afterwards instead.
        "enable_option_2": True,
        "proposal_title_2": opt2.title if opt2 else "",
        "amount_in_words_2": amount_in_words(opt2.total) if opt2 else _OPTION2_MARKER,
    }
    tpl.render(context)
    document: docx.document.Document = tpl.docx
    width = _text_width_twips(document)

    tables = _item_tables(document)
    if not tables:
        warnings.append(
            "No item table was found in the template. It needs a 7-column table with "
            "'DESCRIPTIONS' as the third header."
        )
    for i, opt in enumerate(options):
        if i < len(tables):
            _fill_table(tables[i], opt, pricing)
            _fit_table(tables[i], width)
        elif tables:
            warnings.append(f"Option {i + 1} was not added: the template has no table for it.")
    if opt2 is None:
        _remove_option_2(document, tables[1] if len(tables) > 1 else None)

    _fit_layout_tables(document, width, tables)
    _fix_reference_line(document, header["ref_number"], header["date"], width)
    _tidy_closing(document)

    bio = BytesIO()
    document.save(bio)
    return bio.getvalue(), warnings


def check_template(data: bytes) -> list[str]:
    """Problems that would stop a .docx from working as the quotation template."""
    try:
        document = docx.Document(BytesIO(data))
    except Exception:  # python-docx raises several types for a bad file
        return ["This is not a valid Word (.docx) file."]
    problems = []
    text = " ".join(p.text for p in document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            text += " " + " ".join(c.text for c in row.cells)
    tables = _item_tables(document)
    if not tables:
        problems.append("No item table found: it needs a 7-column table with 'DESCRIPTIONS' as the third header.")
    for tag in ("ref_number", "client_name", "amount_in_words_1"):
        if tag not in text:
            problems.append(f"The placeholder {{{{ {tag} }}}} is missing.")
    return problems


def safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "Client"
