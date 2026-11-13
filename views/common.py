"""Shared helpers for the screens: database connection, signed-in user, formatting."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

import pdf_export
import store as db


def _secret_section(name: str) -> dict | None:
    try:
        return dict(st.secrets[name])
    except (KeyError, FileNotFoundError):  # no secrets file (FileNotFoundError subclass) or no section
        return None


@st.cache_resource(show_spinner="Connecting to the database…", max_entries=1)
def _connect(code_version: int) -> db.Store:
    store = db.Store(db.database_url(_secret_section("database")))
    store.import_local_files()
    return store


def get_store() -> db.Store:
    """The shared database connection. When store.py is updated (a new version pushed to
    GitHub), the old connection is replaced so it never runs outdated code."""
    root = Path(db.__file__).parent
    version = max(int(f.stat().st_mtime) for f in [*root.glob("*.py"), *root.glob("views/*.py")])
    return _connect(version)


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


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@st.cache_data(max_entries=16, show_spinner=False)
def to_pdf(word: bytes) -> bytes | None:
    """PDF copy of a Word document (None where LibreOffice is not installed)."""
    return pdf_export.docx_to_pdf(word)


def download_buttons(word: bytes, pdf: bytes | None, file_name: str, key: str) -> None:
    """'Download PDF' and 'Download Word' side by side."""
    with st.container(horizontal=True, key=f"{key}_row"):
        if pdf:
            st.download_button("Download PDF", data=pdf, file_name=file_name.rsplit(".", 1)[0] + ".pdf",
                               mime="application/pdf", icon=":material/picture_as_pdf:", type="primary",
                               on_click="ignore", key=f"{key}_pdf")
        st.download_button("Download Word", data=word, file_name=file_name, mime=DOCX_MIME,
                           icon=":material/description:", on_click="ignore", key=key)
    if pdf is None:
        st.caption(":material/info: PDF is not available on this computer (it needs LibreOffice). "
                   "The online app makes PDFs.")


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
