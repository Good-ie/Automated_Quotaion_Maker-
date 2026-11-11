# ⚡ Rehoboth Quotation Maker

A **Custom CPQ (Configure, Price, Quote) Automation Engine** specifically engineered for the electrical power and engineering industry. 

This proprietary Streamlit application acts as a bridge between quick field communications (like WhatsApp messages) and highly professional, client-ready financial documents. It transforms a tedious, error-prone administrative chore into a secure, one-click software solution that empowers staff to generate flawless proposals in seconds.

### ⚙️ Core Capabilities
* **Intelligent Data Processing:** Uses advanced pattern recognition to parse unstructured WhatsApp text, extracting product names, quantities, and raw prices.
* **Smart Model Extraction:** Automatically detects alphanumeric part numbers (e.g., CAD50M7) and isolates them into a dedicated column.
* **Dynamic Financial Engine:** Instantly calculates custom markups, optional VAT, subtotals, and automatically translates large Naira totals into formal English text (e.g., "Five Million Naira Only").
* **Secure Document Generation:** Bypasses standard Word tags to forcefully construct custom table rows inside a proprietary template. Files are processed entirely in the computer’s invisible memory (`BytesIO`) and pushed straight to the user's Downloads folder to protect client data and prevent folder clutter.

---

### 🌍 Online for all staff, free (Streamlit Community Cloud + Supabase)
The app runs online, so staff can use it from the office, home or site. Everything is saved
permanently in a free Supabase database: staff accounts, every quotation (with all its items and
prices, so it can be downloaded again exactly as sent), an activity log, saved items and the
Word template. Nothing is lost when the app restarts. Total cost: ₦0.

**One-time set-up (about 20 minutes):**

1. **Create the database.** Sign up at [supabase.com](https://supabase.com) (free plan), click
   **New project**, give it a name, choose a strong database password (save it somewhere safe),
   and pick the region **East US (North Virginia)**: the app's servers are in the US, so this keeps
   it fast. When the project is ready, click **Connect** at the top and choose **Session pooler**.
   Note the *host*, *port*, *user* (looks like `postgres.abcdefgh`) and *database*.
2. **Push the code to GitHub** (private repository). `template.docx`, the old JSON files, the
   local database and any secrets are excluded by `.gitignore`, so they never reach GitHub.
3. **Deploy.** Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub, click
   **Create app**, choose this repository, branch `main`, file `app.py`. Under
   **Advanced settings**, pick Python 3.12 and paste the secrets (format in
   `.streamlit/secrets.example.toml`), filling in the Supabase values and a long `setup_key`
   phrase of your choice. Click **Deploy**.
4. **First visit.** Open the app link, create the **admin account** (it asks for the
   `setup_key`), then go to **Admin › Template** and upload `template.docx`. In
   **Admin › Backup** you can import the old `product_catalog.json` and `quotes_history.json`.
5. **Add staff** in **Admin › Staff accounts** with a temporary password. They choose their own
   password at first sign-in. Share the app link with them.

**Good to know about the free plans:**
* If nobody uses the app for a while it sleeps; the next visitor waits about 30 seconds while it wakes up.
* Supabase pauses a free project after 7 days without any use. Your data is kept; click **Restore** in the Supabase dashboard.
* Supabase's free plan has no automatic backups: download one from **Admin › Backup** about once a week (the app reminds you).

### 🔐 Accounts and accountability
* **Staff** sign in, prepare quotations and see **My quotations** (their own history, re-download any of them).
* **Admin** also sees **All quotations** (filter by staff), and the **Admin** tab: this month's quotations and value per staff member, the full **activity log** (sign-ins, failed sign-ins, quotations generated, past quotations downloaded, cancellations, account changes), staff accounts, the template and backups.
* Nothing is overwritten: generating again with the same reference saves a new **revision**; a reference already used for another client is refused; cancelled quotations stay in history.
* Reference numbers use each person's initials from their account: `RSL/<initials>/<year>/<month>/<number>`.
* Passwords are stored as salted hashes; 5 wrong attempts lock an account for 15 minutes.
* **10-minute sessions:** after signing in, a refresh keeps you signed in. After 10 minutes with no activity you are signed out automatically and the session key is deleted (from the browser and the database). Signing out, an admin switching an account off, or an admin password reset also delete it immediately.
* **Drafts:** the quotation being prepared is saved as you work. After signing in again, the app offers to continue it, with your edits intact. The admin overview lists unfinished quotations.

### 🚀 Running it on your own PC (for testing)
```bash
pip install -r requirements.txt
streamlit run app.py
```
Without secrets it uses a local database file, `data/rehoboth.db`, and on first start copies in
`template.docx` and the old JSON files from this folder. To use the online database from your PC
instead, copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and fill it in.

The template must contain a table whose header row has **exactly 7 columns** in this order:
`S/N` | `MODEL NO` | `DESCRIPTIONS` | `UNIT COST =N=` | `QTY` | `TOTAL COST =N=` | `AVAILABILITIES`

### 🧠 How the item reader works (free, offline)
* **Understands staff wording:** "16 nos", "two pieces", "a pair of", "(4)", "qty: 4", "x4", "16 batteries"; prices like "320k", "1.45m", "₦85,000", "320,000 each = 5,120,000"; availability like "ex-stock" or "2 weeks"; and instructions like "markup 15%", "5% discount", "add VAT" (shown as a suggestion with an **Apply** button).
* **Knows specs are not quantities:** 200AH, 10kVA, 63A, 16mm, 4core, 12V.
* **Flags guesses:** anything uncertain gets a note in the review table.
* **Learns from your quotes:** every generated quotation saves its items and base prices to the database. Next time, a matching item gets its model number, full description and missing price filled in, with a warning if the price moved more than 20%. See them in the **Saved items** tab (the admin can edit them).
* **Tests:** run `python tests/test_parser.py` and `python tests/test_app.py` (the whole app, with a throwaway database). When staff find a message the app reads wrongly, add that line to `CASES` in the test file, then fix the parser until all checks pass.

### 🗂 Project layout
* `app.py`: start-up, sign-in gate and the main tabs.
* `views/`: the screens (`quote.py` new quotation, `history.py`, `saved_items.py`, `admin.py`, `auth.py` sign-in and passwords).
* `store.py`: the database (accounts, quotations, activity log, saved items, templates).
* `item_parser.py`: reads pasted messages into items.
* `catalog.py`: the product memory (matching saved items and prices).
* `quote_engine.py`: pricing, reference numbers and Word generation.
* `tests/`: `test_parser.py` (item reader) and `test_app.py` (whole app).
* `.streamlit/config.toml`: colours and fonts. `.streamlit/secrets.example.toml`: secrets format.
