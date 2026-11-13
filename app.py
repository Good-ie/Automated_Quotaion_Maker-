"""RESYPL Quotation Maker: entry point.

Run locally:   streamlit run app.py
Online:        deployed on Streamlit Community Cloud, data kept in Supabase (see README).
"""
import sys
from pathlib import Path

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from views import admin, auth, common, history, invoices, quote, saved_items

st.set_page_config(
    page_title="RESYPL Quotation Maker",
    page_icon=str(Path(__file__).with_name("assets") / "logo_mark.png"),
    layout="wide",
)
st.html(Path(__file__).with_name("assets") / "animations.css")

try:
    store = common.get_store()
    if not st.session_state.get("has_accounts"):
        st.session_state.has_accounts = store.user_count() > 0
except SQLAlchemyError as exc:
    common._connect.clear()  # try again from scratch on the next refresh
    print(f"Database connection failed: {exc!r}", file=sys.stderr)  # full detail: app logs only
    common.hero("Rehoboth Systems & Project Limited")
    st.error(
        "Cannot reach the database. Check the internet connection, or (admin) check that the "
        "Supabase project is not paused, then refresh this page.",
        icon=":material/cloud_off:",
    )
    st.caption(f"**Admin hint:** {common.db_error_hint(exc)}")
    st.stop()

# ---------------------------------------------------------------- sign-in gate
auth.forget_cookie_if_needed()
if not st.session_state.has_accounts:
    auth.setup_screen(store)
    st.stop()
if "user" not in st.session_state:
    auth.resume_from_cookie(store)  # a page refresh within 10 minutes keeps you signed in
if "user" not in st.session_state:
    auth.forget_cookie_if_needed()
    if reason := st.session_state.pop("signed_out_reason", None):
        st.warning(reason, icon=":material/info:")
    auth.login_screen(store)
    st.stop()
if not auth.keep_alive(store):  # 10-minute idle limit, account re-check, renew the key
    st.rerun()
auth.idle_watch()
if st.session_state.user["must_change_password"]:
    auth.forced_password_change(store)
    st.stop()

# ---------------------------------------------------------------- the app
auth.sidebar_account(store)
is_admin = common.is_admin()
common.hero(
    "Rehoboth Systems & Project Limited · Quotations, invoices and waybills from pasted text or pictures, "
    "ready as PDF or Word."
)

names = [
    "New document",
    "All quotations" if is_admin else "My quotations",
    "All invoices" if is_admin else "My invoices",
    "Saved items",
]
if is_admin:
    names.append("Admin")
tabs = st.tabs(names, key="main_tab", on_change="rerun")

# Work in progress always stays live, so switching tabs never loses it.
# The other tabs only load from the database when they are opened.
with tabs[0]:
    make_quote, make_invoice, make_waybill = st.tabs(
        [":material/request_quote: Quotation", ":material/receipt_long: Invoice", ":material/local_shipping: Waybill"],
        key="make_kind",
    )
    with make_quote:
        quote.render()
    with make_invoice:
        invoices.direct_invoice()
    with make_waybill:
        invoices.direct_waybill()
for tab, screen in zip(tabs[1:], [history.render, invoices.render, saved_items.render, admin.render]):
    with tab:
        if tab.open:
            screen()
