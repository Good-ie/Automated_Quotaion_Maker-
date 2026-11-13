"""Quotation history: staff see their own, the admin sees everyone's. Any saved
quotation can be opened, checked and downloaded again."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import quote_engine as qe
from views import common, invoices
from views.quote import lines_table

STATUS_LABELS = {"active": "Sent", "void": "Cancelled", "legacy": "Old record"}


def render() -> None:
    store = common.get_store()
    user = common.user()
    admin = common.is_admin()

    rows = store.list_quotes(None if admin else user["username"])
    if not rows:
        st.caption("Quotations you generate are saved here permanently.")
        return
    names = {u["username"]: u["full_name"] for u in store.list_users()} if admin else {}
    names.update({"imported": "Imported", "system": "System"})

    df = pd.DataFrame(rows)
    df["staff"] = df["username"].map(lambda u: names.get(u, u))
    df["status_label"] = df["status"].map(lambda s: STATUS_LABELS.get(s, s))

    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("Search", placeholder="Client, reference or title", key="hist_search")
        if admin:
            staff = st.multiselect("Staff", sorted(df["staff"].unique()), key="hist_staff", placeholder="Everyone")
        else:
            staff = []
        status = st.segmented_control(
            "Show", ["Sent", "Cancelled", "All"], default="Sent", key="hist_status"
        )

    view = df
    if query:
        mask = view[["reference", "client_name", "title"]].apply(
            lambda c: c.astype(str).str.contains(query, case=False, regex=False)
        ).any(axis=1)
        view = view[mask]
    if staff:
        view = view[view["staff"].isin(staff)]
    if status == "Sent":
        view = view[view["status"] != "void"]
    elif status == "Cancelled":
        view = view[view["status"] == "void"]

    st.caption(f"{len(view)} quotation(s) · total value {common.naira(view.loc[view['status'] != 'void', 'grand_total'].sum())}")
    event = st.dataframe(
        view,
        key="hist_table",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_order=["created_at", "reference", "revision", "client_name", "title", "grand_total", "staff", "status_label"],
        column_config={
            "created_at": st.column_config.DatetimeColumn("Date", format="DD MMM YYYY, HH:mm"),
            "reference": "Reference",
            "revision": st.column_config.NumberColumn("Rev.", width="small"),
            "client_name": "Client",
            "title": "Proposal",
            "grand_total": st.column_config.NumberColumn("Grand total (₦)", format="accounting"),
            "staff": "Prepared by",
            "status_label": "Status",
        },
        alt="Saved quotations",
    )
    selected = event.selection.rows
    if not selected:
        st.caption(":material/touch_app: Select a row to see its items and download it again.")
        return
    _detail(int(view.iloc[selected[0]]["id"]), names)


def _detail(quote_id: int, names: dict) -> None:
    store = common.get_store()
    q = store.get_quote(quote_id)
    if q is None:
        st.warning("That quotation no longer exists.")
        return
    user = common.user()

    with st.container(border=True):
        label = q["reference"] + (f" · revision {q['revision']}" if q["revision"] > 1 else "")
        st.subheader(label, icon=":material/receipt_long:")
        with st.container(horizontal=True):
            st.metric("Client", q["client_name"], border=True)
            st.metric("Grand total", common.naira(q["grand_total"]), border=True)
            st.metric("Prepared by", names.get(q["username"], q["username"]), border=True)
            st.metric("Date", q["created_at"].strftime("%d %b %Y, %H:%M"), border=True)
        if q["status"] == "void":
            st.warning(f"Cancelled: {q['void_reason'] or 'no reason given'}", icon=":material/block:")

        payload = q["data"]
        if not payload:
            st.info(
                "This quotation was made before full history was kept, so only its summary is available.",
                icon=":material/info:",
            )
        else:
            options, pricing = qe.options_from_payload(payload)
            adjustments = [f"markup {pricing.markup_pct:g}%"] if pricing.markup_pct else []
            if pricing.discount_pct:
                adjustments.append(f"discount {pricing.discount_pct:g}%")
            if pricing.extra_discount:
                adjustments.append(f"additional discount {common.naira(pricing.extra_discount)}")
            if pricing.vat_enabled:
                adjustments.append(f"VAT {pricing.vat_pct:g}%")
            head = payload["header"]
            st.caption(
                f"{head.get('client_address', '').replace(chr(10), ', ')}"
                + (f" · Attn: {head['attention']}" if head.get("attention") else "")
                + (f" · Pricing: {', '.join(adjustments)}" if adjustments else "")
            )
            for i, opt in enumerate(options, 1):
                st.markdown(f"**Option {i}: {opt.title}** · {common.naira(opt.total)}")
                lines_table(opt, alt=f"Items in option {i} of {q['reference']}")

            prepared = st.session_state.get("past_doc")
            if prepared and prepared["id"] == quote_id:
                common.download_buttons(prepared["data"], prepared.get("pdf"), prepared["file_name"], "past_download")
            elif st.button("Prepare documents", icon=":material/description:", key=f"prepare_{quote_id}",
                           help="Makes the quotation again exactly as it was sent, as PDF and Word."):
                _prepare(q)
                st.rerun()

            st.divider()
            invoices.quote_section(q)

        if common.is_admin() and q["status"] == "active":
            with st.expander("Cancel this quotation", icon=":material/block:"):
                st.caption("It stays in the history (marked cancelled) and its reference number is never re-used.")
                reason = st.text_input("Reason", key=f"void_reason_{quote_id}")
                if st.button("Cancel quotation", key=f"void_{quote_id}", disabled=not reason.strip()):
                    store.void_quote(quote_id, reason.strip())
                    common.log("Cancelled quotation", f"{q['reference']} rev {q['revision']} · {reason.strip()}")
                    st.toast("Quotation marked as cancelled", icon=":material/check_circle:")
                    st.rerun()


def _prepare(q: dict) -> None:
    store = common.get_store()
    template_id = q["template_id"]
    data = common.template_bytes(template_id) if template_id else None
    note = ""
    if data is None:
        latest = store.latest_template()
        if latest is None:
            st.error("No template is stored. Ask the admin to upload it.", icon=":material/error:")
            return
        data = common.template_bytes(latest["id"])
        note = " (original template missing, used current one)"
    with st.spinner("Rebuilding the document (PDF and Word)…"):
        doc, _ = qe.rebuild_quotation(data, q["data"])
        pdf = common.to_pdf(doc)
    st.session_state.past_doc = {
        "id": q["id"],
        "data": doc,
        "pdf": pdf,
        "file_name": f"Quotation_{qe.safe_filename(q['client_name'])}_{qe.safe_filename(q['reference'])}"
        + (f"_rev{q['revision']}" if q["revision"] > 1 else "") + ".docx",
    }
    common.log("Downloaded past quotation", f"{q['reference']} rev {q['revision']} · {q['client_name']}{note}")
