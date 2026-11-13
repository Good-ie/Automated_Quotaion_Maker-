"""Shared item input: paste text or add pictures (screenshots / photos), then review."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

import image_reader
import item_parser

PICTURE_TYPES = ["png", "jpg", "jpeg", "webp", "bmp"]


def _read_pictures(text_key: str) -> None:
    """Callback: read newly added pictures and add their text to the text box."""
    files = st.session_state.get(f"{text_key}_pics") or []
    done = st.session_state.setdefault(f"{text_key}_pics_done", set())
    texts, failed = [], []
    for f in files:
        signature = (f.name, f.size)
        if signature in done:
            continue
        done.add(signature)
        try:
            text = image_reader.read_picture(f.getvalue())
        except RuntimeError as exc:  # OCR not installed on this computer
            st.session_state[f"{text_key}_pics_msg"] = ("error", str(exc))
            return
        except (OSError, ValueError):
            failed.append(f.name)
            continue
        if text.strip():
            texts.append(text)
        else:
            failed.append(f.name)
    if texts:
        current = st.session_state.get(text_key, "") or ""
        st.session_state[text_key] = (current.rstrip() + "\n" if current.strip() else "") + "\n".join(texts)
    if texts and not failed:
        st.session_state[f"{text_key}_pics_msg"] = (
            "success", f"Read {len(texts)} picture(s). Check the text above. Fix anything misread, then review the items.")
    elif failed:
        st.session_state[f"{text_key}_pics_msg"] = (
            "warning", f"No text could be read from: {', '.join(failed)}. Try a clearer or straighter picture.")


def picture_box(text_key: str) -> None:
    """Upload box under a 'Paste items' text box; read text goes into that box."""
    st.file_uploader(
        "Or add pictures (screenshots or photos)",
        type=PICTURE_TYPES,
        accept_multiple_files=True,
        key=f"{text_key}_pics",
        on_change=_read_pictures,
        args=(text_key,),
        help="WhatsApp or computer screenshots read best. Photos of a screen or printed list work if they are "
        "clear and straight. The text found is added to the box above for you to check.",
    )
    message = st.session_state.pop(f"{text_key}_pics_msg", None)
    if message:
        kind, text = message
        getattr(st, kind)(text, icon={"success": ":material/check_circle:", "warning": ":material/warning:",
                                      "error": ":material/error:"}[kind])
    elif not image_reader.available():
        st.caption(":material/info: Reading pictures works on the online app (it needs the free Tesseract reader).")


def text_and_pictures(text_key: str, placeholder: str) -> str:
    raw = st.text_area(
        "Paste items",
        key=text_key,
        height=140,
        placeholder=placeholder,
        help="Paste the message or list as it is. Quantities, prices (320k, 1.45m, ₦85,000) and model numbers "
        "are picked up.",
    )
    picture_box(text_key)
    return raw


def parsed(text_key: str, raw: str) -> tuple[str, list[dict]]:
    """(short id of the text, items read from it), cached so edits in the table are kept."""
    tag = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    cache = f"{text_key}_parsed_{tag}"
    if cache not in st.session_state:
        st.session_state[cache] = item_parser.parse_message(raw, "").items if raw.strip() else []
    return tag, st.session_state[cache]


def item_table(key: str, rows: list[dict], with_price: bool) -> list[dict]:
    """Editable table of items. Rows: {model, qty, desc, unit}. Returns the edited rows."""
    columns = ["Model no.", "Qty", "Description"] + (["Unit price (₦)"] if with_price else [])
    df = pd.DataFrame(
        [{"Model no.": r.get("model", ""), "Qty": r.get("qty", 1), "Description": r.get("desc", ""),
          "Unit price (₦)": r.get("unit", 0.0)} for r in rows],
        columns=["Model no.", "Qty", "Description", "Unit price (₦)"],
    )[columns]
    config = {
        "Model no.": st.column_config.TextColumn(width="small"),
        "Qty": st.column_config.NumberColumn(min_value=1, step=1, default=1, required=True),
        "Description": st.column_config.TextColumn(width="large", required=True),
    }
    if with_price:
        config["Unit price (₦)"] = st.column_config.NumberColumn(min_value=0.0, format="accounting", default=0.0)
    edited = st.data_editor(df, key=key, num_rows="dynamic", hide_index=True, column_config=config)
    out = []
    for r in edited.to_dict("records"):
        desc = "" if r.get("Description") is None or r["Description"] != r["Description"] else str(r["Description"]).strip()
        model = "" if r.get("Model no.") is None or r["Model no."] != r["Model no."] else str(r["Model no."]).strip()
        if not desc and not model:
            continue
        qty = r.get("Qty")
        unit = r.get("Unit price (₦)", 0.0) if with_price else 0.0
        out.append({
            "model": model, "desc": desc,
            "qty": int(qty) if qty == qty and qty else 1,
            "unit": float(unit) if unit == unit and unit else 0.0,
        })
    return out


def from_parsed(items: list[dict]) -> list[dict]:
    return [{"model": i["Model no."], "qty": i["Qty"], "desc": i["Description"], "unit": i["Unit cost (₦)"]}
            for i in items]
