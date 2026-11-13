"""Checks that items are read correctly from files staff upload.

Run with:  python tests/test_files.py
Pictures and old .doc/.xls need Tesseract / LibreOffice (installed online), so they are
checked separately; everything here runs on any PC.
"""
import datetime as dt
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

import file_reader  # noqa: E402
import invoice_engine as ie  # noqa: E402
from item_parser import parse_message  # noqa: E402

failures: list[str] = []


def items_of(name: str, data: bytes) -> list[tuple]:
    text = file_reader.read_file(name, data)
    return [(i["Model no."], i["Description"], i["Qty"], i["Unit cost (₦)"]) for i in parse_message(text, "").items]


def check(name: str, got, want) -> None:
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(f"{name}: expected {want}, got {got}")


# Excel: title row, S/N and Amount columns, a TOTAL row
book = openpyxl.Workbook()
sheet = book.active
sheet.append(["Client: PAL Pensions"])
sheet.append([])
sheet.append(["S/N", "Item Code", "Description", "Qty", "Unit Price", "Amount"])
sheet.append([1, "ITAC00195", "iPhone 11 Screen", 30, 25000, 750000])
sheet.append([2, "ITAC0098", "Logitech M90 Wired Mouse", 15, 7500.0, 112500])
sheet.append([3, None, "Installation and configuration", 1, 150000, 150000])
sheet.append([None, None, "TOTAL", None, None, 1012500])
buffer = io.BytesIO()
book.save(buffer)
check("Excel: items only (no title, S/N or TOTAL row)", items_of("list.xlsx", buffer.getvalue()), [
    ("ITAC00195", "iPhone 11 Screen", 30, 25000.0), ("ITAC0098", "Logitech M90 Wired Mouse", 15, 7500.0),
    ("", "Installation and configuration", 1, 150000.0)])

csv_data = "Model No;Description;Quantity;Unit Cost\nSRT10KXLI;APC Smart UPS 10kVA;1;4,850,000\n;Battery rack;4;150,000\n"
check("CSV with ; separators", items_of("list.csv", csv_data.encode()), [
    ("SRT10KXLI", "APC Smart UPS 10kVA", 1, 4850000.0), ("", "Battery rack", 4, 150000.0)])

check("text file of a chat", items_of("chat.txt", b"Good morning sir\n16 nos 200AH battery @ 320,000\n"),
      [("", "200AH battery", 16, 320000.0)])

# Word: a full invoice (letterhead, details, totals, bank) -> only its items
invoice = ie.Invoice(number="RESYPL/2026/11/001", date=dt.date(2026, 11, 6), terms="45 Days Net",
                     lpo_number="PO-16967-01", lpo_date="27th October 2026", attention="Babalola",
                     client_name="Pladis Foods Nigeria Limited", client_address="58B Kudirat Abiola Way",
                     lines=[ie.InvoiceLine("", 2, "Cat 6 Network Cable", 187000),
                            ie.InvoiceLine("CAD50M7", 4, "25mm PVC Flex Pipes", 19800)])
check("Word document: only the items table", items_of("invoice.docx", ie.build_invoice(invoice)), [
    ("", "Cat 6 Network Cable", 2, 187000.0), ("CAD50M7", "25mm PVC Flex Pipes", 4, 19800.0)])

# PDF made by a computer (uses the demo invoice when it is on this PC)
demo_pdf = ROOT / "demo" / "1_Invoice_full.pdf"
if demo_pdf.exists():
    check("PDF invoice: only the item rows", items_of("invoice.pdf", demo_pdf.read_bytes()), [
        ("", "Cat 6 Network Cable", 2, 187000.0), ("", "25mm PVC Flex Pipes", 4, 19800.0),
        ("", "Accessories Installation Materials", 1, 167200.0), ("", "Logistics", 1, 39600.0),
        ("", "Installation, Testing, Service Charge", 1, 132000.0)])
else:
    print("skip PDF check (demo/1_Invoice_full.pdf not on this PC)")

try:
    file_reader.read_file("virus.exe", b"MZ")
    check("unknown file types are refused", False, True)
except file_reader.FileReadError:
    check("unknown file types are refused", True, True)


def test_files():
    assert not failures, "; ".join(failures)


if __name__ == "__main__":
    print(f"\n{'All file checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    sys.exit(1 if failures else 0)
