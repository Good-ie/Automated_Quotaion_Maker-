"""Saved items: products and prices the app has learned from past quotations."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from views import common

_COLUMNS = ["id", "description", "model", "unit_cost", "availability", "last_quoted", "times_quoted"]


def render() -> None:
    store = common.get_store()
    admin = common.is_admin()
    st.caption(
        "Items the app has learned from your quotations. When a pasted item matches one of these, "
        "the app fills in the model number, full description and missing price, and warns if the "
        "price changed by more than 20%."
        + (" Edit or delete entries here, then save." if admin else " Only the admin can edit this list.")
    )
    entries = store.catalog()
    if not entries:
        st.caption("Nothing saved yet. Items are saved automatically each time a quotation is generated.")
        return

    df = pd.DataFrame(entries, columns=_COLUMNS)
    df["last_quoted"] = pd.to_datetime(df["last_quoted"], errors="coerce")
    query = st.text_input("Search", placeholder="Description or model", key="catalog_search")
    if query:
        mask = df[["description", "model"]].apply(
            lambda c: c.astype(str).str.contains(query, case=False, regex=False)
        ).any(axis=1)
        df = df[mask]

    config = {
        "id": None,
        "description": st.column_config.TextColumn("Description", width="large", required=True),
        "model": "Model no.",
        "unit_cost": st.column_config.NumberColumn("Last cost (₦)", format="accounting", min_value=0.0),
        "availability": "Availability",
        "last_quoted": st.column_config.DateColumn("Last quoted", format="DD MMM YYYY", disabled=True),
        "times_quoted": st.column_config.NumberColumn("Times quoted", disabled=True),
    }
    if not admin:
        st.dataframe(df, hide_index=True, column_config=config, alt="Saved items")
        return

    edited = st.data_editor(
        df,
        key=f"catalog_editor_{len(entries)}_{query}",
        num_rows="delete",
        hide_index=True,
        column_config=config,
    )
    if st.button("Save changes", icon=":material/save:", key="save_catalog"):
        out = edited.copy()
        out["last_quoted"] = out["last_quoted"].dt.date.astype(str).replace({"NaT": ""})
        records = out.fillna("").to_dict("records")
        # rows hidden by the search are kept as they were
        shown = set(df["id"])
        records += [e for e in entries if e["id"] not in shown]
        removed = len(entries) - len(records)
        store.replace_catalog(records)
        common.log("Edited saved items", f"{len(records)} item(s) kept, {removed} deleted")
        st.toast("Saved items updated", icon=":material/check_circle:")
        st.rerun()
