"""Shared helpers for the screens: database connection, signed-in user, formatting."""
from __future__ import annotations

import streamlit as st

import store as db


def _secret_section(name: str) -> dict | None:
    try:
        return dict(st.secrets[name])
    except (KeyError, FileNotFoundError):  # no secrets file (FileNotFoundError subclass) or no section
        return None


@st.cache_resource(show_spinner="Connecting to the database…")
def get_store() -> db.Store:
    store = db.Store(db.database_url(_secret_section("database")))
    store.import_local_files()
    return store


def db_error_hint(exc: Exception) -> str:
    """Plain-English reason for a failed database connection (never shows secrets)."""
    text = str(exc).lower()
    if "password authentication failed" in text:
        return "The database password in the app secrets is wrong (password = \"...\")."
    if "tenant or user not found" in text or "tenant/user" in text:
        return ("The user or host does not match the project. user must be postgres.<project id> "
                "and host must be the Session pooler host, both copied from Supabase › Connect.")
    if "could not translate host name" in text or "name or service not known" in text:
        return "The host name in the app secrets is wrong. Copy it from Supabase › Connect › Session pooler."
    if "network is unreachable" in text or "cannot assign requested address" in text:
        return ("This looks like the 'Direct connection' host (db.<id>.supabase.co), which Streamlit Cloud "
                "cannot reach. Use the Session pooler host instead.")
    if "timeout" in text or "timed out" in text:
        return "The database did not answer. Check the host and port (5432), and that the project is not paused."
    if "max clients" in text or "too many" in text:
        return "Too many connections at once. Wait a minute and refresh."
    return "See Manage app › Logs for the technical error."


def setup_key() -> str | None:
    """Optional code needed to create the very first (admin) account online."""
    app = _secret_section("app") or {}
    return app.get("setup_key") or None


@st.cache_data(max_entries=4, show_spinner=False)
def template_bytes(template_id: int) -> bytes | None:
    return get_store().template_bytes(template_id)


def user() -> dict:
    return st.session_state["user"]


def is_admin() -> bool:
    return user()["role"] == "admin"


def log(action: str, details: str = "") -> None:
    get_store().log(user()["username"], action, details)


def naira(amount: float) -> str:
    return f"₦{amount:,.2f}"


def hero(caption: str) -> None:
    with st.container(key="hero"):
        st.title("Rehoboth Quotation Maker", icon=":material/bolt:")
        st.caption(caption)


def centered():
    """A narrow middle column for forms such as sign-in."""
    return st.columns([1, 1.3, 1])[1]
