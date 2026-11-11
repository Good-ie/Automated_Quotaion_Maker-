"""Admin only: what staff are doing, staff accounts, the Word template and backups."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import streamlit as st

import quote_engine as qe
import store as db
from views import common

BACKUP_REMINDER_DAYS = 7


def render() -> None:
    store = common.get_store()
    users = store.list_users()
    names = {u["username"]: u["full_name"] for u in users}
    _overview(store, users)
    activity_tab, staff_tab, template_tab, backup_tab = st.tabs(
        ["Activity log", "Staff accounts", "Template", "Backup"]
    )
    with activity_tab:
        _activity(store, names)
    with staff_tab:
        _staff(store, users)
    with template_tab:
        _template(store)
    with backup_tab:
        _backup(store)


# ---------------------------------------------------------------------------
def _overview(store: db.Store, users: list[dict]) -> None:
    month_start = db.today().replace(day=1)
    quotes = pd.DataFrame(store.list_quotes())
    if quotes.empty:
        this_month = quotes
    else:
        this_month = quotes[(quotes["created_at"] >= pd.Timestamp(month_start)) & (quotes["status"] == "active")]

    last_backup = store.get_setting("last_backup")
    days = (db.now() - dt.datetime.fromisoformat(last_backup)).days if last_backup else None
    with st.container(horizontal=True):
        st.metric(f"Quotations in {month_start:%B}", len(this_month), border=True)
        st.metric(
            f"Value quoted in {month_start:%B}",
            common.naira(this_month["grand_total"].sum() if len(this_month) else 0),
            border=True,
        )
        st.metric("Active staff accounts", sum(1 for u in users if u["active"]), border=True)
        st.metric("Last backup", "Never" if days is None else f"{days} day(s) ago", border=True)
    if days is None or days >= BACKUP_REMINDER_DAYS:
        st.caption(":orange[:material/backup:] Time for a backup: open the **Backup** tab below.")

    week_ago = db.now() - dt.timedelta(days=7)
    newcomers = [u for u in users if u["created_at"] >= week_ago and u["username"] != common.user()["username"]]
    if newcomers:
        st.caption(
            ":material/person_add: **New accounts this week:** "
            + "; ".join(f"{u['full_name']} ({u['username']}, {u['created_at']:%d %b})" for u in newcomers)
            + ". Not someone you know? Switch the account off in **Staff accounts**."
        )

    unfinished = store.list_drafts()
    if unfinished:
        full_names = {u["username"]: u["full_name"] for u in users}
        st.caption(
            ":material/edit_note: **Unfinished quotations:** "
            + "; ".join(
                f"{full_names.get(d['username'], d['username'])}"
                f" ({d['client_name'] or 'no client yet'}, {d['saved_at']:%d %b %H:%M})"
                for d in unfinished
            )
        )

    if len(this_month):
        per_staff = (
            this_month.assign(staff=this_month["username"].map(lambda u: {x["username"]: x["full_name"] for x in users}.get(u, u)))
            .groupby("staff")
            .agg(quotations=("id", "count"), value=("grand_total", "sum"), last=("created_at", "max"))
            .reset_index()
            .sort_values("value", ascending=False)
        )
        st.dataframe(
            per_staff,
            hide_index=True,
            column_config={
                "staff": "Staff",
                "quotations": st.column_config.NumberColumn("Quotations this month"),
                "value": st.column_config.NumberColumn("Value (₦)", format="accounting"),
                "last": st.column_config.DatetimeColumn("Last quotation", format="DD MMM, HH:mm"),
            },
            alt="Quotations per staff member this month",
        )


# ---------------------------------------------------------------------------
def _activity(store: db.Store, names: dict) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        who = st.selectbox(
            "Staff", ["Everyone"] + sorted(names, key=lambda u: names[u]),
            format_func=lambda u: names.get(u, u), key="act_staff",
        )
        period = st.date_input(
            "Dates", value=(db.today() - dt.timedelta(days=6), db.today()), format="DD/MM/YYYY", key="act_dates"
        )
        query = st.text_input("Search", placeholder="Client, reference, action…", key="act_search")
    start, end = (period[0], period[-1]) if isinstance(period, (tuple, list)) and period else (db.today(), db.today())
    rows = store.activity(
        username=None if who == "Everyone" else who,
        since=dt.datetime.combine(start, dt.time()),
        until=dt.datetime.combine(end + dt.timedelta(days=1), dt.time()),
        limit=2000,
    )
    if not rows:
        st.caption("No activity in this period.")
        return
    df = pd.DataFrame(rows)
    df["staff"] = df["username"].map(lambda u: names.get(u, u))
    if query:
        mask = df[["action", "details", "staff"]].apply(
            lambda c: c.astype(str).str.contains(query, case=False, regex=False)
        ).any(axis=1)
        df = df[mask]
    st.caption(f"{len(df)} event(s)")
    st.dataframe(
        df,
        hide_index=True,
        column_order=["at", "staff", "action", "details"],
        column_config={
            "at": st.column_config.DatetimeColumn("When", format="DD MMM YYYY, HH:mm"),
            "staff": "Who",
            "action": "What",
            "details": st.column_config.TextColumn("Details", width="large"),
        },
        alt="Activity log",
    )


# ---------------------------------------------------------------------------
def _new_code() -> None:
    common.get_store().new_signup_code()
    common.log("Changed sign-up code", "the old code no longer works")


def _signup_settings(store: db.Store) -> None:
    with st.container(border=True):
        st.markdown("**Staff sign-up**")
        enabled = st.toggle(
            "Staff can create their own accounts", value=store.signup_enabled(), key="signup_enabled",
            help="They need the company sign-up code below. No approval is needed; every new account "
            "appears in the activity log and you can switch any account off.",
        )
        if enabled != store.signup_enabled():
            store.set_signup_enabled(enabled)
            common.log("Changed sign-up", "turned on" if enabled else "turned off")
            st.rerun()
        if enabled:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"Company sign-up code: **`{store.signup_code()}`**")
                st.button("New code", icon=":material/autorenew:", on_click=_new_code, key="new_signup_code",
                          help="Use this if the code got to someone outside the company. The old code stops working.")
            st.caption("Share the code with your staff (e.g. on the staff WhatsApp group) together with the app link.")


def _staff(store: db.Store, users: list[dict]) -> None:
    me = common.user()["username"]
    _signup_settings(store)
    df = pd.DataFrame(users)
    st.dataframe(
        df,
        hide_index=True,
        column_order=["full_name", "username", "initials", "role", "active", "last_login", "created_at"],
        column_config={
            "full_name": "Name",
            "username": "Username",
            "initials": "Initials",
            "role": "Role",
            "active": st.column_config.CheckboxColumn("Can sign in"),
            "last_login": st.column_config.DatetimeColumn("Last sign-in", format="DD MMM YYYY, HH:mm"),
            "created_at": st.column_config.DatetimeColumn("Added", format="DD MMM YYYY"),
        },
        alt="Staff accounts",
    )

    add_col, manage_col = st.columns(2, gap="large")
    with add_col, st.form("add_staff", clear_on_submit=True):
        st.markdown("**Add a staff member**")
        full_name = st.text_input("Full name")
        c1, c2 = st.columns([2, 1])
        username = c1.text_input("Username", help="Used to sign in, e.g. jade")
        initials = c2.text_input("Initials", max_chars=4, help="Used in their quote references")
        role = st.radio("Role", ["staff", "admin"], horizontal=True, format_func=str.title)
        password = st.text_input(
            "Temporary password", type="password",
            help="Give this to them. They must choose their own password at first sign-in.",
        )
        if st.form_submit_button("Add account", icon=":material/person_add:", type="primary"):
            if not full_name.strip() or not username.strip():
                st.error("Enter a full name and username.", icon=":material/error:")
            else:
                try:
                    store.create_user(username, full_name, initials, role, password, must_change_password=True)
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                else:
                    common.log("Added staff account", f"{username.strip().lower()} ({role})")
                    st.toast(f"Account created for {full_name.strip()}", icon=":material/check_circle:")
                    st.rerun()

    with manage_col, st.container(border=True):
        st.markdown("**Change an account**")
        target = st.selectbox(
            "Account", [u["username"] for u in users],
            format_func=lambda u: next(x["full_name"] for x in users if x["username"] == u) + f" ({u})",
            key="manage_user",
        )
        account = next(u for u in users if u["username"] == target)
        c1, c2 = st.columns(2)
        new_role = c1.radio(
            "Role", ["staff", "admin"], index=["staff", "admin"].index(account["role"]),
            horizontal=True, format_func=str.title, key=f"role_{target}",
        )
        new_active = c2.toggle("Can sign in", value=bool(account["active"]), key=f"active_{target}",
                               disabled=target == me, help="You cannot switch off your own account.")
        if st.button("Save account changes", icon=":material/save:", key="save_account"):
            try:
                store.update_user(target, role=new_role, active=new_active)
            except ValueError as exc:
                st.error(str(exc), icon=":material/error:")
            else:
                common.log("Changed staff account", f"{target}: role {new_role}, {'active' if new_active else 'switched off'}")
                st.toast("Account updated", icon=":material/check_circle:")
                st.rerun()

        st.markdown("**Reset password**")
        with st.form(f"reset_{target}", clear_on_submit=True):
            temp = st.text_input("New temporary password", type="password")
            if st.form_submit_button("Reset password", icon=":material/lock_reset:"):
                try:
                    store.set_password(target, temp, must_change=True)
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                else:
                    common.log("Reset password", target)
                    st.toast(f"Password reset. {target} must choose a new one at next sign-in.", icon=":material/check_circle:")

        st.markdown("**Delete account**")
        made = store.quote_count(target)
        if target == me:
            st.caption("You cannot delete your own account.")
        elif made:
            st.caption(f"{account['full_name']} has made {made} quotation(s), so the account is kept for the "
                       "records. Switch it off above instead.")
        else:
            st.caption("For a mistaken or duplicate account that never made a quotation. "
                       "The username and initials become free to use again.")
            sure = st.checkbox(f"Yes, delete {target} completely", key=f"confirm_delete_{target}")
            if st.button("Delete account", icon=":material/person_remove:", disabled=not sure,
                         key=f"delete_{target}"):
                try:
                    store.delete_user(target)
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                else:
                    common.log("Deleted account", f"{target} ({account['full_name']}, {account['initials']})")
                    st.toast(f"Account {target} deleted", icon=":material/check_circle:")
                    st.rerun()


# ---------------------------------------------------------------------------
def _template(store: db.Store) -> None:
    current = store.latest_template()
    if current:
        st.markdown(
            f":material/description: Current template: **{current['filename']}** "
            f"({current['size'] / 1_048_576:.1f} MB), uploaded {current['uploaded_at']:%d %b %Y} "
            f"by {current['username']}."
        )
        data = common.template_bytes(current["id"])
        st.download_button(
            "Download current template", data=data, file_name=current["filename"],
            icon=":material/download:", on_click="ignore", key="download_template",
        )
    else:
        st.warning("No template yet. Upload the company template.docx so staff can generate quotations.",
                   icon=":material/warning:")

    st.caption(
        "Uploading a new template only affects new quotations. Old quotations keep the template they "
        "were made with, so they download exactly as they were sent."
    )
    upload = st.file_uploader("Upload a new template (.docx)", type=["docx"], key="template_upload")
    if upload is not None:
        data = upload.getvalue()
        problems = qe.check_template(data)
        if problems:
            for p in problems:
                st.error(p, icon=":material/error:")
        elif st.button("Use this template", type="primary", icon=":material/check:", key="save_template"):
            store.add_template(data, upload.name, common.user()["username"])
            common.log("Uploaded template", f"{upload.name} ({len(data) / 1_048_576:.1f} MB)")
            st.toast("Template saved. New quotations will use it.", icon=":material/check_circle:")
            st.rerun()

    older = store.list_templates()[1:]
    if older:
        with st.expander(f"Earlier templates ({len(older)})"):
            st.dataframe(
                pd.DataFrame(older), hide_index=True,
                column_order=["uploaded_at", "filename", "username"],
                column_config={
                    "uploaded_at": st.column_config.DatetimeColumn("Uploaded", format="DD MMM YYYY, HH:mm"),
                    "filename": "File", "username": "By",
                },
            )


# ---------------------------------------------------------------------------
def _mark_backup() -> None:
    store = common.get_store()
    store.set_setting("last_backup", db.now().isoformat())
    common.log("Downloaded backup")


def _backup(store: db.Store) -> None:
    st.markdown(
        "The free database plan does not make automatic backups. Download a backup about once a week "
        "and keep it somewhere safe (e.g. Google Drive). It holds every quotation, the activity log, "
        "saved items and the staff list (without passwords)."
    )
    if st.button("Prepare backup file", icon=":material/backup:", key="prepare_backup"):
        st.session_state.backup = json.dumps(store.export_backup(), indent=1, default=str, ensure_ascii=False)
    if "backup" in st.session_state:
        st.download_button(
            "Download backup",
            data=st.session_state.backup,
            file_name=f"rehoboth_quotes_backup_{db.today():%Y-%m-%d}.json",
            mime="application/json",
            icon=":material/download:",
            type="primary",
            on_click=_mark_backup,
            key="download_backup",
        )

    st.divider()
    st.markdown("**Bring in data from the old version**")
    st.caption("Upload product_catalog.json (saved items) or quotes_history.json from the app folder on your PC.")
    old = st.file_uploader("Old data file", type=["json"], key="old_json")
    if old is not None and st.button("Import", icon=":material/upload:", key="import_old"):
        try:
            entries = json.loads(old.getvalue().decode("utf-8"))
            if not isinstance(entries, list):
                raise ValueError("unexpected format")
            if entries and "ref" in entries[0]:
                count = store.import_old_history(entries)
                what = "quotation record(s)"
            else:
                count = store.import_old_catalog(entries)
                what = "saved item(s)"
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            st.error(f"Could not read that file: {exc}", icon=":material/error:")
        else:
            common.log("Imported data", f"{count} {what} from {old.name}")
            st.success(f"Imported {count} {what}.", icon=":material/check_circle:")
