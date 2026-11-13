"""Checks the totals (discount, additional discount, VAT) on quotations and invoices.

Run with:  python tests/test_pricing.py
The figures are from a real Rehoboth quotation (APC Easy UPS SRV 10kVA job).
"""
import datetime as dt
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import docx  # noqa: E402

import invoice_engine as ie  # noqa: E402
import quote_engine as qe  # noqa: E402

failures: list[str] = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def table_text(data: bytes) -> list[str]:
    d = docx.Document(io.BytesIO(data))
    return [" | ".join(dict.fromkeys(c.text.strip() for c in r.cells)) for t in d.tables for r in t.rows]


ROWS = [
    {"Model no.": "SRV10KIL", "Description": "APC Easy UPS On-Line SRV 10000VA 230V", "Qty": 1, "Unit cost (₦)": 6597500},
    {"Model no.": "SRV240BP-9A", "Description": "APC Easy UPS On-Line SRV Battery Pack", "Qty": 4, "Unit cost (₦)": 2262000},
    {"Model no.": "", "Description": "Logistic, Labor and Professional Charge", "Qty": 1, "Unit cost (₦)": 150000},
]
pricing = qe.Pricing(markup_pct=0, discount_pct=5, vat_enabled=True, vat_pct=7.5, extra_discount=20000)
opt = qe.price_option("APC EASY UPS 10KVA", ROWS, pricing)
check("quotation figures match the real quotation",
      (opt.subtotal, opt.discount, opt.extra_discount, opt.after_discount, opt.vat, opt.total)
      == (15795500, 789775, 20000, 14985725, 1123929.38, 16109654.38))
check("additional discount is optional (0 = no change)",
      qe.price_option("x", ROWS, qe.Pricing(0, 5, True, 7.5)).after_discount == 15795500 - 789775)
check("additional discount can never make the total negative",
      qe.price_option("x", ROWS[2:], qe.Pricing(0, 0, False, 7.5, 999999)).total == 0)
check("quotations saved before this change still open", qe.Pricing(**{"markup_pct": 0, "discount_pct": 5,
                                                                       "vat_enabled": True, "vat_pct": 7.5}).extra_discount == 0)

template = ROOT / "template.docx"
if template.exists():
    header = {"ref_number": "RSL/MJO/2026/11/009", "date": "13th November, 2026", "client_name": "Test Client",
              "client_address": "Lagos", "attention": "Mr Test"}
    data, _ = qe.build_quotation(template, header, [opt], pricing)
    rows = table_text(data)
    labels = [r.split(" | ")[0] for r in rows if r.split(" | ")[0] in
              ("SUB TOTAL", "5% DISCOUNT", "Additional Discount", "DISCOUNTED TOTAL", "7.5% VAT", "TOTAL")]
    check(f"quotation document has the totals in the right order: {labels}",
          labels == ["SUB TOTAL", "5% DISCOUNT", "Additional Discount", "DISCOUNTED TOTAL", "7.5% VAT", "TOTAL"])
    check("quotation document shows 16,109,654.38", any("16,109,654.38" in r for r in rows))
    no_extra, _ = qe.build_quotation(template, header, [qe.price_option("x", ROWS, qe.Pricing(0, 5, True, 7.5))],
                                     qe.Pricing(0, 5, True, 7.5))
    check("no 'Additional Discount' row when it is 0", not any(r.startswith("Additional Discount") for r in table_text(no_extra)))
else:
    print("skip quotation document checks (template.docx not on this PC)")

invoice = ie.Invoice(number="RESYPL/2026/11/009", date=dt.date(2026, 11, 13), terms="45 Days Net", lpo_number="",
                     lpo_date="", attention="", client_name="Test Client", client_address="Lagos",
                     lines=[ie.InvoiceLine(r["Model no."], r["Qty"], r["Description"], r["Unit cost (₦)"]) for r in ROWS],
                     discount_pct=5, extra_discount=20000)
check("invoice total matches the quotation", invoice.compute()["contract_total"] == 16109654.38)
text = "\n".join(table_text(ie.build_invoice(invoice)))
check("invoice document lists 5% DISCOUNT, ADDITIONAL DISCOUNT and DISCOUNTED TOTAL",
      all(x in text for x in ("5% DISCOUNT", "ADDITIONAL DISCOUNT", "20,000.00", "DISCOUNTED TOTAL", "14,985,725.00")))


def test_pricing():
    assert not failures, "; ".join(failures)


if __name__ == "__main__":
    print(f"\n{'All pricing checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)
