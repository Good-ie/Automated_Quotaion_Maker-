"""Reads pasted WhatsApp / plain-text item lists the way a person in the office would.

Every line is broken into: model number, description, quantity, unit price and availability.
Instructions such as "markup 15%" or "add VAT" are collected as suggestions instead of items.
When the parser has to guess, it says so in the row's note so staff can check it.

Free and offline: plain Python rules, no AI service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
}
_NUM_WORD = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

# Words that mean "how many": 16 nos, 4 pcs, 2 sets, 3 rolls...
UNIT_WORDS = (
    r"pcs|pc|pieces?|nos|no|units?|sets?|rolls?|coils?|drums?|pairs?|lengths?|"
    r"packs?|bags?|cartons?|boxes|box|lots?|bundles?"
)

# Numbers glued to an electrical/technical unit are specifications, never qty or price.
_SPEC = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?(?:"
    r"\s?(?:ah|kva|kvar|va|kwh|kwp|kw|watts?|volts?|vac|vdc|amps?|mm2|mm|sqmm|cm|cores?|phase|ph|"
    r"poles?|way|gang|hp|hz|mins?|minutes|hrs?|hours|kg|ft|inch|mah|wp|ports?|bar|tons?|yrs?|years?)"
    r"|(?:a|v|w|c|p)"
    r")(?![a-z])",
    re.IGNORECASE,
)
# Lengths: "100m", "50 meters". Kept in the description (they describe the item).
_LENGTH = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:m|mtrs?|meters?|metres?|yards?|yds?)(?![a-z])", re.IGNORECASE)
_NOT_QTY_NOUNS = {
    "phase", "core", "cores", "pole", "poles", "way", "gang", "years", "yrs", "months", "weeks",
    "days", "hours", "mins", "minutes", "volts", "amps", "watts", "meters", "metres", "naira",
}

_WHATSAPP_PREFIX = re.compile(
    r"^\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp][Mm])?\]?\s*(?:-\s*)?"
    r"(?:[^:\d][^:]{0,40}:\s*)?"
)
_BULLET = re.compile(r"^(?:[\-*••]+|\d{1,2}[.)])\s+")
_CHATTER = re.compile(
    r"\b(?:good\s+(?:morning|afternoon|evening|day)|hello|hi|thanks?|thank\s+you|regards|noted|ok(?:ay)?|"
    r"kindly|pls|please|prepare|quotation|quote|send|attached|below|following)\b",
    re.IGNORECASE,
)
# words that only appear in a table's header row
_HEADER_WORDS = {
    "s", "n", "sn", "no", "item", "items", "code", "codes", "model", "models", "number", "numbers", "part",
    "description", "descriptions", "desc", "product", "products", "qty", "quantity", "quantities", "unit",
    "units", "price", "prices", "cost", "costs", "rate", "total", "amount", "amounts", "availability",
    "availabilities", "ngn", "naira", "remark", "remarks", "uom",
}
_FILLER_START = re.compile(
    r"^(?:(?:pls|please|kindly)\s+)?(?:(?:we|i|they|client)\s+)?(?:need|needs|want|wants|require|requires|"
    r"supply\s+of|supply)\s+(?:of\s+)?",
    re.IGNORECASE,
)

# Instructions that belong to the whole quote, not one item
_MARKUP = re.compile(r"(?:mark[\s-]?up|margin)\s*(?:of|:|=|at)?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:mark[\s-]?up|margin)", re.I)
_DISCOUNT = re.compile(r"discount\s*(?:of|:|=|at)?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*discount", re.I)
_VAT_RATE = re.compile(r"vat\s*(?:of|:|=|at|@)?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*vat", re.I)
_VAT_ON = re.compile(r"\b(?:add|include|including|plus|with|charge|apply)\s+vat\b|\+\s*vat\b|\bvat\s+inclusive\b", re.I)
_VAT_OFF = re.compile(r"\b(?:no|without|exclude|excluding|minus)\s+vat\b|\bvat\s+exclusive\b", re.I)

# Availability / delivery wording
_AVAILABILITY = re.compile(
    r"\b(?:ex[\s-]?stock|in[\s-]?stock|available(?!\s+(?:in|within|after)\s+\d)(?:\s+now)?|off[\s-]the[\s-]shelf)\b"
    r"|(?:\b(?:available|delivery|lead\s*time|import(?:ed)?|order|eta|ready)\s*(?:in|:|-|of|within|after)?\s*)?"
    r"\b\d+\s*(?:(?:-|to)\s*\d+\s*)?(?:days?|weeks?|wks?|months?)\b(?:\s+(?:delivery|import|lead\s*time))?"
    r"|\bimport(?:ed)?\s+order\b|\bon\s+import\b|\bout\s+of\s+stock\b|\bnot\s+available\b",
    re.IGNORECASE,
)

# A number that might be money
_MONEY = re.compile(
    r"(?P<cur>₦|=N=|NGN|(?<![A-Za-z])N(?=\s?\d)|#)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suf>k|m|million|mil|thousand)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MARK_BEFORE = re.compile(
    r"(?:unit\s+(?:price|cost)|price|cost|rate|amount|total|at|for|@|=|:|-)\s*$", re.IGNORECASE
)
_UNIT_BEFORE = re.compile(r"(?:unit\s+(?:price|cost)|rate|at|@)\s*$", re.IGNORECASE)
_TOTAL_BEFORE = re.compile(r"(?:total|amount|sum)\s*(?:[:=]\s*)?$", re.IGNORECASE)
_TOTAL_AFTER = re.compile(r"^\s*(?:in\s+)?(?:total|altogether|for\s+all|for\s+the\s+lot)\b", re.IGNORECASE)
_EACH_AFTER = re.compile(r"^\s*(?:each|ea\b|per\s+(?:unit|piece|pc|one|item)|/\s*(?:unit|pc|piece|each))", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Suggestions:
    markup_pct: float | None = None
    discount_pct: float | None = None
    vat_enabled: bool | None = None
    vat_pct: float | None = None

    def any(self) -> bool:
        return any(v is not None for v in self.__dict__.values())


@dataclass
class ParseResult:
    items: list[dict] = field(default_factory=list)
    suggestions: Suggestions = field(default_factory=Suggestions)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _overlaps(span, spans) -> bool:
    return any(span[0] < e and s < span[1] for s, e in spans)


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove the given character spans from text."""
    out, pos = [], 0
    for s, e in sorted(spans):
        if s >= pos:
            out.append(text[pos:s])
            pos = e
    out.append(text[pos:])
    return " ".join("".join(out).split())


def _money_value(m: re.Match) -> float:
    value = float(m.group("num").replace(",", ""))
    suf = (m.group("suf") or "").lower()
    if suf in ("k", "thousand"):
        value *= 1_000
    elif suf in ("m", "million", "mil"):
        value *= 1_000_000
    return value


def _find_prices(line: str, protected: list[tuple[int, int]]):
    """Return list of (value, kind, span) where kind is 'unit', 'total' or ''."""
    found = []
    spec_spans = [m.span() for m in _SPEC.finditer(line)]
    for m in _MONEY.finditer(line):
        span = m.span()
        if not m.group("num") or _overlaps(span, spec_spans):
            continue
        if _overlaps(span, protected):
            # "1.45m" / "N2m" is money; plain "100m" is a length
            millions = (m.group("suf") or "").lower() in ("m", "million", "mil")
            if not (millions and ("." in m.group("num") or m.group("cur"))):
                continue
        start = m.start("cur") if m.group("cur") else m.start("num")
        # part of a hyphenated model number such as SRV-10000
        if start >= 2 and line[start - 1] in "-/" and line[start - 2].isalnum():
            continue
        # part of a code such as A1965 or ITAC00195
        if not m.group("cur") and start >= 1 and line[start - 1].isalnum():
            continue
        before = line[:start]
        after = line[m.end():]
        if re.match(rf"\s*(?:{UNIT_WORDS})\b", after, re.I):
            continue  # "16 pcs" is a quantity
        qty_times = None
        if re.search(r"(?:\bx|[×*])\s*$", before, re.I):
            # "2 x 187,000" is 2 at 187,000 each; but "x 16" alone is a quantity
            qty_times = re.search(r"(?:^|\s)(\d{1,4})\s*(?:x|×|\*)\s*$", before, re.I)
            money_like = bool(m.group("cur") or m.group("suf")) or "," in m.group("num") or _money_value(m) >= 1000
            if not (qty_times and money_like):
                continue
        if re.search(r"\b(?:qty|quantity)\s*[:=]?\s*$", before, re.I):
            continue
        num_txt = m.group("num")
        suf = (m.group("suf") or "").lower()
        value = _money_value(m)
        has_cur = bool(m.group("cur"))
        has_mark = bool(_MARK_BEFORE.search(before))
        if suf in ("m", "million", "mil") and not (has_cur or has_mark or "." in num_txt):
            continue  # "100m" is more likely metres than millions
        is_price = (
            has_cur
            or bool(suf)
            or "," in num_txt
            or (has_mark and value >= 100)
            or (value >= 1000 and "." not in num_txt[:-3])
        )
        if not is_price or value < 1:
            continue
        kind = ""
        full_span = (start, m.end())
        mark = _MARK_BEFORE.search(before)
        if mark:
            full_span = (mark.start(), m.end())
        each = _EACH_AFTER.match(after)
        total_after = _TOTAL_AFTER.match(after)
        if each:
            kind = "unit"
            full_span = (full_span[0], m.end() + each.end())
        elif total_after:
            kind = "total"
            full_span = (full_span[0], m.end() + total_after.end())
        elif _UNIT_BEFORE.search(before):
            kind = "unit"
        elif _TOTAL_BEFORE.search(before):
            kind = "total"
        if qty_times:
            kind = "unit"
            found.append((int(qty_times.group(1)), "qty", (qty_times.start(1), start)))
        found.append((value, kind, full_span))
    return found


def _choose_prices(found):
    """Work out (unit_price, total_price) from the money amounts on a line."""
    if not found:
        return None, None
    units = [f for f in found if f[1] == "unit"]
    totals = [f for f in found if f[1] == "total"]
    unit = units[0][0] if units else None
    total = totals[-1][0] if totals else None
    others = [f[0] for f in found if f[1] == ""]
    if unit is None and total is None:
        if len(others) >= 2:
            lo, hi = min(others), max(others)
            if lo > 0 and hi > lo and (hi / lo).is_integer():
                return lo, hi
        return others[-1], None
    if unit is not None and total is None and others:
        total = max(others) if max(others) > unit else None
    if total is not None and unit is None and others:
        unit = min(others) if min(others) < total else None
    return unit, total


_QTY_PATTERNS = [
    # qty: 4 / quantity 4
    ("label", re.compile(r"\b(?:qty|quantity|qnty)\s*[:=\-]?\s*(\d{1,5})\b", re.I)),
    # (4) / (16pcs)
    ("bracket", re.compile(rf"\(\s*(\d{{1,5}})\s*(?:{UNIT_WORDS})?\.?\s*\)", re.I)),
    # 16 nos / 4 pcs / 2 sets (of)
    ("unit", re.compile(rf"(?<![\w.,])(\d{{1,5}})\s*(?:{UNIT_WORDS})\.?(?![a-z])(?:\s+of\b)?", re.I)),
    # a pair of / a dozen
    ("pair", re.compile(r"\ba\s+pair\s+of\b", re.I)),
    ("dozen", re.compile(rf"\b(?:(\d{{1,3}}|{_NUM_WORD}|a)\s+)?dozens?\b(?:\s+of\b)?", re.I)),
    # two pieces / sixteen nos
    ("word_unit", re.compile(rf"\b({_NUM_WORD})\s+(?:{UNIT_WORDS})\b(?:\s+of\b)?", re.I)),
    # x4 / x 4 / * 4
    ("times", re.compile(r"(?:\bx|\*)\s*(\d{1,5})(?![\w.])", re.I)),
    # 2 x battery (at start)
    ("lead_times", re.compile(r"^(\d{1,5})\s*(?:x|\*)\s+", re.I)),
    # sixteen batteries / two UPS (at start)
    ("word_start", re.compile(rf"^({_NUM_WORD})\b(?!\s*(?:{'|'.join(_NOT_QTY_NOUNS)})\b)", re.I)),
    # 16 batteries / 4 breakers
    ("plural", re.compile(r"(?<![\w.,])(\d{1,5})\s+(?=([a-z]{3,}s)\b)", re.I)),
    # 2 battery rack (at start)
    ("lead", re.compile(r"^(\d{1,5})\s+(?=[A-Za-z(])")),
    # battery rack 2 (at end)
    ("trail", re.compile(r"(?<![\w.,])(\d{1,5})\s*$")),
]


def _find_qty(text: str, protected: list[tuple[int, int]]):
    """Return (qty, span_to_remove, note)."""
    for kind, pattern in _QTY_PATTERNS:
        for m in pattern.finditer(text):
            span = m.span(1) if m.lastindex else m.span()
            if m.lastindex and _overlaps(span, protected):
                continue
            if kind == "pair":
                return 2, m.span(), ""
            if kind == "dozen":
                n = m.group(1)
                mult = 1 if not n or n.lower() == "a" else (int(n) if n.isdigit() else NUMBER_WORDS[n.lower()])
                return 12 * mult, m.span(), ""
            raw = m.group(1)
            qty = int(raw) if raw.isdigit() else NUMBER_WORDS[raw.lower()]
            if kind == "plural":
                noun = m.group(2).lower()
                if noun in _NOT_QTY_NOUNS:
                    continue
                return qty, m.span(1), ""
            if kind == "lead":
                nxt = text[m.end():].split(" ", 1)[0].lower()
                if nxt in _NOT_QTY_NOUNS:
                    continue
            if kind == "trail":
                before = text[: m.start()].strip()
                if not before:
                    continue
                prev_tok = before.split()[-1]
                if _SPEC.fullmatch(prev_tok) or _LENGTH.fullmatch(prev_tok):
                    return 1, None, (
                        f"Check qty: is '{raw}' the quantity or part of the spec? Set to 1."
                    )
            return qty, m.span(), ""
    return None, None, ""


_MODEL_LABEL = re.compile(r"\b(?:model|part)\s*(?:no\.?|number|#)?\s*[:=]?\s*([A-Z0-9][A-Z0-9\-_/.]{2,})", re.I)
_MODEL_TOKEN = re.compile(r"(?<![\w\-])([A-Z][A-Z0-9\-_/]*\d[A-Z0-9\-_/]*|\d+[A-Z]+\d[A-Z0-9\-_/]*)(?![\w\-])")


def _find_model(text: str):
    m = _MODEL_LABEL.search(text)
    if m:
        return m.group(1).rstrip(".-"), m.span()
    for m in _MODEL_TOKEN.finditer(text):
        token = m.group(1)
        letters = sum(c.isalpha() for c in token)
        digits = sum(c.isdigit() for c in token)
        if _SPEC.fullmatch(token) or _LENGTH.fullmatch(token):
            continue
        first = m.start() == 0
        if letters >= 1 and digits >= 1 and (first or (len(token) >= 5 and letters >= 2 and digits >= 2)):
            return token, m.span()
    return "", None


def _tidy(desc: str) -> str:
    desc = re.sub(r"\(\s*\)", "", desc)
    desc = re.sub(r"\s+([,.;:)])", r"\1", desc)
    desc = re.sub(r"^[\s,;:\-=@#.&]+|[\s,;:\-=@#.&]+$", "", desc)
    desc = re.sub(r"\b(?:each|ea)\s*$", "", desc, flags=re.I).strip(" ,;:-")
    desc = re.sub(r"\s{2,}", " ", desc).strip()
    first_word = desc.split(" ", 1)[0]
    if any(c.isupper() for c in first_word[1:]):  # iPhone, iPad, eBay: keep as typed
        return desc
    return desc[:1].upper() + desc[1:] if desc else desc


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _read_instructions(line: str, sug: Suggestions) -> list[tuple[int, int]]:
    spans = []
    for pattern, attr in ((_MARKUP, "markup_pct"), (_DISCOUNT, "discount_pct"), (_VAT_RATE, "vat_pct")):
        for m in pattern.finditer(line):
            setattr(sug, attr, float(m.group(1) or m.group(2)))
            spans.append(m.span())
            if attr == "vat_pct":
                sug.vat_enabled = True
    for m in _VAT_ON.finditer(line):
        sug.vat_enabled = True
        spans.append(m.span())
    for m in _VAT_OFF.finditer(line):
        sug.vat_enabled = False
        spans.append(m.span())
    return spans


# Labelled fields: "Item Code: X | Description: Y | Quantity: 30 Units | Unit Price: 25,000"
_FIELD_LABELS = {
    "model": r"item\s*code|product\s*code|part\s*(?:no\.?|number|#)|model\s*(?:no\.?|number|#)?|sku|code",
    "desc": r"description|desc|item\s*name|product\s*name|product|item|name",
    "qty": r"quantity|qty|qnty|no\.?\s*of\s*units|units",
    "unit": r"unit\s*(?:price|cost)|price\s*per\s*unit|rate|price|cost",
    "total": r"total\s*(?:price|cost|amount)?|amount",
    "avail": r"availability|available|delivery|lead\s*time|stock|status",
}
_LABEL_RE = re.compile(
    # known labels may follow a space; unknown ones ("Brand:") only start a | or ; field
    r"(?:(?:^|(?<=[|;,\s]))\s*(?P<label>"
    + "|".join(f"(?P<{k}>{v})" for k, v in _FIELD_LABELS.items())
    + r")|(?:^|(?<=[|;]))\s*(?P<other>[A-Za-z][A-Za-z ]{1,20}?))\s*[:=]\s*",
    re.IGNORECASE,
)


def _parse_amount(text: str) -> float | None:
    m = _MONEY.search(text)
    return _money_value(m) if m and m.group("num") else None


def _parse_labelled(line: str, default_availability: str) -> dict | None:
    """Read a line made of 'Label: value' fields. Returns None if it isn't one."""
    matches = [m for m in _LABEL_RE.finditer(line)]
    known = [m for m in matches if any(m.group(k) for k in _FIELD_LABELS)]
    if len(known) < 2:
        return None
    fields: dict[str, str] = {}
    extras: list[str] = []
    leading = line[: matches[0].start()].strip(" |;,")
    if leading:
        extras.append(leading)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        value = line[m.end():end].strip(" |;,")
        kind = next((k for k in _FIELD_LABELS if m.group(k)), None)
        if kind and kind not in fields:
            fields[kind] = value
        elif value:
            extras.append(f"{(m.group('label') or m.group('other')).strip()}: {value}")

    notes = []
    desc = fields.get("desc", "")
    if extras:
        desc = f"{desc} ({', '.join(extras)})" if desc else ", ".join(extras)

    qty = 1
    if fields.get("qty"):
        q = re.search(r"\d{1,6}", fields["qty"].replace(",", ""))
        w = re.search(rf"\b({_NUM_WORD})\b", fields["qty"], re.I)
        if q:
            qty = int(q.group(0))
        elif w:
            qty = NUMBER_WORDS[w.group(1).lower()]
        else:
            notes.append(f"Check qty: could not read '{fields['qty']}'.")

    unit = _parse_amount(fields["unit"]) if fields.get("unit") else None
    total = _parse_amount(fields["total"]) if fields.get("total") else None
    if unit is None and total:
        unit = round(total / max(qty, 1), 2)
        if qty > 1:
            notes.append(f"Unit price worked out from total {total:,.0f} ÷ {qty}.")
    elif unit and total and abs(unit * qty - total) > 1:
        notes.append(f"Check: {qty} × {unit:,.0f} ≠ total {total:,.0f}.")
    if not unit:
        notes.append("Price is 0 or missing. Please fill it in." if "unit" in fields else "No price found.")

    availability = ""
    if fields.get("avail"):
        availability = _availability_label(fields["avail"])

    model = fields.get("model", "").strip()
    if not desc and not model:
        return None
    return {
        "Model no.": model,
        "Description": _tidy(desc),
        "Qty": max(1, qty),
        "Unit cost (₦)": float(unit or 0.0),
        "Availability": availability or default_availability,
        "Note": " ".join(notes),
    }


def parse_line(raw: str, default_availability: str, sug: Suggestions) -> dict | None:
    line = _WHATSAPP_PREFIX.sub("", raw.strip()).strip()
    line = _BULLET.sub("", line).strip()
    if not line:
        return None

    labelled = _parse_labelled(line, default_availability)
    if labelled:
        return labelled

    instruction_spans = _read_instructions(line, sug)
    if instruction_spans:
        line = _cut(line, instruction_spans)

    notes = []
    protected = [m.span() for m in _SPEC.finditer(line)] + [m.span() for m in _LENGTH.finditer(line)]

    # Availability
    availability = ""
    avail_spans = []
    for m in _AVAILABILITY.finditer(line):
        if not availability:
            availability = _availability_label(m.group(0))
        avail_spans.append(m.span())
    if avail_spans:
        line = _cut(line, avail_spans)
        protected = [m.span() for m in _SPEC.finditer(line)] + [m.span() for m in _LENGTH.finditer(line)]

    # Prices
    found = _find_prices(line, protected)
    unit, total = _choose_prices(found)
    qty_after_price = next((f[0] for f in found if f[1] == "qty"), None)  # "2 x 187,000"
    if found:
        if qty_after_price is None:
            # "Battery 200AH #320,000 16": a bare number right after the price is the quantity
            last_end = max(f[2][1] for f in found)
            m = re.match(rf"\s*(?:x\s*)?(\d{{1,5}})\s*(?:(?:{UNIT_WORDS})\.?)?\s*$", line[last_end:], re.I)
            if m and not _overlaps((last_end + m.start(1), last_end + m.end(1)), protected):
                qty_after_price = int(m.group(1))
                found.append((0, "qty", (last_end, len(line))))
        line = _cut(line, [f[2] for f in found])
        protected = [m.span() for m in _SPEC.finditer(line)] + [m.span() for m in _LENGTH.finditer(line)]

    if unit is None and total is None:
        letters_only = re.sub(r"[^A-Za-z ]", "", line).strip()
        if not letters_only or _CHATTER.search(line) or instruction_spans:
            return None  # greetings, "pls prepare quotation", "markup 15%" on its own line
        words = re.findall(r"[a-z]+", line.lower())
        if words and all(w in _HEADER_WORDS for w in words):
            return None  # a table's header row, e.g. "Item Code | Description | Qty | Unit Price"

    line = _FILLER_START.sub("", line).strip()

    # Quantity
    if qty_after_price is not None:
        qty, qty_span, qty_note = qty_after_price, None, ""
    else:
        qty, qty_span, qty_note = _find_qty(line, protected)
    if qty_note:
        notes.append(qty_note)
    if qty_span:
        line = _cut(line, [qty_span])
    qty_given = qty is not None
    qty = qty or 1

    # Reconcile unit price and total
    if unit is not None and total is not None:
        ratio = total / unit if unit else 0
        if ratio and abs(ratio - round(ratio)) < 1e-6:
            if qty_given and round(ratio) != qty:
                notes.append(f"Check qty: line says {qty} but total ÷ unit price = {round(ratio)}.")
            elif not qty_given:
                qty = int(round(ratio))
                qty_given = True
                # a table row "4 25mm PVC Flex Pipes 19,800 79,200": the leading 4 is that quantity
                line = re.sub(rf"^\s*{qty}\s+(?=\S)", "", line)
        else:
            notes.append(f"Check price: total {total:,.0f} is not a multiple of unit price {unit:,.0f}.")
    elif total is not None:
        if qty_given and qty > 1:
            unit = round(total / qty, 2)
            notes.append(f"Unit price worked out from total {total:,.0f} ÷ {qty}.")
        else:
            unit = total

    model, model_span = _find_model(line)
    if model_span:
        line = _cut(line, [model_span])
    desc = _tidy(line)

    if not desc and not model:
        return None
    if unit is None:
        notes.append("No price found.")
    if not qty_given and unit is not None and (
        re.search(r"\bper\s+(?:m|meter|metre)\b|/\s*m\b", raw, re.I) or _LENGTH.search(desc)
    ):
        notes.append("If this is priced per metre, set Qty to the length.")

    return {
        "Model no.": model,
        "Description": desc,
        "Qty": max(1, qty),
        "Unit cost (₦)": float(unit or 0.0),
        "Availability": availability or default_availability,
        "Note": " ".join(notes),
    }


def _availability_label(text: str) -> str:
    t = " ".join(text.lower().split())
    if "out of stock" in t or "not available" in t:
        return "Not available"
    m = re.search(r"(\d+\s*(?:(?:-|to)\s*\d+\s*)?)(days?|weeks?|wks?|months?)", t)
    if m:
        num = re.sub(r"\s*(?:-|to)\s*", "-", m.group(1).strip())
        unit = m.group(2)
        unit = {"wk": "week", "wks": "weeks"}.get(unit, unit)
        if num == "1" and unit.endswith("s"):
            unit = unit[:-1]
        elif num != "1" and not unit.endswith("s"):
            unit += "s"
        return f"{num} {unit}"
    if re.search(r"ex[\s-]?stock|in[\s-]?stock|available|off[\s-]the[\s-]shelf", t):
        return "Ex-stock"
    if "import" in t:
        return "Import order"
    return text.strip().capitalize()


def parse_message(text: str, default_availability: str = "") -> ParseResult:
    result = ParseResult()
    for raw in text.splitlines():
        if raw.strip():
            item = parse_line(raw, default_availability, result.suggestions)
            if item:
                result.items.append(item)
    return result
