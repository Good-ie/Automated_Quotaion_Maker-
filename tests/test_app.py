"""End-to-end check of the app with a throwaway database.

Run with:  python tests/test_app.py
Needs template.docx next to app.py (it is copied into the test database).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="rq_test_")
os.environ["REHOBOTH_DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"

from streamlit.testing.v1 import AppTest  # noqa: E402

import store as db  # noqa: E402

APP = str(ROOT / "app.py")
failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(("ok   " if ok else "FAIL ") + message)
    if not ok:
        failures.append(message)


def main_tabs(app):
    """The main tab names (the inner Quotation / Invoice / Waybill tabs start with an icon)."""
    return [t.label for t in app.tabs if not t.label.startswith(":material")]


def button(at, label):
    return next(b for b in at.button if b.label == label)


def text(at, label):
    return next(t for t in at.text_input if t.label == label)


def errors(at):
    return [e.value for e in at.error]


def new_app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def sign_in(at, username, password):
    text(at, "Username").input(username)
    text(at, "Password").input(password)
    button(at, "Sign in").click().run()


# 1. first run asks for the admin account ----------------------------------------
at = new_app()
check(any("Create the admin account" in s.value for s in at.subheader), "first run shows admin set-up")
text(at, "Full name").input("Mary Okon")
text(at, "Username").input("Mary")
text(at, "Initials").input("mjo")
text(at, "Password").input("secret123")
text(at, "Repeat password").input("secret123")
button(at, "Create admin account").click().run()
check(not at.exception and "user" in at.session_state, f"admin created and signed in {errors(at)}")
check(main_tabs(at)[:5] == ["New document", "All quotations", "All invoices", "Saved items", "Admin"],
      "admin sees 5 tabs")

store = db.Store(os.environ["REHOBOTH_DATABASE_URL"])
check(store.latest_template() is not None, "template.docx was stored in the database")
base = len(store.list_quotes())  # old quotes_history.json records are imported too
print(f"     ({base} old record(s) imported, {len(store.catalog())} saved item(s))")

# 2. generate a quotation ---------------------------------------------------------
import pdf_export  # noqa: E402

pdf_export.docx_to_pdf = lambda data, timeout=180: b"%PDF-1.4 test copy"
check(at.session_state["quote_ref"].startswith("RSL/MJO/"), f"reference uses initials: {at.session_state['quote_ref']}")
first_ref = at.session_state["quote_ref"]
text(at, "Client name").input("Pladis Foods Nigeria")
next(t for t in at.text_area if t.label == "Paste items").input(
    "Item Code: ITAC00195 | Description: iPhone 11 Screen | Quantity: 30 Units | Unit Price: 25,000"
)
at.run()
button(at, "Generate quotation").click().run()
saved = store.list_quotes()
saved = [q for q in saved if q["status"] != "legacy"]
check(len(saved) == 1 and saved[0]["reference"] == first_ref and saved[0]["grand_total"] == 750000,
      f"quotation saved: {[(q['reference'], q['grand_total']) for q in saved]}")
labels = [b.label for b in at.get("download_button")]
check("Download Word" in labels and "Download PDF" in labels, f"PDF and Word download buttons shown: {labels}")

# 3. same reference for a different client is refused (the old overwrite bug) ----------
text(at, "Client name").input("Plaudis Food")
at.run()
button(at, "Generate quotation").click().run()
# made in this session by the same person: allowed as a revision (fixing a client-name typo)
check(len(store.list_quotes()) == base + 2 and store.latest_for_reference(first_ref)["revision"] == 2,
      "re-generating in the same session saves revision 2, nothing overwritten")

# a fresh session typing an old reference for another client is refused
at2 = new_app()
sign_in(at2, "mary", "secret123")
at2.session_state["quote_ref"] = first_ref
text(at2, "Client name").input("Another Client Ltd")
next(t for t in at2.text_area if t.label == "Paste items").input("Battery 200AH 320k x 2")
at2.run()
button(at2, "Generate quotation").click().run()
check(any("already used" in e for e in errors(at2)), f"reference clash refused: {errors(at2)}")
check(len(store.list_quotes()) == base + 2, "clash did not save anything")
button(at2, "Use next number").click().run()
want = f"{int(first_ref.rsplit('/', 1)[1]) + 1:03d}"
check(at2.session_state["quote_ref"].endswith("/" + want), f"next number is {want}: {at2.session_state['quote_ref']}")
button(at2, "Generate quotation").click().run()
check(len(store.list_quotes()) == base + 3, "second client saved under the next number")

# 4. staff account, forced password change, staff only see their own ------------------
store.create_user("jade", "Jade Ade", "JA", "staff", "temp1234", must_change_password=True)
at3 = new_app()
sign_in(at3, "jade", "wrong")
check(any("Wrong username or password" in e for e in errors(at3)), "wrong password refused")
sign_in(at3, "jade", "temp1234")
check(any("Choose your own password" in s.value for s in at3.subheader), "temporary password must be changed")
text(at3, "New password").input("jadepass1")
text(at3, "Repeat new password").input("jadepass1")
button(at3, "Change password").click().run()
check(main_tabs(at3) == ["New document", "My quotations", "My invoices", "Saved items"]
      and "Admin" not in [t.label for t in at3.tabs], "staff do not see the Admin tab")
check(at3.session_state["quote_ref"].startswith("RSL/JA/"), "staff reference uses their initials")

# 5. lockout after repeated wrong passwords -----------------------------------------
for _ in range(db.MAX_ATTEMPTS):
    user, reason = store.authenticate("jade", "nope")
user, reason = store.authenticate("jade", "jadepass1")
check(user is None and "Try again" in reason, f"account locks after {db.MAX_ATTEMPTS} wrong attempts")

# 6. activity log and rebuild ------------------------------------------------------
actions = [a["action"] for a in store.activity()]
for expected in ("Created the admin account", "Generated quotation", "Failed sign-in", "Signed in", "Changed own password"):
    check(expected in actions, f"activity log has '{expected}'")
import quote_engine as qe  # noqa: E402

q = store.get_quote(store.list_quotes()[0]["id"])  # newest
doc, _ = qe.rebuild_quotation(store.template_bytes(q["template_id"]), q["data"])
check(len(doc) > 100_000, "old quotation rebuilds into a Word document")

# 7. admin and history tabs render without errors ----------------------------------
at.session_state["main_tab"] = "Admin"
at.run()
check(not at.exception, f"admin tab renders {at.exception}")
at.session_state["main_tab"] = "All quotations"
at.run()
check(not at.exception, f"history tab renders {at.exception}")

# 8. session key: refresh keeps you in, 10 idle minutes or sign-out ends it everywhere ----
import time  # noqa: E402

from views import auth  # noqa: E402

browser = {"cookie": None}  # what the (simulated) browser sends when a page is opened
auth.read_cookie = lambda: browser["cookie"]


def signed_in(app):
    return "user" in app.session_state and any(t.label == "New document" for t in app.tabs)


token = at.session_state["token"]
check(store.touch_session(token, auth.IDLE_SECONDS) is not None, "signing in created a session key")
browser["cookie"] = token
refreshed = new_app()
check(signed_in(refreshed), "page refresh within 10 minutes keeps you signed in (no password)")

refreshed.session_state["last_activity"] = time.time() - auth.IDLE_SECONDS - 1
refreshed.run()
check(not signed_in(refreshed) and any("10 minutes" in w.value for w in refreshed.warning),
      "after 10 idle minutes the screen signs out")
check(store.touch_session(token, auth.IDLE_SECONDS) is None, "...and the key is deleted from the database")
check(not signed_in(new_app()), "...and refreshing with the old key no longer signs in")

# the database itself refuses a key whose 10 minutes have run out
key = store.create_session("mary", 1)
time.sleep(1.2)
check(store.touch_session(key, auth.IDLE_SECONDS) is None, "an expired key is refused and removed")

# sign out deletes the key
browser["cookie"] = None
at4 = new_app()
sign_in(at4, "mary", "secret123")
key4 = at4.session_state["token"]
button(at4, "Sign out").click().run()
check(not signed_in(at4) and store.touch_session(key4, auth.IDLE_SECONDS) is None, "sign out deletes the key")

# switching an account off ends its sessions straight away
at5 = new_app()
store.update_user("jade", active=True)
with store.engine.begin() as conn:  # lift the lockout from test 5
    conn.execute(db.users.update().where(db.users.c.username == "jade").values(locked_until=None))
sign_in(at5, "jade", "jadepass1")
key5 = at5.session_state["token"]
store.update_user("jade", active=False)
at5.run()
check(not signed_in(at5) and store.touch_session(key5, auth.IDLE_SECONDS) is None,
      "switching an account off signs it out at once")
store.update_user("jade", active=True)

# 9. drafts: unfinished work survives being signed out --------------------------------
at6 = new_app()
sign_in(at6, "mary", "secret123")
button(at6, "Start a new quotation").click().run()
text(at6, "Client name").input("Draft Client Ltd")
next(t for t in at6.text_area if t.label == "Paste items").input("Battery 200AH 320k x 2\nInverter 5kva 850,000")
at6.run()
draft = store.get_draft("mary")
check(draft is not None and draft["client_name"] == "Draft Client Ltd" and len(draft["data"]["options"][0]["rows"]) == 2,
      "work in progress is saved as a draft")
at6.session_state["last_activity"] = time.time() - auth.IDLE_SECONDS - 1
at6.run()
check(not signed_in(at6), "idle sign-out")
sign_in(at6, "mary", "secret123")
check(any(b.label == "Continue it" for b in at6.button), "after signing in, the app offers the unfinished quotation")
button(at6, "Continue it").click().run()
check(at6.session_state["client_name"] == "Draft Client Ltd"
      and "Battery 200AH" in at6.session_state["raw_1"], "draft restored: client and pasted text")
button(at6, "Use next number").click().run()
button(at6, "Generate quotation").click().run()
check(store.get_draft("mary") is None, "generating the quotation clears the draft")
check(store.list_quotes()[0]["client_name"] == "Draft Client Ltd" and store.list_quotes()[0]["items_count"] == 2,
      "restored draft generated with both items")

# 10. staff create their own accounts with the company code ----------------------------
browser["cookie"] = None


def sign_up(app, full_name, username, initials, password, code):
    form = {t.key: t for t in app.text_input}
    form["signup_full_name"].input(full_name)
    form["signup_username"].input(username)
    form["signup_initials"].input(initials)
    form["signup_password"].input(password)
    form["signup_password2"].input(password)
    form["signup_code"].input(code)
    button(app, "Create my account").click().run()


code = store.signup_code()
check(code.startswith("RSL-") and len(code) == 13, f"a sign-up code was made automatically: {code[:4]}…")
at7 = new_app()
check("Create an account" in [t.label for t in at7.tabs], "sign-in page offers 'Create an account'")
sign_up(at7, "Tunde Bello", "tunde", "TB", "tundepass1", "RSL-WRONG-CODE")
check(any("sign-up code is wrong" in e for e in errors(at7)) and store.get_user("tunde") is None,
      "a wrong sign-up code is refused")
sign_up(at7, "Tunde Bello", "tunde", "TB", "tundepass1", code.lower())
tunde = store.get_user("tunde")
check(tunde is not None and tunde["role"] == "staff" and signed_in(at7)
      and "Admin" not in [t.label for t in at7.tabs], "the right code creates a staff account and signs in")
check(at7.session_state["quote_ref"].startswith("RSL/TB/"), "new staff member's references use their initials")
acts = [(a["username"], a["action"]) for a in store.activity()]
check(("tunde", "Created own account") in acts and ("tunde", "Failed sign-up") in acts,
      "the admin's activity log shows the sign-up and the wrong code")

old_code = code
store.new_signup_code()
check(not store.check_signup_code(old_code), "after 'New code' the old code stops working")

store.set_signup_enabled(False)
check("Create an account" not in [t.label for t in new_app().tabs], "admin can switch sign-up off")
store.set_signup_enabled(True)

for _ in range(db.SIGNUP_MAX_WRONG):
    store.log("guesser", "Failed sign-up", "wrong sign-up code")
at8 = new_app()
sign_up(at8, "Guess Who", "guess", "GW", "guesspass1", store.signup_code())
check(store.get_user("guess") is None and any("paused" in e for e in errors(at8)),
      f"after {db.SIGNUP_MAX_WRONG} wrong codes sign-up pauses, even for the right code")

admin_app = new_app()
sign_in(admin_app, "mary", "secret123")
admin_app.session_state["main_tab"] = "Admin"
admin_app.run()
check(not admin_app.exception and any("sign-up code" in m.value for m in admin_app.markdown),
      f"admin sees the company sign-up code {admin_app.exception}")

# 11. a mistaken account can be removed and its username used again ---------------------
store.log("guesser", "x", "")  # keep the pause from test 10 from mattering below
with store.engine.begin() as conn:
    conn.execute(db.activity.delete().where(db.activity.c.action == "Failed sign-up"))
at9 = new_app()
sign_up(at9, "Tunde Bello", "tunde", "TX", "another1", store.signup_code())
check(any("already taken" in e for e in errors(at9)), f"taken username gets a clear message: {errors(at9)}")
sign_up(at9, "Tola Bello", "tola", "TB", "another1", store.signup_code())
check(any("initials TB are already used" in e for e in errors(at9)), "taken initials get a clear message")
store.delete_user("tunde")
check(store.get_user("tunde") is None, "admin can delete an account that made no quotations")
sign_up(at9, "Tunde Bello", "tunde", "TB", "freshpass1", store.signup_code())
check(store.get_user("tunde") is not None and signed_in(at9), "the freed username can sign up again")
try:
    store.delete_user("mary")
    kept = False
except ValueError:
    kept = True
check(kept and store.get_user("mary") is not None, "an account with quotations cannot be deleted")
boss = new_app()
sign_in(boss, "mary", "secret123")
boss.session_state["main_tab"] = "Admin"
boss.run()
boss.session_state["manage_user"] = "tunde"
boss.session_state["main_tab"] = "Admin"  # the test harness does not remember the open tab
boss.run()
check(not boss.exception and any(b.label == "Delete account" for b in boss.button), "admin sees the Delete account button")

# 12. invoices and waybills ------------------------------------------------------------
import io  # noqa: E402

import docx  # noqa: E402

import invoice_engine as ie  # noqa: E402
from views import invoices as invoice_views  # noqa: E402


def invoice_form(quote_id: int):
    """Runs just the 'Invoices for this quotation' section, signed in as mary."""
    import streamlit as st

    from views import common, invoices

    st.session_state.setdefault("user", {"username": "mary", "full_name": "Mary Okon", "initials": "MJO",
                                         "role": "admin", "must_change_password": False})
    invoices.quote_section(common.get_store().get_quote(quote_id))


def doc_text(data: bytes) -> str:
    d = docx.Document(io.BytesIO(data))
    return "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)


draft_quote = next(q for q in store.list_quotes() if q["client_name"] == "Draft Client Ltd")
form = AppTest.from_function(invoice_form, args=(draft_quote["id"],), default_timeout=60)
form.run()
check(not form.exception and any(b.label == "Generate invoice" for b in form.button),
      f"a quotation offers 'Create invoice' {form.exception}")
contract = 640_000 + 850_000  # 2 x 320k batteries + 850k inverter, no VAT on that quote
form.session_state[f"inv_{draft_quote['id']}_mode"] = "Percentage"
form.run()
next(n for n in form.number_input if n.label.startswith("Percentage")).set_value(60.0)
form.run()
check(any("894,000.00" in str(m.value) for m in form.metric), "60% part payment shows ₦894,000.00 due")
next(t for t in form.text_input if t.label == "LPO number").input("PO-123")
button(form, "Generate invoice").click().run()
first = store.list_invoices()[0]
month = db.today().strftime("%Y/%m")
check(first["number"] == f"RESYPL/{month}/001" and first["amount_due"] == 894_000 and first["contract_total"] == contract,
      f"invoice saved with company number: {first['number']} {first['amount_due']}")
check({"Download PDF", "Download Word"} <= {b.label for b in form.get("download_button")}, "invoice ready as PDF and Word")
first_text = doc_text(invoice_views.invoice_document(store.get_invoice(first["id"])))
check("AMOUNT DUE (60% PART PAYMENT)" in first_text and "894,000.00" in first_text and "PO-123" in first_text
      and "24280259-0001" in first_text and "1224641552" in first_text,
      "invoice document has part payment, LPO, TIN and bank details")

form2 = AppTest.from_function(invoice_form, args=(draft_quote["id"],), default_timeout=60)
form2.run()
check(any("596,000.00" in str(m.value) for m in form2.metric), "next invoice defaults to the balance ₦596,000.00")
button(form2, "Generate invoice").click().run()
second = store.list_invoices()[0]
check(second["number"] == f"RESYPL/{month}/002" and second["amount_due"] == 596_000, "balance invoice gets the next number")
check("LESS: PREVIOUSLY INVOICED" in doc_text(invoice_views.invoice_document(store.get_invoice(second["id"]))),
      "balance invoice shows what was invoiced before")
form3 = AppTest.from_function(invoice_form, args=(draft_quote["id"],), default_timeout=60)
form3.run()
check(any("Nothing left to invoice" in w.value for w in form3.warning)
      and next(b for b in form3.button if b.label == "Generate invoice").disabled,
      "once fully invoiced, no more invoices can be made for it")

check(store.set_invoice_payment(first["id"], 400_000) == "part_paid", "recording part of the money -> Part paid")
check(store.set_invoice_payment(first["id"], 894_000) == "paid", "recording all of it -> Paid")
wb = invoice_views.waybill_document(store.get_invoice(first["id"]), db.today())
wb_text = doc_text(wb)
check("WAYBILL/DELIVERY" in wb_text and "Battery" in wb_text and "320,000" not in wb_text,
      "waybill lists the items without prices")
check(ie.words(851_400) == "EIGHT HUNDRED AND FIFTY ONE THOUSAND, FOUR HUNDRED NAIRA ONLY", "amount in words in capitals")

boss = new_app()
sign_in(boss, "mary", "secret123")
boss.session_state["main_tab"] = "All invoices"
boss.run()
check(not boss.exception and any(m.label == "Invoiced" for m in boss.metric), f"admin Invoices tab renders {boss.exception}")
boss.session_state["main_tab"] = "Admin"
boss.run()
check(not boss.exception and any("Invoiced per month" in m.value for m in boss.markdown),
      f"admin sees money per month {boss.exception}")
acts = [a["action"] for a in store.activity()]
check("Created invoice" in acts, "invoice creation is in the activity log")

# 13. invoice without a quotation -------------------------------------------------------
maker = new_app()
sign_in(maker, "mary", "secret123")
check([t.label.split(" ", 1)[1] for t in maker.tabs if t.label.startswith(":material")] == ["Quotation", "Invoice", "Waybill"],
      f"New document offers Quotation | Invoice | Waybill: {[t.label for t in maker.tabs]}")
widgets = {w.key: w for w in maker.text_input}
widgets["direct_inv_client"].input("Pladis Foods Nigeria Limited")
widgets["direct_inv_attn"].input("Babalola")
widgets["direct_inv_lpo"].input("PO-16967-01")
next(t for t in maker.text_area if t.key == "direct_inv_raw").input(
    "Cat 6 Network Cable (2 pcs) 187,000\n25mm PVC Flex Pipes 4 pcs 19,800")
maker.run()
direct_total = (2 * 187_000 + 4 * 19_800) * 1.075  # 453,200 + 7.5% VAT
check(any(f"{direct_total:,.2f}" in str(m.value) for m in maker.metric), f"direct invoice total ₦{direct_total:,.2f}")
maker.session_state["direct_inv_mode"] = "Percentage"
maker.session_state["direct_inv_terms_kind"] = "Percentage"  # payment terms as a percentage
maker.run()
next(n for n in maker.number_input if n.key == "direct_inv_pct").set_value(50.0)
maker.run()
next(b for b in maker.button if b.key == "direct_inv_go").click().run()
direct = store.list_invoices()[0]
check(direct["quote_id"] is None and direct["job_id"] == direct["id"] and direct["quote_reference"] == ""
      and round(direct["amount_due"], 2) == round(direct_total / 2, 2),
      f"direct invoice saved without a quotation, 50% part: {direct['number']} {direct['amount_due']}")
check(not maker.session_state["direct_inv_client"] if "direct_inv_client" in maker.session_state else True,
      "the form is emptied after generating, so it is not made twice")
check(store.invoiced_for_job(direct["id"]) == round(direct_total / 2, 2), "the job remembers what was invoiced")
direct_terms = store.get_invoice(direct["id"])["data"]["invoice"]["terms"]
check(direct_terms == "70% with order, 30% after delivery" and direct["due_date"] == direct["invoice_date"],
      f"payment terms as a percentage are printed and due straight away: {direct_terms!r}")


def invoice_detail(invoice_id: int):
    import streamlit as st

    from views import invoices

    st.session_state.setdefault("user", {"username": "mary", "full_name": "Mary Okon", "initials": "MJO",
                                         "role": "admin", "must_change_password": False})
    invoices._detail(invoice_id, {})


detail = AppTest.from_function(invoice_detail, args=(direct["id"],), default_timeout=60)
detail.run()
check(not detail.exception and any(f"{direct_total / 2:,.2f}" in str(m.value) for m in detail.metric)
      and any(b.key == f"next_{direct['id']}_go" for b in detail.button),
      f"a part-paid direct invoice offers 'Invoice the rest' with the balance {detail.exception}")
next(b for b in detail.button if b.key == f"next_{direct['id']}_go").click().run()
rest = store.list_invoices()[0]
check(rest["job_id"] == direct["id"] and round(rest["amount_due"], 2) == round(direct_total / 2, 2)
      and "LESS: PREVIOUSLY INVOICED" in doc_text(invoice_views.invoice_document(store.get_invoice(rest["id"]))),
      "the balance invoice belongs to the same job and shows what was invoiced before")

# 14. waybill without an invoice ---------------------------------------------------------
widgets = {w.key: w for w in maker.text_input}
widgets["direct_wb_client"].input("Arkseco Nig. Ltd")
maker.session_state["direct_wb_terms_kind"] = "Words"
maker.run()
next(t for t in maker.text_input if t.key == "direct_wb_terms_text").input("Cash on delivery")
next(t for t in maker.text_area if t.key == "direct_wb_raw").input("2 x Cat 6 Network Cable\n4 pcs 25mm PVC Flex Pipes")
maker.run()
next(b for b in maker.button if b.key == "direct_wb_go").click().run()
waybill = store.list_waybills()[0] if store.list_waybills() else None
check(waybill is not None and waybill["invoice_number"] == "" and waybill["items_count"] == 2,
      f"waybill saved without an invoice number: {waybill}")
wb_doc = doc_text(invoice_views.waybill_document_from(store.get_waybill(waybill["id"])["data"], db.today()))
check("Cash on delivery" in wb_doc, "payment terms written in words appear on the waybill")
check("WAYBILL/DELIVERY" in wb_doc and "Arkseco Nig. Ltd" in wb_doc and "Cat 6 Network Cable" in wb_doc
      and "187,000" not in wb_doc, "stand-alone waybill document has client and items, no prices")
check({"Download PDF", "Download Word"} <= {b.label for b in maker.get("download_button")},
      "stand-alone waybill offered as PDF and Word")
check("Made waybill" in [a["action"] for a in store.activity()], "waybill is in the activity log")

print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
store.engine.dispose()
sys.exit(1 if failures else 0)
