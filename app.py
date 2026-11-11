"""Rehoboth Quotation Maker: entry point.

Run locally:   streamlit run app.py
Online:        deployed on Streamlit Community Cloud, data kept in Supabase (see README).
"""
import sys
from pathlib import Path

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from views import admin, auth, common, history, quote, saved_items

st.set_page_config(
    page_title="Rehoboth Quotation Maker",
    page_icon=":material/request_quote:",
    layout="wide",
)
st.html(Path(__file__).with_name("assets") / "animations.css")

try:
    store = common.get_store()
    if not st.session_state.get("has_accounts"):
        st.session_state.has_accounts = store.user_count() > 0
except SQLAlchemyError as exc:
    common.get_store.clear()  # try again from scratch on the next refresh
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
    "Rehoboth Systems & Project Limited · Paste the item list from WhatsApp, review it, "
    "then download a client-ready Word quotation."
)

names = ["New quotation", "All quotations" if is_admin else "My quotations", "Saved items"]
if is_admin:
    names.append("Admin")
tabs = st.tabs(names, key="main_tab", on_change="rerun")

# The quotation being prepared always stays live, so switching tabs never loses work.
# The other tabs only load from the database when they are opened.
with tabs[0]:
    quote.render()
with tabs[1]:
    if tabs[1].open:
        history.render()
with tabs[2]:
    if tabs[2].open:
        saved_items.render()
if is_admin:
    with tabs[3]:
        if tabs[3].open:
            admin.render()
