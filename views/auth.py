"""Sign-in, first admin set-up, password changes and sign-out."""
from __future__ import annotations

import hmac
import time

import streamlit as st

import store as db
from views import common

PUBLIC_FIELDS = ("username", "full_name", "initials", "role", "must_change_password")

IDLE_SECONDS = 10 * 60   # sign out after 10 minutes without any activity
COOKIE = "rq_session"    # browser cookie holding the session key, so a page refresh keeps you in


# ---------------------------------------------------------------------------
# Session key in the browser
# ---------------------------------------------------------------------------
def read_cookie() -> str | None:
    """The session key the browser sent when this page was opened (or refreshed)."""
    value = st.context.cookies.get(COOKIE)
    return value if isinstance(value, str) and value else None


def _write_cookie(token: str | None) -> None:
    """Store (or delete, when token is None) the key in the browser.

    The cookie itself only lives for IDLE_SECONDS and is renewed on every action, so the
    browser deletes it by itself after 10 idle minutes.
    """
    if token:
        cookie = f"{COOKIE}={token}; Max-Age={IDLE_SECONDS}; Path=/; SameSite=Strict"
    else:
        cookie = f"{COOKIE}=; Max-Age=0; Path=/; SameSite=Strict"
    script = (
        "<script>(function(){var c='" + cookie + "';"
        "if(location.protocol==='https:'){c+='; Secure';}document.cookie=c;})();</script>"
        f"<!-- {time.time()} -->"  # changes every run so the browser re-applies it
    )
    with st.container(key="rq_cookie"):
        st.html(script, unsafe_allow_javascript=True)


def _sign_in(user: dict, token: str) -> None:
    st.session_state.user = {k: user[k] for k in PUBLIC_FIELDS}
    st.session_state.token = token
    st.session_state.last_activity = time.time()


def _end(reason: str | None = None) -> None:
    """Forget the session here, in the database and (next run) in the browser."""
    token = st.session_state.get("token")
    if token:
        common.get_store().end_session(token)
    st.session_state.clear()
    st.session_state.cookie_checked = True  # don't re-read the old cookie in this tab
    st.session_state.clear_cookie = True
    if reason:
        st.session_state.signed_out_reason = reason


def sign_out() -> None:
    username = st.session_state.get("user", {}).get("username")
    if username:
        common.get_store().log(username, "Signed out")
    _end()


def resume_from_cookie(store: db.Store) -> None:
    """After a refresh: sign back in with the browser's session key, if still valid."""
    if st.session_state.get("cookie_checked"):
        return
    st.session_state.cookie_checked = True
    token = read_cookie()
    if not token:
        return
    user = store.touch_session(token, IDLE_SECONDS)
    if user:
        _sign_in(user, token)
    else:
        st.session_state.clear_cookie = True


def forget_cookie_if_needed() -> None:
    if st.session_state.pop("clear_cookie", False):
        _write_cookie(None)


def keep_alive(store: db.Store) -> bool:
    """Run on every action while signed in: enforce the 10-minute idle limit, re-check
    the account (switched off? role changed?) and renew the session key."""
    if time.time() - st.session_state.get("last_activity", time.time()) > IDLE_SECONDS:
        store.log(st.session_state.user["username"], "Signed out automatically", "10 minutes without activity")
        _end("You were signed out after 10 minutes without activity. Your unfinished quotation was kept.")
        return False
    user = store.touch_session(st.session_state.token, IDLE_SECONDS)
    if user is None:
        _end("Your session ended (your account was changed by the admin). Please sign in again.")
        return False
    _sign_in(user, st.session_state.token)
    _write_cookie(st.session_state.token)
    return True


@st.fragment(run_every=30)
def idle_watch() -> None:
    """Signs an untouched screen out at the 10-minute mark, without waiting for a click."""
    if time.time() - st.session_state.get("last_activity", time.time()) > IDLE_SECONDS:
        st.rerun(scope="app")


def _start_session(store: db.Store, user: dict) -> None:
    _sign_in(user, store.create_session(user["username"], IDLE_SECONDS))


def setup_screen(store: db.Store) -> None:
    common.hero("First-time set-up")
    with common.centered(), st.container(border=True):
        st.subheader("Create the admin account", icon=":material/admin_panel_settings:")
        st.caption(
            "No accounts exist yet. This first account is the admin: it can add staff, "
            "see every quotation and the activity log."
        )
        needed_key = common.setup_key()
        with st.form("setup"):
            full_name = st.text_input("Full name")
            c1, c2 = st.columns([2, 1])
            username = c1.text_input("Username", help="Used to sign in, e.g. mjohn")
            initials = c2.text_input("Initials", max_chars=4, help="Goes into quote references, e.g. RSL/MJO/…")
            password = st.text_input("Password", type="password")
            password2 = st.text_input("Repeat password", type="password")
            key = st.text_input("Set-up code", type="password", help="From the app secrets.") if needed_key else ""
            submitted = st.form_submit_button("Create admin account", type="primary", icon=":material/check:")
        if submitted:
            if needed_key and not hmac.compare_digest(key, needed_key):
                st.error("The set-up code is wrong.", icon=":material/error:")
            elif not full_name.strip() or not username.strip():
                st.error("Enter your full name and a username.", icon=":material/error:")
            elif password != password2:
                st.error("The two passwords are different.", icon=":material/error:")
            else:
                try:
                    store.create_user(username, full_name, initials, "admin", password, must_change_password=False)
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                else:
                    store.log(username.strip().lower(), "Created the admin account")
                    user, _ = store.authenticate(username, password)
                    _start_session(store, user)
                    st.rerun()


def login_screen(store: db.Store) -> None:
    common.hero("Rehoboth Systems & Project Limited · Staff sign-in")
    signup_open = store.signup_enabled()
    with common.centered(), st.container(border=True):
        if signup_open:
            sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Create an account"])
        else:
            sign_in_tab, sign_up_tab = st.container(), None
        with sign_in_tab:
            _sign_in_form(store)
        if sign_up_tab is not None:
            with sign_up_tab:
                _sign_up_form(store)


def _sign_in_form(store: db.Store) -> None:
    st.subheader("Sign in", icon=":material/lock:")
    with st.form("login"):
        username = st.text_input("Username", autocomplete="username", key="login_username")
        password = st.text_input("Password", type="password", autocomplete="current-password", key="login_password")
        submitted = st.form_submit_button("Sign in", type="primary", icon=":material/login:", width="stretch")
    if submitted:
        if not username.strip() or not password:
            st.error("Enter your username and password.", icon=":material/error:")
            return
        user, reason = store.authenticate(username, password)
        if user is None:
            store.log(username.strip().lower()[:40], "Failed sign-in", reason)
            st.error(reason, icon=":material/error:")
            return
        store.log(user["username"], "Signed in")
        _start_session(store, user)
        st.rerun()
    st.caption("Forgot your password? Ask the admin to reset it.")


def _sign_up_form(store: db.Store) -> None:
    """Staff create their own account with the company sign-up code. No approval step."""
    st.subheader("Create your staff account", icon=":material/person_add:")
    st.caption("You need the company sign-up code. Ask a colleague or the admin for it.")
    with st.form("signup"):
        full_name = st.text_input("Full name", key="signup_full_name")
        c1, c2 = st.columns([2, 1])
        username = c1.text_input("Username", help="You will use this to sign in, e.g. jade", key="signup_username")
        initials = c2.text_input("Initials", max_chars=4, help="Goes into your quote references, e.g. RSL/JA/…",
                                 key="signup_initials")
        password = st.text_input("Password", type="password", help=f"At least {db.MIN_PASSWORD} characters.",
                                 key="signup_password")
        password2 = st.text_input("Repeat password", type="password", key="signup_password2")
        code = st.text_input("Company sign-up code", type="password", key="signup_code")
        submitted = st.form_submit_button("Create my account", type="primary", icon=":material/check:", width="stretch")
    if not submitted:
        return
    if store.signup_paused():
        st.error(f"Too many wrong sign-up codes. Sign-up is paused; try again in {db.SIGNUP_PAUSE_MINUTES} minutes.",
                 icon=":material/error:")
        return
    if not store.check_signup_code(code):
        store.log(username.strip().lower()[:40] or "?", "Failed sign-up", "wrong sign-up code")
        st.error("The sign-up code is wrong.", icon=":material/error:")
        return
    if not full_name.strip() or not username.strip():
        st.error("Enter your full name and a username.", icon=":material/error:")
        return
    if password != password2:
        st.error("The two passwords are different.", icon=":material/error:")
        return
    try:
        store.create_user(username, full_name, initials, "staff", password, must_change_password=False)
    except ValueError as exc:
        st.error(str(exc), icon=":material/error:")
        return
    new_user = username.strip().lower()
    try:
        user, reason = store.authenticate(new_user, password)
        if user is None:
            raise RuntimeError(reason)
        _start_session(store, user)
    except Exception:  # anything going wrong here must not leave a half-made account behind
        store.delete_user(new_user)
        st.error("Your account could not be finished, so nothing was saved. Please try again.",
                 icon=":material/error:")
        return
    store.log(new_user, "Created own account", f"{full_name.strip()} ({initials.strip().upper()})")
    st.rerun()


def _password_form(store: db.Store, form_key: str, need_current: bool) -> None:
    username = common.user()["username"]
    with st.form(form_key, clear_on_submit=True):
        current = st.text_input("Current password", type="password") if need_current else None
        new = st.text_input("New password", type="password", help=f"At least {db.MIN_PASSWORD} characters.")
        new2 = st.text_input("Repeat new password", type="password")
        submitted = st.form_submit_button("Change password", icon=":material/key:")
    if submitted:
        if need_current:
            ok, _ = store.authenticate(username, current or "")
            if not ok:
                st.error("Your current password is wrong.", icon=":material/error:")
                return
        if new != new2:
            st.error("The two new passwords are different.", icon=":material/error:")
            return
        try:
            store.set_password(username, new)
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
            return
        store.log(username, "Changed own password")
        st.session_state.user["must_change_password"] = False
        st.toast("Password changed", icon=":material/check_circle:")
        if not need_current:
            st.rerun()


def forced_password_change(store: db.Store) -> None:
    common.hero(f"Welcome, {common.user()['full_name']}")
    with common.centered(), st.container(border=True):
        st.subheader("Choose your own password", icon=":material/key:")
        st.caption("You signed in with a temporary password from the admin. Choose a new one to continue.")
        _password_form(store, "first_password", need_current=False)
        st.button("Sign out", on_click=sign_out, type="tertiary", icon=":material/logout:")


def sidebar_account(store: db.Store) -> None:
    user = common.user()
    with st.sidebar:
        st.markdown(
            f":material/account_circle: **{user['full_name']}**  \n"
            f"{'Admin' if user['role'] == 'admin' else 'Staff'} · {user['initials']}"
        )
        with st.container(horizontal=True):
            with st.popover("Account", icon=":material/settings:", type="tertiary"):
                st.markdown("**Change my password**")
                _password_form(store, "own_password", need_current=True)
            st.button("Sign out", icon=":material/logout:", on_click=sign_out, key="sign_out", type="tertiary")
        st.divider()
