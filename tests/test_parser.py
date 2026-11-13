"""Checks the item parser against real-world wording.

Run with:  python tests/test_parser.py
Add a new line to CASES whenever staff find a message the parser reads wrongly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from item_parser import parse_message  # noqa: E402

# (pasted line, expected fields). Only the fields listed are checked.
CASES = [
    # --- quantity wording ---
    ("Supply of 16 nos 200AH battery @ 320,000", {"Qty": 16, "Unit cost (₦)": 320000, "Description": "200AH battery"}),
    ("Two pieces changeover switch = 85,000", {"Qty": 2, "Unit cost (₦)": 85000, "Description": "Changeover switch"}),
    ("Battery rack (4) = 150,000", {"Qty": 4, "Unit cost (₦)": 150000, "Description": "Battery rack"}),
    ("Qty: 4 Battery rack = 150,000", {"Qty": 4, "Description": "Battery rack"}),
    ("Battery 12V 200AH (16pcs) 320,000", {"Qty": 16, "Unit cost (₦)": 320000, "Description": "Battery 12V 200AH"}),
    ("A pair of battery terminals = 5,000", {"Qty": 2, "Description": "Battery terminals"}),
    ("Breaker 63A x4 = 45,000", {"Qty": 4, "Unit cost (₦)": 45000, "Description": "Breaker 63A"}),
    ("2 x Battery rack N150,000", {"Qty": 2, "Unit cost (₦)": 150000, "Description": "Battery rack"}),
    ("CAD50M7 Changeover switch 50A = 85,000 2pcs", {"Qty": 2, "Model no.": "CAD50M7", "Description": "Changeover switch 50A"}),
    ("Battery 12V 200AH #320,000 16", {"Qty": 16, "Unit cost (₦)": 320000}),
    ("16 batteries at 320,000 each", {"Qty": 16, "Unit cost (₦)": 320000, "Description": "Batteries"}),
    ("sixteen 200AH batteries 320k", {"Qty": 16, "Unit cost (₦)": 320000}),
    ("Cable lugs one dozen 2,500", {"Qty": 12, "Unit cost (₦)": 2500}),
    ("3 phase changeover switch = 250,000", {"Qty": 1, "Description": "3 phase changeover switch"}),
    ("4 core armoured cable 16mm = 12,500 x 30", {"Qty": 30, "Unit cost (₦)": 12500}),
    ("Cat 6 Network Cable 2 x 187,000", {"Qty": 2, "Unit cost (₦)": 187000, "Description": "Cat 6 Network Cable"}),
    ("Battery 200AH 16 x 320k", {"Qty": 16, "Unit cost (₦)": 320000, "Description": "Battery 200AH"}),
    ("UPS battery 4 * 45000", {"Qty": 4, "Unit cost (₦)": 45000, "Description": "UPS battery"}),
    # --- spec numbers are not quantities ---
    ("Battery 12V 100 = 320,000", {"Qty": 1, "Unit cost (₦)": 320000, "Note": "Check qty"}),
    ("10KVA UPS = 1,450,000", {"Qty": 1, "Model no.": "", "Description": "10KVA UPS"}),
    ("Cable 16mm x 4core @12,500 x 30", {"Qty": 30, "Unit cost (₦)": 12500}),
    # --- price wording ---
    ("16 batteries at 320,000 each = 5,120,000", {"Qty": 16, "Unit cost (₦)": 320000}),
    ("Battery 200AH 320k x 16", {"Qty": 16, "Unit cost (₦)": 320000}),
    ("APC UPS 10kVA 1.45m", {"Qty": 1, "Unit cost (₦)": 1450000}),
    ("Battery 200AH total 5,120,000 for 16 pcs", {"Qty": 16, "Unit cost (₦)": 320000}),
    ("Surge protector 45000", {"Qty": 1, "Unit cost (₦)": 45000}),
    ("SRV-10000 APC Easy UPS 10kVA - 1,450,000", {"Model no.": "SRV-10000", "Unit cost (₦)": 1450000}),
    ("Installation and commissioning =250,000", {"Qty": 1, "Unit cost (₦)": 250000, "Description": "Installation and commissioning"}),
    ("Inverter 5kva unit price: 850,000 qty 2", {"Qty": 2, "Unit cost (₦)": 850000}),
    ("Battery rack", {"Qty": 1, "Unit cost (₦)": 0, "Note": "No price"}),
    # --- availability ---
    ("Battery 200AH 320k x 16, 2 weeks delivery", {"Qty": 16, "Availability": "2 weeks", "Description": "Battery 200AH"}),
    ("Changeover switch 85,000 ex-stock", {"Availability": "Ex-stock", "Description": "Changeover switch"}),
    ("Inverter 10kVA 2.1m import 6-8 weeks", {"Availability": "6-8 weeks", "Unit cost (₦)": 2100000}),
    ("We need sixteen 200AH batteries at 320k each, available in 2 weeks", {"Qty": 16, "Unit cost (₦)": 320000, "Availability": "2 weeks", "Description": "200AH batteries"}),
    ("Surge protection device 40kA 3 nos = 135,000 total", {"Qty": 3, "Unit cost (₦)": 45000}),
    ("APC Smart UPS SRT10KXLI 10kVA - N4,850,000 (1 unit) import 6 weeks", {"Model no.": "SRT10KXLI", "Qty": 1, "Unit cost (₦)": 4850000, "Availability": "6 weeks"}),
    ("Armoured cable 4core 16mm 100m roll = 1.2m", {"Qty": 1, "Unit cost (₦)": 1200000}),
    # --- labelled fields (real staff format, Nov 2026) ---
    ("1. Item Code: ITAC00195 | Description: iPhone 11 Screen | Quantity: 30 Units | Unit Price: 25,000",
     {"Model no.": "ITAC00195", "Description": "iPhone 11 Screen", "Qty": 30, "Unit cost (₦)": 25000, "Note": ""}),
    ("3. Item Code: ITAC0098 | Description: Logitech M90 Wired Mouse (must have Serial Number) | Quantity: 15 Units | Unit Price: 7,500",
     {"Model no.": "ITAC0098", "Description": "Logitech M90 Wired Mouse (must have Serial Number)", "Qty": 15, "Unit cost (₦)": 7500}),
    ("4. Item Code: ITAC0074 | Description: Logitech K120 Wired Keyboard with Mac Support (must have Serial Number) | Quantity: 15 Units | Unit Price: 0",
     {"Model no.": "ITAC0074", "Qty": 15, "Unit cost (₦)": 0, "Note": "Price is 0"}),
    ("5. Item Code: ITAC0186 | Description: Apple USB-C to MagSafe 3 Cable (2M) | Quantity: 10 Units | Unit Price: 45,000",
     {"Description": "Apple USB-C to MagSafe 3 Cable (2M)", "Qty": 10, "Unit cost (₦)": 45000}),
    ("10. Item Code: ITAC0149 | Description: Apple MacBook Air Battery (A1965) | Quantity: 5 Units | Unit Price: 120,000",
     {"Model no.": "ITAC0149", "Description": "Apple MacBook Air Battery (A1965)", "Qty": 5, "Unit cost (₦)": 120000}),
    ("13. Item Code: ITAC001 | Description: Apple USB-C 70 Watts Power Adapter | Quantity: 5 Units | Unit Price: 90,000",
     {"Model no.": "ITAC001", "Description": "Apple USB-C 70 Watts Power Adapter", "Qty": 5, "Unit cost (₦)": 90000}),
    ("Item Code: ITAC0168 Description: Apple iPhone 15 Pro Protective Black Case Quantity: 3 Units Unit Price: 5,000",
     {"Model no.": "ITAC0168", "Description": "Apple iPhone 15 Pro Protective Black Case", "Qty": 3, "Unit cost (₦)": 5000}),
    ("SKU: AB12 | Item: UPS battery | Qty: 4 | Total: 200,000 | Brand: APC | Delivery: 2 weeks",
     {"Model no.": "AB12", "Description": "UPS battery (Brand: APC)", "Qty": 4, "Unit cost (₦)": 50000, "Availability": "2 weeks"}),
    ("Laptop charger A1965 = 45,000", {"Unit cost (₦)": 45000}),
    # --- WhatsApp formatting ---
    ("[12/10/25, 9:41:03 AM] Josiah: Surge protector = 45,000", {"Description": "Surge protector", "Unit cost (₦)": 45000}),
    ("12/10/2025, 09:45 - Josiah: 2 x Battery rack 150k", {"Qty": 2, "Unit cost (₦)": 150000}),
    ("1. Breaker 63A = 45,000", {"Qty": 1, "Description": "Breaker 63A"}),
]

# Lines that are chat or instructions, not items
IGNORED = [
    "Good morning sir",
    "Pls prepare quotation for PAL",
    "[12/10/25, 9:41:10 AM] Josiah: Pls prepare quotation, markup 15%",
    "Thanks",
    "Add VAT",
    "Item Code | Description | Qty | Unit Price",
    "S/N Model No Descriptions Unit Cost Qty Total Cost Availability",
]

# Raw text exactly as the picture reader (Tesseract) returned it for real-looking pictures,
# with the items that must come out after clean-up.
PICTURES = [
    ("WhatsApp screenshot", """Good morning sir, pls prepare quotation for
PAL 10:40 AM OO
16 nos 200AH battery @ 320,000
10:41 AM OO
Changeover switch 2pcs = 85,000 each
10:42 AM O0
APC Smart UPS SRT10KXLI 10kVA - N4,850,000 (
1 unit) import 6 weeks 10:43 AM OO
Installation and commissioning = 250,000
10:44 AMO
Markup 15%, add VAT
10:45 AMO""", [("", "200AH battery", 16, 320000), ("", "Changeover switch", 2, 85000),
                  ("SRT10KXLI", "APC Smart UPS 10kVA", 1, 4850000), ("", "Installation and commissioning", 1, 250000)]),
    ("photo of a spreadsheet on a PC screen", """——————<— —
Qty Unit Price
Item Code Description
{TACOO195 iPhone 11 Screen 30 25,000
ITACOO98 Logitech M90 Wired Mouse 15 7,500
ITACO186 Apple USB-C to MagSafe 3 Cable (2M) 10 45,000
———————————————""", [("ITAC00195", "iPhone 11 Screen", 30, 25000), ("ITAC0098", "Logitech M90 Wired Mouse", 15, 7500),
                    ("ITAC0186", "Apple USB-C to MagSafe 3 Cable (2M)", 10, 45000)]),
]


def check_cases():
    failures = []
    for text, expected in CASES:
        items = parse_message(text, "").items
        if len(items) != 1:
            failures.append(f"{text!r}: expected 1 item, got {len(items)}: {items}")
            continue
        got = items[0]
        for key, want in expected.items():
            value = got[key]
            ok = want in value if key == "Note" else value == want
            if not ok:
                failures.append(f"{text!r}: {key} expected {want!r}, got {value!r}")
    for text in IGNORED:
        items = parse_message(text, "").items
        if items:
            failures.append(f"{text!r}: should be ignored, got {items}")
    return failures


def check_pictures():
    from image_reader import clean

    failures = []
    for name, raw, expected in PICTURES:
        items = parse_message(clean(raw), "").items
        got = [(i["Model no."], i["Description"], i["Qty"], i["Unit cost (₦)"]) for i in items]
        if got != expected:
            failures.append(f"{name}: expected {expected}, got {got}")
    return failures


def check_instructions():
    s = parse_message("Pls prepare quotation, markup 15%\n5% discount\nadd VAT\nBattery 320k").suggestions
    failures = []
    if s.markup_pct != 15:
        failures.append(f"markup expected 15, got {s.markup_pct}")
    if s.discount_pct != 5:
        failures.append(f"discount expected 5, got {s.discount_pct}")
    if s.vat_enabled is not True:
        failures.append(f"VAT expected on, got {s.vat_enabled}")
    return failures


def test_cases():
    assert not check_cases(), "\n".join(check_cases())


def test_pictures():
    assert not check_pictures(), "; ".join(check_pictures())


def test_instructions():
    assert not check_instructions(), "\n".join(check_instructions())


if __name__ == "__main__":
    problems = check_cases() + check_instructions() + check_pictures()
    total = len(CASES) + len(IGNORED) + 1 + len(PICTURES)
    for p in problems:
        print("FAIL", p)
    print(f"\n{total - len(problems)} / {total} checks passed" if problems else f"All {total} checks passed")
    sys.exit(1 if problems else 0)
