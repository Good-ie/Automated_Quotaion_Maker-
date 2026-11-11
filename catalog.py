"""Product memory: learns items and prices from every quotation you generate.

The items are kept in the database (see store.py). When a pasted item looks like one
you have quoted before, the app fills in the saved model number and description, a
missing price, and warns when the price moved a lot.
"""
from __future__ import annotations

import datetime as dt
import re

PRICE_CHANGE_WARNING = 0.20  # warn when a price differs from the last quote by more than 20%

_STOP_WORDS = {"of", "and", "the", "for", "with", "a", "an", "&", "to", "in", "on", "set"}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_stem(w) for w in words if w not in _STOP_WORDS}


def _key(description: str) -> str:
    return " ".join(sorted(tokens(description)))


def clean_description(text: str) -> str:
    """Repair descriptions saved with their labels, e.g.
    'Item Code: | Description: iPhone 11 Screen | Units | Unit Price' -> 'iPhone 11 Screen'."""
    m = re.search(r"description\s*[:=]\s*([^|;]+)", text, re.I)
    return (m.group(1) if m else text).strip()


def learn(entries: list[dict], rows: list[dict], quoted_on: dt.date) -> list[dict]:
    """Add or update saved items from reviewed item rows.

    Updates `entries` in place and returns the entries that were added or changed, so
    only those need saving (existing ones keep their database "id").
    """
    by_key = {_key(e["description"]): e for e in entries}
    by_model = {e["model"].upper(): e for e in entries if e.get("model")}
    changed: list[dict] = []
    for row in rows:
        desc = str(row.get("Description") or "").strip()
        model = str(row.get("Model no.") or "").strip()
        price = float(row.get("Unit cost (₦)") or 0)
        if not desc or price <= 0:
            continue
        entry = by_model.get(model.upper()) if model else None
        entry = entry or by_key.get(_key(desc))
        if entry is None:
            entry = {"times_quoted": 0}
            entries.append(entry)
        entry.update(
            description=desc,
            model=model or entry.get("model", ""),
            unit_cost=price,
            availability=str(row.get("Availability") or "").strip(),
            last_quoted=quoted_on.isoformat(),
            times_quoted=int(entry.get("times_quoted", 0)) + 1,
        )
        by_key[_key(desc)] = entry
        if entry["model"]:
            by_model[entry["model"].upper()] = entry
        if not any(c is entry for c in changed):
            changed.append(entry)
    return changed


def find(description: str, model: str, entries: list[dict]) -> dict | None:
    """Best saved match for a pasted item, or None.

    Matches on model number first; otherwise every meaningful word typed must appear in
    the saved description (so "200AH battery" finds "Battery 12V 200AH deep cycle").
    """
    if model:
        for e in entries:
            if e.get("model", "").upper() == model.upper():
                return e
    typed = tokens(description)
    if len(typed) < 2:
        return None
    best, best_score = None, 0.0
    for e in entries:
        saved = tokens(e["description"])
        shared = typed & saved
        if len(shared) < 2:
            continue
        coverage = len(shared) / len(typed)  # how much of what was typed is in the saved item
        if coverage < 0.75:
            continue
        precision = len(shared) / len(saved)  # prefer the closest, not the longest, saved item
        score = coverage + 0.5 * precision + 0.01 * min(e.get("times_quoted", 0), 10)
        if score > best_score:
            best, best_score = e, score
    return best


def _date(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "before"


def enrich(items: list[dict], entries: list[dict]) -> list[dict]:
    """Fill gaps in parsed items from the catalog and add notes about what changed."""
    if not entries:
        return items
    for item in items:
        match = find(item["Description"], item["Model no."], entries)
        if not match:
            continue
        notes = [item["Note"]] if item["Note"] else []
        when = _date(match.get("last_quoted"))
        last_price = float(match.get("unit_cost") or 0)

        if not item["Model no."] and match.get("model"):
            item["Model no."] = match["model"]
        if len(match["description"]) > len(item["Description"]):
            item["Description"] = match["description"]
        notes.append("Matched a saved item.")

        price = float(item["Unit cost (₦)"] or 0)
        if price == 0 and last_price:
            item["Unit cost (₦)"] = last_price
            notes = [n for n in notes if n != "No price found."]
            notes.append(f"Price taken from last quote ({when}).")
        elif price and last_price and abs(price - last_price) / last_price > PRICE_CHANGE_WARNING:
            change = (price - last_price) / last_price * 100
            notes.append(f"Price {change:+.0f}% vs last quote: ₦{last_price:,.0f} on {when}.")
        item["Note"] = " ".join(notes)
    return items
