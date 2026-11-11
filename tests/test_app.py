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
check([t.label for t in at.tabs][:4] == ["New quotation", "All quotations", "Saved items", "Admin"], "admin sees 4 tabs")

store = db.Store(os.environ["REHOBOTH_DATABASE_URL"])
check(store.latest_template() is not None, "template.docx was stored in the database")
base = len(store.list_quotes())  # old quotes_history.json records are imported too
print(f"     ({base} old record(s) imported, {len(store.catalog())} saved item(s))")

# 2. generate a quotation ---------------------------------------------------------
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
check(any(b.label == "Download Word document" for b in at.get("download_button")), "download button shown")

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
check([t.label for t in at3.tabs][:3] == ["New quotation", "My quotations", "Saved items"] and len(at3.tabs) >= 3
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
    return "user" in app.session_state and any(t.label == "New quotation" for t in app.tabs)


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

at.session_state["main_tab"] = "Admin"
at.run()
check(not at.exception, f"admin tab with sign-up settings renders {at.exception}")

print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
store.engine.dispose()
sys.exit(1 if failures else 0)
