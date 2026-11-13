"""The "New quotation" screen: paste items, review, price and generate the Word document.

The quotation being prepared is saved as a draft while people work, so signing out (or
being signed out after 10 idle minutes) never loses it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

import catalog
import item_parser
import quote_engine as qe
import store as db
from views import common, items

COLUMNS = ["Model no.", "Description", "Qty", "Unit cost (₦)", "Availability", "Note"]
PRICING_DEFAULTS = {"markup": 0.0, "discount": 0.0, "vat_on": False, "vat_pct": 7.5}
OPTION_TITLES = {1: "REPLACEMENT OF 10KVA UPS (10MINS RUNTIME)", 2: "APC EASY UPS 10KVA WITH 100MINS RUNTIME"}
# everything typed into a quotation; cleared by "Start a new quotation"
FORM_KEYS = ("client_name", "client_address", "attention", "two_options", "raw_1", "raw_2", "title_1", "title_2")
# simple fields kept in a draft (besides the date and each option's title, text and rows)
DRAFT_FIELDS = ("client_name", "client_address", "attention", "quote_ref", "markup", "discount",
                "vat_on", "vat_pct", "default_availability", "two_options")


def _store() -> db.Store:
    return common.get_store()


def refresh_reference() -> None:
    st.session_state.quote_ref = _store().next_reference(common.user()["initials"], st.session_state.quote_date)


def start_new_quote() -> None:
    _store().delete_draft(common.user()["username"])
    st.session_state.pop("draft_saved_json", None)
    st.session_state.draft_offer_done = True
    for key in FORM_KEYS + ("generated",):
        st.session_state.pop(key, None)
    for key in [k for k in st.session_state if str(k).startswith(("parsed_", "raw_1_pics", "raw_2_pics"))]:
        del st.session_state[key]
    st.session_state.update(PRICING_DEFAULTS)
    st.session_state.quote_date = db.today()
    refresh_reference()


def apply_suggestions(sug: item_parser.Suggestions) -> None:
    if sug.markup_pct is not None:
        st.session_state.markup = sug.markup_pct
    if sug.discount_pct is not None:
        st.session_state.discount = sug.discount_pct
    if sug.vat_enabled is not None:
        st.session_state.vat_on = sug.vat_enabled
    if sug.vat_pct is not None:
        st.session_state.vat_pct = sug.vat_pct


def _init_state() -> None:
    for key, default in PRICING_DEFAULTS.items():
        st.session_state.setdefault(key, default)
    st.session_state.setdefault("quote_date", db.today())
    st.session_state.setdefault("default_availability", "Ex-stock")
    for n, title in OPTION_TITLES.items():
        st.session_state.setdefault(f"title_{n}", title)
    if "quote_ref" not in st.session_state:
        refresh_reference()


def _parse_keys(n: int, raw: str, default_availability: str) -> tuple[str, str]:
    """(editor key part, cache key) for a pasted text. The parser file's timestamp is part
    of the cache key so an updated parser re-reads the message."""
    text_key = hashlib.md5(f"{n}:{default_availability}:{raw}".encode("utf-8")).hexdigest()[:8]
    parser_version = int(Path(item_parser.__file__).stat().st_mtime)
    return text_key, f"parsed_{text_key}_{parser_version}"


def read_message(n: int, raw: str, default_availability: str) -> tuple[str, item_parser.ParseResult]:
    """Parse once per pasted text, so later saved-item updates don't reset staff edits."""
    text_key, cache_key = _parse_keys(n, raw, default_availability)
    if cache_key not in st.session_state:
        if not raw.strip():
            return text_key, item_parser.ParseResult()
        result = item_parser.parse_message(raw, default_availability)
        catalog.enrich(result.items, _store().catalog())
        st.session_state[cache_key] = result
    return text_key, st.session_state[cache_key]


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
def restore_draft(data: dict) -> None:
    for key in DRAFT_FIELDS:
        if key in data:
            st.session_state[key] = data[key]
    try:
        st.session_state.quote_date = dt.date.fromisoformat(data["quote_date"])
    except (KeyError, ValueError):
        pass
    for n, option in enumerate(data.get("options", []), 1):
        st.session_state[f"title_{n}"] = option["title"]
        st.session_state[f"raw_{n}"] = option["raw"]
        # show the rows exactly as they were edited, not a fresh read of the pasted text
        _, cache_key = _parse_keys(n, option["raw"], data.get("default_availability", "Ex-stock"))
        st.session_state[cache_key] = item_parser.ParseResult(items=option["rows"])
    st.session_state.draft_offer_done = True
    common.log("Continued unfinished quotation", data.get("client_name") or "(no client name yet)")


def discard_draft() -> None:
    _store().delete_draft(common.user()["username"])
    st.session_state.draft_offer_done = True


def _offer_draft(store: db.Store) -> None:
    """After signing in, offer to continue an unfinished quotation."""
    if st.session_state.get("draft_offer_done"):
        return
    form_empty = not any(st.session_state.get(k) for k in ("client_name", "raw_1", "raw_2"))
    draft = store.get_draft(common.user()["username"]) if form_empty else None
    if not draft:
        st.session_state.draft_offer_done = True
        return
    who = f"for **{draft['client_name']}**" if draft["client_name"] else "without a client name yet"
    with st.container(border=True, horizontal=True, vertical_alignment="center", key="draft_offer"):
        st.markdown(
            f":material/history: You have an unfinished quotation {who}, last changed "
            f"{draft['saved_at']:%d %b, %H:%M}."
        )
        st.button("Continue it", icon=":material/restore:", type="primary",
                  on_click=restore_draft, args=(draft["data"],), key="draft_restore")
        st.button("Discard", icon=":material/delete:", on_click=discard_draft, key="draft_discard")


def _save_draft(store: db.Store, draft: dict, draft_json: str) -> None:
    has_content = draft["client_name"].strip() or any(o["raw"].strip() or o["rows"] for o in draft["options"])
    if not has_content or draft_json == st.session_state.get("draft_saved_json"):
        return
    try:
        store.save_draft(common.user()["username"], draft["client_name"].strip(), draft)
    except SQLAlchemyError:
        st.caption(":orange[:material/cloud_off:] Could not save your draft just now. It will retry on your next change.")
        return
    st.session_state.draft_saved_json = draft_json


# ---------------------------------------------------------------------------
# Item editor for one option
# ---------------------------------------------------------------------------
def option_editor(n: int, pricing: qe.Pricing, default_availability: str):
    title = st.text_input("Proposal title", key=f"title_{n}")
    raw = st.text_area(
        "Paste items",
        key=f"raw_{n}",
        height=160,
        placeholder="Paste the WhatsApp message as it is, for example:\n"
        "We need sixteen 200AH batteries at 320k each, available in 2 weeks\n"
        "CAD50M7 changeover switch 50A, 2 pcs, 85k each ex-stock\n"
        "Installation & commissioning 250,000\nMarkup 15%, add VAT",
        help="Paste the message as staff wrote it. Greetings are ignored; quantities, prices "
        "(320k, 1.45m, ₦85,000), availability and instructions like 'markup 15%' are picked up.",
    )
    items.picture_box(f"raw_{n}")
    text_key, result = read_message(n, raw, default_availability)
    df = pd.DataFrame(result.items, columns=COLUMNS)

    sug = result.suggestions
    if sug.any():
        found = []
        if sug.markup_pct is not None:
            found.append(f"markup {sug.markup_pct:g}%")
        if sug.discount_pct is not None:
            found.append(f"discount {sug.discount_pct:g}%")
        if sug.vat_enabled is not None:
            found.append("VAT " + ("on" if sug.vat_enabled else "off"))
        if sug.vat_pct is not None:
            found.append(f"VAT rate {sug.vat_pct:g}%")
        already = (
            (sug.markup_pct is None or sug.markup_pct == pricing.markup_pct)
            and (sug.discount_pct is None or sug.discount_pct == pricing.discount_pct)
            and (sug.vat_enabled is None or sug.vat_enabled == pricing.vat_enabled)
            and (sug.vat_pct is None or sug.vat_pct == pricing.vat_pct)
        )
        with st.container(border=True, horizontal=True, vertical_alignment="center", key=f"suggest_{n}"):
            st.markdown(f":material/lightbulb: The message mentions **{', '.join(found)}**.")
            st.button(
                "Applied" if already else "Apply to pricing",
                key=f"apply_{n}",
                icon=":material/check:" if already else ":material/bolt:",
                disabled=already,
                on_click=apply_suggestions,
                args=(sug,),
            )

    st.markdown("**Review items** · edit any cell, add rows at the bottom, or select rows to delete them.")
    flagged = sum(1 for item in result.items if item["Note"] and item["Note"] != "Matched a saved item.")
    if flagged:
        st.caption(f":orange[:material/flag:] {flagged} row(s) have a note. Please check them before generating.")
    edited = st.data_editor(
        df,
        key=f"editor_{n}_{text_key}",
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "Model no.": st.column_config.TextColumn(width="small"),
            "Description": st.column_config.TextColumn(width="large", required=True),
            "Qty": st.column_config.NumberColumn(min_value=1, step=1, default=1, required=True),
            "Unit cost (₦)": st.column_config.NumberColumn(
                "Unit cost (₦, before markup)", min_value=0.0, format="accounting", default=0.0
            ),
            "Availability": st.column_config.TextColumn(default=default_availability),
            "Note": st.column_config.TextColumn(
                "Note", width="large", help="What the app guessed or matched. Not printed on the quotation."
            ),
        },
        disabled=["Note"],
    )
    rows = qe.clean_rows(edited.to_dict("records"))
    opt = qe.price_option(title, rows, pricing)

    if opt.lines:
        with st.container(horizontal=True, key=f"totals_{n}"):
            st.metric("Items", len(opt.lines), border=True)
            st.metric("Sub total", common.naira(opt.subtotal), border=True)
            if opt.discount:
                st.metric(f"Discount {pricing.discount_pct:g}%", f"-{common.naira(opt.discount)}", border=True)
            if pricing.vat_enabled:
                st.metric(f"VAT {pricing.vat_pct:g}%", common.naira(opt.vat), border=True)
            st.metric("Grand total", common.naira(opt.total), border=True)
        st.caption(f"**In words:** {qe.amount_in_words(opt.total)}")

        if pricing.markup_pct:
            with st.expander("Client prices after markup", icon=":material/visibility:"):
                lines_table(opt, alt=f"Option {n} items with marked-up client prices")
    return opt, rows


def lines_table(opt: qe.QuoteOption, alt: str) -> None:
    st.dataframe(
        pd.DataFrame(opt.lines).rename(
            columns={
                "sn": "S/N", "model": "Model no.", "desc": "Description", "qty": "Qty",
                "unit": "Unit cost (₦)", "total": "Total cost (₦)", "availability": "Availability",
            }
        ),
        hide_index=True,
        column_config={
            "Unit cost (₦)": st.column_config.NumberColumn(format="accounting"),
            "Total cost (₦)": st.column_config.NumberColumn(format="accounting"),
        },
        alt=alt,
    )


def _reference_conflict(ref: str, client_name: str) -> str | None:
    """A reference may be re-used only to revise the same client's quotation."""
    existing = _store().latest_for_reference(ref)
    if not existing:
        return None
    user = common.user()
    when = existing["created_at"].strftime("%d %b %Y")
    made_here = st.session_state.get("generated", {}).get("reference") == ref
    if made_here and existing["username"] == user["username"]:
        return None  # revising what was just generated, e.g. fixing a typo in the client name
    if existing["client_name"].strip().lower() != client_name.strip().lower():
        return (
            f"Reference **{ref}** was already used for **{existing['client_name']}** on {when}. "
            "Click **Use next number** in the sidebar for a new reference."
        )
    if existing["username"] not in (user["username"], "imported") and user["role"] != "admin":
        return (
            f"Reference **{ref}** belongs to another staff member's quotation. "
            "Click **Use next number** in the sidebar for a new reference."
        )
    return None


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------
def render() -> None:
    store = _store()
    user = common.user()
    _init_state()
    _offer_draft(store)

    # ---------------------------------------------------------------- sidebar
    with st.sidebar:
        st.markdown("**Quotation settings**")
        st.caption("Used by the Quotation form. Invoices and waybills have their own fields.")
        st.button(
            "Start a new quotation",
            icon=":material/note_add:",
            on_click=start_new_quote,
            width="stretch",
            help="Clears the client, items and pricing, and takes the next reference number.",
        )
        st.subheader("Client", icon=":material/business:")
        client_name = st.text_input("Client name", placeholder="e.g. PAL Pensions", key="client_name")
        client_address = st.text_area("Address", placeholder="Street\nCity, State", height=90, key="client_address")
        attention = st.text_input("Kind attention", placeholder="Contact person", key="attention")

        st.subheader("Quote details", icon=":material/description:")
        st.date_input("Date", key="quote_date", format="DD/MM/YYYY", on_change=refresh_reference)
        st.text_input(
            "Reference",
            key="quote_ref",
            help="Numbered automatically for you. To revise a quotation you sent before, "
            "type its reference: it is saved as a new revision, never overwritten.",
        )
        st.button("Use next number", icon=":material/autorenew:", on_click=refresh_reference, type="tertiary")

        st.subheader("Pricing", icon=":material/payments:")
        markup = st.number_input("Markup (%)", min_value=0.0, step=5.0, key="markup")
        discount = st.number_input("Discount (%)", min_value=0.0, max_value=100.0, step=0.5, key="discount")
        vat_on = st.toggle("Charge VAT", key="vat_on")
        vat_pct = st.number_input("VAT rate (%)", min_value=0.0, step=0.5, disabled=not vat_on, key="vat_pct")
        default_availability = st.text_input(
            "Default availability",
            key="default_availability",
            help="Pre-filled for each new item. You can change it per item in the table.",
        )

        st.subheader("Options", icon=":material/alt_route:")
        two_options = st.toggle("Offer an alternative (Option 2)", key="two_options")

    pricing = qe.Pricing(markup, discount, vat_on, vat_pct)

    # ---------------------------------------------------------------- items
    if two_options:
        t1, t2 = st.tabs(["Option 1", "Option 2"])
    else:
        t1, t2 = st.container(), None
    with t1:
        option_1, rows_1 = option_editor(1, pricing, default_availability)
    options, option_rows = [option_1], [rows_1]
    if t2 is not None:
        with t2:
            option_2, rows_2 = option_editor(2, pricing, default_availability)
        options.append(option_2)
        option_rows.append(rows_2)

    # ---------------------------------------------------------------- generate
    st.subheader("Generate quotation", icon=":material/task:")
    problems, cautions = [], []
    if not client_name.strip():
        problems.append("Enter the client name in the sidebar.")
    if not st.session_state.quote_ref.strip():
        problems.append("Enter a reference in the sidebar, or click Use next number.")
    for i, opt in enumerate(options, 1):
        if not opt.lines:
            problems.append(f"Option {i} has no items.")
        zero = [line["desc"] or line["model"] for line in opt.lines if line["unit"] == 0]
        if zero:
            cautions.append(f"Option {i}: no price for {', '.join(zero[:3])}{'…' if len(zero) > 3 else ''}.")
        descs = [line["desc"].lower() for line in opt.lines]
        if len(descs) != len(set(descs)):
            cautions.append(f"Option {i} lists the same item more than once.")
    for p in problems:
        st.warning(p, icon=":material/info:")
    for c in cautions:
        st.caption(f":orange[:material/warning:] {c}")

    ref = st.session_state.quote_ref.strip()
    header = {
        "ref_number": ref,
        "date": qe.format_date(st.session_state.quote_date),
        "client_name": client_name.strip(),
        "client_address": client_address.strip(),
        "attention": attention.strip(),
    }
    fingerprint = hashlib.md5(
        json.dumps([header, pricing.__dict__, [(o.title, o.lines) for o in options]], sort_keys=True, default=str).encode()
    ).hexdigest()

    draft = {key: st.session_state.get(key) for key in DRAFT_FIELDS}
    draft["quote_date"] = st.session_state.quote_date.isoformat()
    draft["options"] = [
        {"title": o.title, "raw": st.session_state.get(f"raw_{n}", ""), "rows": rows}
        for n, (o, rows) in enumerate(zip(options, option_rows), 1)
    ]
    draft_json = json.dumps(draft, sort_keys=True, default=str)

    if st.button("Generate quotation", type="primary", icon=":material/bolt:", disabled=bool(problems), key="generate"):
        template = store.latest_template()
        conflict = _reference_conflict(ref, client_name)
        if conflict:
            st.error(conflict, icon=":material/error:")
        elif template is None:
            st.error(
                "The company Word template has not been uploaded yet. "
                + ("Upload it in **Admin › Template**." if common.is_admin() else "Please ask the admin to upload it."),
                icon=":material/error:",
            )
        else:
            _generate(store, user, template, header, options, option_rows, pricing, fingerprint, draft_json)

    generated = st.session_state.get("generated")
    if generated and not problems:
        for w in generated["warnings"]:
            st.warning(w, icon=":material/warning:")
        if generated["fingerprint"] == fingerprint:
            saved_as = generated["reference"] + (f" (revision {generated['revision']})" if generated["revision"] > 1 else "")
            st.caption(f":green[:material/cloud_done:] Saved to history as **{saved_as}**.")
            common.download_buttons(generated["data"], generated.get("pdf"), generated["file_name"], "download")
        else:
            st.caption(
                ":material/sync_problem: You changed something since the last document was "
                "generated. Generate again to download the updated version (saved as a new revision)."
            )

    _save_draft(store, draft, draft_json)


def _generate(store, user, template, header, options, option_rows, pricing, fingerprint, draft_json) -> None:
    ref = header["ref_number"]
    with st.status("Building your quotation…", expanded=True) as progress:
        st.write(":material/table_rows: Adding items and totals")
        data, warnings = qe.build_quotation(common.template_bytes(template["id"]), header, options, pricing)
        st.write(":material/cloud_upload: Saving to the company history")
        payload = qe.quote_payload(
            header, st.session_state.quote_date, pricing, [o.title for o in options], option_rows
        )
        try:
            revision = store.save_quote(
                reference=ref,
                username=user["username"],
                client_name=header["client_name"],
                title=options[0].title.strip(),
                options_count=len(options),
                items_count=sum(len(o.lines) for o in options),
                grand_total=options[0].total,
                template_id=template["id"],
                data=payload,
            )
            store.log(
                user["username"],
                "Generated quotation",
                f"{ref}{f' rev {revision}' if revision > 1 else ''} · {header['client_name']} · "
                f"{common.naira(options[0].total)}"
                + (f" (+ option 2: {common.naira(options[1].total)})" if len(options) > 1 else ""),
            )
            entries = store.catalog()
            changed = catalog.learn(entries, [r for rows in option_rows for r in rows], st.session_state.quote_date)
            store.upsert_catalog(changed)
            store.delete_draft(user["username"])  # finished: no longer a draft
        except SQLAlchemyError:
            progress.update(label="Could not save the quotation", state="error")
            st.error(
                "The quotation could not be saved to the company history (database not reachable), "
                "so it was not released. Check the internet connection and try again.",
                icon=":material/cloud_off:",
            )
            return
        st.write(":material/picture_as_pdf: Making the PDF copy")
        pdf = common.to_pdf(data)
        progress.update(label="Quotation ready", state="complete", expanded=False)
    st.session_state.draft_saved_json = draft_json
    st.session_state.generated = {
        "fingerprint": fingerprint,
        "data": data,
        "pdf": pdf,
        "file_name": f"Quotation_{qe.safe_filename(header['client_name'])}_{qe.safe_filename(ref)}"
        + (f"_rev{revision}" if revision > 1 else "") + ".docx",
        "warnings": warnings,
        "reference": ref,
        "revision": revision,
    }
    st.toast(
        f"Quotation saved and ready to download. {len(changed)} item(s) remembered for next time.",
        icon=":material/check_circle:",
    )
