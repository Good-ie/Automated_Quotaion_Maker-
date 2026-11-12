"""Invoices and waybills: created from a saved quotation and saved permanently.

All staff can create them; the admin sees every invoice and the monthly figures.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

import invoice_engine as ie
import quote_engine as qe
import store as db
from views import common

STATUS_LABELS = {"unpaid": "Unpaid", "part_paid": "Part paid", "paid": "Paid", "cancelled": "Cancelled"}
LINE_COLUMNS = ["Model no.", "Qty", "Description", "Unit price (₦)"]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LOGO_FILE = Path(__file__).resolve().parent.parent / "assets" / "resypl_logo.png"


# ---------------------------------------------------------------------------
# Company details, logo and signature
# ---------------------------------------------------------------------------
def company_details() -> dict:
    return {**ie.DEFAULT_COMPANY, **common.get_store().company()}


def logo_bytes() -> bytes | None:
    custom = common.get_store().get_image("logo")
    if custom:
        return custom
    return LOGO_FILE.read_bytes() if LOGO_FILE.exists() else None


@st.cache_data(max_entries=4, show_spinner=False)
def _signature_from_template(template_id: int) -> bytes | None:
    data = common.template_bytes(template_id)
    return ie.extract_signature(data) if data else None


def signature_bytes() -> bytes | None:
    store = common.get_store()
    custom = store.get_image("signature")
    if custom:
        return custom
    template = store.latest_template()
    return _signature_from_template(template["id"]) if template else None


def invoice_document(record: dict) -> bytes:
    inv = ie.Invoice.from_dict(record["data"]["invoice"])
    return ie.build_invoice(inv, record["data"]["company"], logo_bytes(), signature_bytes())


def waybill_document(record: dict, delivery_date: dt.date) -> bytes:
    inv = ie.Invoice.from_dict(record["data"]["invoice"])
    return ie.build_waybill(inv, delivery_date, record["data"]["company"], logo_bytes(), signature_bytes())


def _file_name(kind: str, record: dict) -> str:
    return f"{kind}_{qe.safe_filename(record['client_name'])}_{qe.safe_filename(record['number'])}.docx"


# ---------------------------------------------------------------------------
# On a quotation: list its invoices and create a new one
# ---------------------------------------------------------------------------
def quote_section(q: dict) -> None:
    store = common.get_store()
    st.markdown("**Invoices for this quotation**")

    just_made = st.session_state.get("new_invoice")
    if just_made and just_made["quote_id"] == q["id"]:
        st.success(f"Invoice **{just_made['number']}** saved.", icon=":material/check_circle:")
        st.download_button("Download invoice", data=just_made["data"], file_name=just_made["file_name"],
                           mime=DOCX, icon=":material/download:", on_click="ignore", type="primary",
                           key=f"new_invoice_download_{q['id']}")
        st.caption("For the waybill or to record payment, open the **Invoices** tab.")

    existing = store.invoices_for_quote(q["id"])
    if existing:
        st.dataframe(
            pd.DataFrame(existing).assign(status=lambda d: d["status"].map(STATUS_LABELS)),
            hide_index=True,
            column_order=["number", "invoice_date", "amount_due", "amount_paid", "status"],
            column_config={
                "number": "Invoice", "invoice_date": "Date",
                "amount_due": st.column_config.NumberColumn("Amount (₦)", format="accounting"),
                "amount_paid": st.column_config.NumberColumn("Paid (₦)", format="accounting"),
                "status": "Status",
            },
            alt=f"Invoices for {q['reference']}",
        )
    if q["status"] != "active" or not q["data"]:
        if not existing:
            st.caption("Invoices can only be made from an active quotation with its items saved.")
        return
    label = "Create invoice" if not existing else "Create another invoice (next part or balance)"
    with st.expander(label, icon=":material/receipt_long:", expanded=not existing):
        _create_form(q)


def _create_form(q: dict) -> None:
    store = common.get_store()
    user = common.user()
    payload = q["data"]
    options, pricing = qe.options_from_payload(payload)
    head = payload["header"]
    k = f"inv_{q['id']}"

    if len(options) > 1:
        idx = st.radio("Which option did the client accept?", list(range(len(options))), horizontal=True,
                       format_func=lambda i: f"Option {i + 1}: {options[i].title}", key=f"{k}_opt")
    else:
        idx = 0
    opt = options[idx]
    already = store.invoiced_so_far(q["id"], idx)

    c1, c2, c3 = st.columns(3)
    inv_date = c1.date_input("Invoice date", value=db.today(), format="DD/MM/YYYY", key=f"{k}_date")
    days = c2.number_input("Payment terms (days)", min_value=0, max_value=365, value=45, step=5, key=f"{k}_days",
                           help="Printed as e.g. '45 Days Net'. The due date is worked out from it.")
    attention = c3.text_input("Attention (ATTN TO)", value=head.get("attention", ""), key=f"{k}_attn")
    c1, c2 = st.columns(2)
    lpo_number = c1.text_input("LPO number", placeholder="e.g. PO-16967-01", key=f"{k}_lpo")
    lpo_date = c2.date_input("LPO date", value=None, format="DD/MM/YYYY", key=f"{k}_lpo_date")
    client_name = st.text_input("Client", value=q["client_name"], key=f"{k}_client")
    client_address = st.text_area("Client address", value=head.get("client_address", ""), height=80, key=f"{k}_addr")

    st.markdown("**Items** (client prices after markup). Change them if the LPO differs from the quotation.")
    df = pd.DataFrame(
        [{"Model no.": ln["model"], "Qty": ln["qty"], "Description": ln["desc"], "Unit price (₦)": ln["unit"]}
         for ln in opt.lines],
        columns=LINE_COLUMNS,
    )
    edited = st.data_editor(
        df, key=f"{k}_lines_{idx}", num_rows="dynamic", hide_index=True,
        column_config={
            "Model no.": st.column_config.TextColumn(width="small"),
            "Qty": st.column_config.NumberColumn(min_value=1, step=1, default=1, required=True),
            "Description": st.column_config.TextColumn(width="large", required=True),
            "Unit price (₦)": st.column_config.NumberColumn(min_value=0.0, format="accounting", default=0.0),
        },
    )
    c1, c2, c3 = st.columns(3)
    discount = c1.number_input("Discount (%)", min_value=0.0, max_value=100.0, value=float(pricing.discount_pct),
                               step=0.5, key=f"{k}_discount")
    vat_on = c2.toggle("Charge VAT", value=bool(pricing.vat_enabled), key=f"{k}_vat_on")
    vat_pct = c3.number_input("VAT (%)", min_value=0.0, value=float(pricing.vat_pct), step=0.5,
                              disabled=not vat_on, key=f"{k}_vat")

    st.markdown("**How much to invoice now**")
    mode = st.segmented_control(
        "Amount", ["Full / remaining", "Percentage", "Fixed amount"], default="Full / remaining",
        key=f"{k}_mode", label_visibility="collapsed",
    ) or "Full / remaining"
    part_pct = part_amt = None
    if mode == "Percentage":
        part_pct = st.number_input("Percentage of the contract total (%)", min_value=0.5, max_value=100.0,
                                   value=50.0, step=5.0, key=f"{k}_pct")
    elif mode == "Fixed amount":
        part_amt = st.number_input("Amount to invoice (₦)", min_value=0.0, value=0.0, step=10_000.0,
                                   format="%.2f", key=f"{k}_amt")

    lines = [
        ie.InvoiceLine(r["Model no."], r["Qty"], r["Description"], r["Unit cost (₦)"])
        for r in qe.clean_rows(edited.rename(columns={"Unit price (₦)": "Unit cost (₦)"}).to_dict("records"))
    ]
    inv = ie.Invoice(
        number="", date=inv_date, terms=f"{int(days)} Days Net", lpo_number=lpo_number.strip(),
        lpo_date=ie.format_date(lpo_date) if lpo_date else "", attention=attention.strip(),
        client_name=client_name.strip(), client_address=client_address.strip(), lines=lines,
        discount_pct=discount, vat_enabled=vat_on, vat_pct=vat_pct, part_percent=part_pct,
        part_amount=part_amt or None, previously_invoiced=already,
    )
    totals = inv.compute()
    remaining = round(totals["contract_total"] - already, 2)

    with st.container(horizontal=True):
        st.metric("Contract total", common.naira(totals["contract_total"]), border=True)
        if already:
            st.metric("Already invoiced", common.naira(already), border=True)
        st.metric("This invoice", common.naira(totals["due"]), border=True)
        st.metric("Balance after", common.naira(max(totals["balance"], 0)), border=True)
    st.caption(f"**In words:** {ie.words(totals['due']).capitalize()}")

    problems = []
    if mode == "Fixed amount" and not part_amt:
        problems.append("Enter the amount to invoice.")
    if not lines:
        problems.append("Add at least one item.")
    if not client_name.strip():
        problems.append("Enter the client name.")
    if totals["due"] <= 0:
        problems.append("Nothing left to invoice for this quotation." if already else "The amount to invoice is zero.")
    elif totals["due"] > remaining + 0.005:
        problems.append(f"This is more than what is left to invoice ({common.naira(remaining)}).")
    for p in problems:
        st.warning(p, icon=":material/info:")

    if st.button("Generate invoice", type="primary", icon=":material/receipt_long:", disabled=bool(problems),
                 key=f"{k}_go"):
        company = company_details()

        def document_data(number: str) -> dict:
            inv.number = number
            return {"invoice": inv.to_dict(), "company": company}

        due_date = inv_date + dt.timedelta(days=int(days))
        new_id, number = store.save_invoice(
            on=inv_date, data_for_number=document_data, quote_id=q["id"], quote_reference=q["reference"],
            option_index=idx, username=user["username"], client_name=inv.client_name,
            invoice_date=inv_date.isoformat(), due_date=due_date.isoformat(),
            contract_total=totals["contract_total"], amount_due=totals["due"],
        )
        kind = (f"{part_pct:g}% part payment" if part_pct else "part payment" if part_amt
                else "balance" if already else "full")
        common.log("Created invoice", f"{number} · {inv.client_name} · {common.naira(totals['due'])} ({kind}) "
                                      f"from {q['reference']}")
        record = store.get_invoice(new_id)
        st.session_state.new_invoice = {
            "quote_id": q["id"], "number": number, "data": invoice_document(record),
            "file_name": _file_name("Invoice", record),
        }
        st.rerun()


# ---------------------------------------------------------------------------
# The Invoices tab
# ---------------------------------------------------------------------------
def _frame(rows: list[dict], names: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    live = df["status"] != "cancelled"
    df["outstanding"] = (df["amount_due"] - df["amount_paid"]).where(live, 0.0).clip(lower=0)
    df["overdue"] = live & (df["outstanding"] > 0) & (df["due_date"] < db.today().isoformat())
    df["status_label"] = df["status"].map(STATUS_LABELS) + df["overdue"].map({True: " · overdue", False: ""})
    df["staff"] = df["username"].map(lambda u: names.get(u, u))
    return df


def render() -> None:
    store = common.get_store()
    user = common.user()
    admin = common.is_admin()
    rows = store.list_invoices(None if admin else user["username"])
    if not rows:
        st.caption(
            "No invoices yet. To make one, open **" + ("All" if admin else "My")
            + " quotations**, select the quotation and click **Create invoice**."
        )
        return
    names = {u["username"]: u["full_name"] for u in store.list_users()}
    df = _frame(rows, names)
    live = df[df["status"] != "cancelled"]

    with st.container(horizontal=True):
        st.metric("Invoiced", common.naira(live["amount_due"].sum()), border=True)
        st.metric("Received", common.naira(live["amount_paid"].sum()), border=True)
        st.metric("Outstanding", common.naira(live["outstanding"].sum()), border=True)
        st.metric("Overdue invoices", int(live["overdue"].sum()), border=True)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("Search", placeholder="Client, invoice or quote reference", key="inv_search")
        show = st.segmented_control("Show", ["Not fully paid", "Paid", "Cancelled", "All"], default="All",
                                    key="inv_show") or "All"
    view = df
    if query:
        mask = view[["number", "client_name", "quote_reference"]].apply(
            lambda c: c.astype(str).str.contains(query, case=False, regex=False)).any(axis=1)
        view = view[mask]
    if show == "Not fully paid":
        view = view[view["status"].isin(["unpaid", "part_paid"])]
    elif show == "Paid":
        view = view[view["status"] == "paid"]
    elif show == "Cancelled":
        view = view[view["status"] == "cancelled"]

    event = st.dataframe(
        view, key="inv_table", hide_index=True, on_select="rerun", selection_mode="single-row",
        column_order=["invoice_date", "number", "client_name", "amount_due", "amount_paid", "due_date",
                      "status_label", "quote_reference"] + (["staff"] if admin else []),
        column_config={
            "invoice_date": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
            "number": "Invoice", "client_name": "Client",
            "amount_due": st.column_config.NumberColumn("Amount (₦)", format="accounting"),
            "amount_paid": st.column_config.NumberColumn("Paid (₦)", format="accounting"),
            "due_date": st.column_config.DateColumn("Due", format="DD MMM YYYY"),
            "status_label": "Status", "quote_reference": "Quotation", "staff": "Created by",
        },
        alt="Invoices",
    )
    if not event.selection.rows:
        st.caption(":material/touch_app: Select an invoice to download it, make its waybill or record payment.")
        return
    _detail(int(view.iloc[event.selection.rows[0]]["id"]), names)


def _detail(invoice_id: int, names: dict) -> None:
    store = common.get_store()
    rec = store.get_invoice(invoice_id)
    if rec is None:
        st.warning("That invoice no longer exists.")
        return
    inv = ie.Invoice.from_dict(rec["data"]["invoice"])
    k = f"invd_{invoice_id}"
    with st.container(border=True):
        st.subheader(rec["number"], icon=":material/receipt_long:")
        with st.container(horizontal=True):
            st.metric("Client", rec["client_name"], border=True)
            st.metric("Amount", common.naira(rec["amount_due"]), border=True)
            st.metric("Paid", common.naira(rec["amount_paid"]), border=True)
            st.metric("Status", STATUS_LABELS.get(rec["status"], rec["status"]), border=True)
        st.caption(
            f"Invoice date {dt.date.fromisoformat(rec['invoice_date']):%d %b %Y} · due "
            f"{dt.date.fromisoformat(rec['due_date']):%d %b %Y} · quotation {rec['quote_reference']}"
            + (f" · LPO {inv.lpo_number}" if inv.lpo_number else "")
            + f" · created by {names.get(rec['username'], rec['username'])}"
        )
        if rec["status"] == "cancelled":
            st.warning(f"Cancelled: {rec['cancel_reason'] or 'no reason given'}", icon=":material/block:")

        doc_col, waybill_col = st.columns(2, gap="large")
        with doc_col:
            st.markdown("**Invoice document**")
            ready = st.session_state.get("invoice_ready")
            if ready and ready["id"] == invoice_id:
                st.download_button("Download invoice", data=ready["data"], file_name=ready["file_name"], mime=DOCX,
                                   icon=":material/download:", on_click="ignore", key=f"{k}_dl")
            elif st.button("Prepare invoice", icon=":material/description:", key=f"{k}_prep"):
                st.session_state.invoice_ready = {"id": invoice_id, "data": invoice_document(rec),
                                                  "file_name": _file_name("Invoice", rec)}
                common.log("Downloaded invoice", f"{rec['number']} · {rec['client_name']}")
                st.rerun()
        with waybill_col:
            st.markdown("**Waybill / delivery note**")
            default = dt.date.fromisoformat(rec["waybill_date"]) if rec["waybill_date"] else db.today()
            on = st.date_input("Delivery date", value=default, format="DD/MM/YYYY", key=f"{k}_wb_date")
            ready = st.session_state.get("waybill_ready")
            if ready and ready["id"] == invoice_id and ready["date"] == on:
                st.download_button("Download waybill", data=ready["data"], file_name=ready["file_name"], mime=DOCX,
                                   icon=":material/download:", on_click="ignore", key=f"{k}_wb_dl")
            elif st.button("Prepare waybill", icon=":material/local_shipping:", key=f"{k}_wb_prep"):
                store.set_waybill_date(invoice_id, on)
                st.session_state.waybill_ready = {"id": invoice_id, "date": on,
                                                  "data": waybill_document(rec, on),
                                                  "file_name": _file_name("Waybill", rec)}
                common.log("Made waybill", f"{rec['number']} · {rec['client_name']} · delivered {on:%d %b %Y}")
                st.rerun()

        if rec["status"] != "cancelled":
            st.markdown("**Payment**")
            with st.container(horizontal=True, vertical_alignment="bottom"):
                paid = st.number_input("Amount received so far (₦)", min_value=0.0, value=float(rec["amount_paid"]),
                                       step=10_000.0, format="%.2f", key=f"{k}_paid")
                if st.button("Save payment", icon=":material/payments:", key=f"{k}_pay"):
                    status = store.set_invoice_payment(invoice_id, paid)
                    common.log("Recorded payment", f"{rec['number']} · {common.naira(paid)} received "
                                                   f"({STATUS_LABELS[status].lower()})")
                    st.toast("Payment saved", icon=":material/check_circle:")
                    st.rerun()
                if rec["status"] != "paid" and st.button("Mark fully paid", icon=":material/done_all:",
                                                         key=f"{k}_full"):
                    store.set_invoice_payment(invoice_id, rec["amount_due"])
                    common.log("Recorded payment", f"{rec['number']} · paid in full ({common.naira(rec['amount_due'])})")
                    st.rerun()

        if common.is_admin() and rec["status"] != "cancelled":
            with st.expander("Cancel this invoice", icon=":material/block:"):
                st.caption("It stays in the records (marked cancelled), is left out of the totals, and its "
                           "number is never re-used. The amount can then be invoiced again.")
                reason = st.text_input("Reason", key=f"{k}_cancel_reason")
                if st.button("Cancel invoice", key=f"{k}_cancel", disabled=not reason.strip()):
                    store.cancel_invoice(invoice_id, reason.strip())
                    common.log("Cancelled invoice", f"{rec['number']} · {reason.strip()}")
                    st.rerun()


# ---------------------------------------------------------------------------
# Admin: money per month
# ---------------------------------------------------------------------------
def monthly_summary(users: list[dict]) -> None:
    rows = common.get_store().list_invoices()
    if not rows:
        st.caption("No invoices yet.")
        return
    names = {u["username"]: u["full_name"] for u in users}
    df = _frame(rows, names)
    df = df[df["status"] != "cancelled"].copy()
    if df.empty:
        st.caption("No invoices yet (all cancelled).")
        return
    df["month"] = pd.to_datetime(df["invoice_date"]).dt.to_period("M")
    monthly = (
        df.groupby("month")
        .agg(invoices=("id", "count"), invoiced=("amount_due", "sum"), received=("amount_paid", "sum"),
             outstanding=("outstanding", "sum"))
        .sort_index(ascending=False)
        .head(12)
        .reset_index()
    )
    monthly["month"] = monthly["month"].dt.strftime("%B %Y")
    st.markdown("**Invoiced per month**")
    st.dataframe(
        monthly, hide_index=True,
        column_config={
            "month": "Month", "invoices": st.column_config.NumberColumn("Invoices"),
            "invoiced": st.column_config.NumberColumn("Invoiced (₦)", format="accounting"),
            "received": st.column_config.NumberColumn("Received (₦)", format="accounting"),
            "outstanding": st.column_config.NumberColumn("Outstanding (₦)", format="accounting"),
        },
        alt="Invoiced, received and outstanding per month",
    )
    this_month = df[df["month"] == pd.Period(db.today(), "M")]
    if len(this_month):
        per_staff = (
            this_month.groupby("staff")
            .agg(invoices=("id", "count"), invoiced=("amount_due", "sum"), received=("amount_paid", "sum"))
            .reset_index()
            .sort_values("invoiced", ascending=False)
        )
        st.markdown(f"**Invoiced in {db.today():%B} by staff**")
        st.dataframe(
            per_staff, hide_index=True,
            column_config={
                "staff": "Staff", "invoices": st.column_config.NumberColumn("Invoices"),
                "invoiced": st.column_config.NumberColumn("Invoiced (₦)", format="accounting"),
                "received": st.column_config.NumberColumn("Received (₦)", format="accounting"),
            },
            alt="Invoiced this month per staff member",
        )
    overdue = df[df["overdue"]]
    if len(overdue):
        st.markdown(f":orange[:material/schedule:] **Overdue ({len(overdue)})**")
        st.dataframe(
            overdue, hide_index=True,
            column_order=["number", "client_name", "due_date", "outstanding", "staff"],
            column_config={
                "number": "Invoice", "client_name": "Client",
                "due_date": st.column_config.DateColumn("Was due", format="DD MMM YYYY"),
                "outstanding": st.column_config.NumberColumn("Outstanding (₦)", format="accounting"),
                "staff": "Created by",
            },
            alt="Overdue invoices",
        )
