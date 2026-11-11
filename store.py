"""Permanent storage: staff accounts, quotations, activity log, saved items, templates.

Uses the online Supabase (Postgres) database when the app's secrets contain a
[database] section, so nothing is lost when the app restarts. Without secrets
(e.g. testing on your own PC) it falls back to a local file, data/rehoboth.db.

Quotations are stored as data (client, items, prices) plus the template version they
used, not as Word files: the template embeds ~6 MB of fonts, so storing every file
would fill the free database quickly. Rebuilding from the data gives the same document.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import (
    URL,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import catalog
import quote_engine as qe

ROOT = Path(__file__).resolve().parent
LOCAL_DB = ROOT / "data" / "rehoboth.db"

# Nigeria is UTC+1 all year. The cloud server runs on UTC, so set the clock explicitly.
WAT = dt.timezone(dt.timedelta(hours=1), "WAT")

MAX_ATTEMPTS = 5          # wrong passwords in a row before the account is locked
LOCK_MINUTES = 15
MIN_PASSWORD = 6
_PBKDF2_ROUNDS = 240_000


def now() -> dt.datetime:
    """Current Nigerian time, without timezone info (stored as plain timestamps)."""
    return dt.datetime.now(WAT).replace(tzinfo=None, microsecond=0)


def today() -> dt.date:
    return now().date()


# ---------------------------------------------------------------------------
# Tables (prefixed rq_ so they never clash with anything else in the database)
# ---------------------------------------------------------------------------
metadata = MetaData()

users = Table(
    "rq_users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(40), nullable=False, unique=True),
    Column("full_name", String(100), nullable=False),
    Column("initials", String(4), nullable=False, unique=True),
    Column("role", String(10), nullable=False, default="staff"),  # "admin" or "staff"
    Column("pw_hash", String(200), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("must_change_password", Boolean, nullable=False, default=False),
    Column("failed_attempts", Integer, nullable=False, default=0),
    Column("locked_until", DateTime),
    Column("created_at", DateTime, nullable=False),
    Column("last_login", DateTime),
)

quotes = Table(
    "rq_quotes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("reference", String(60), nullable=False, index=True),
    Column("revision", Integer, nullable=False, default=1),
    Column("created_at", DateTime, nullable=False, index=True),
    Column("username", String(40), nullable=False, index=True),
    Column("client_name", String(200), nullable=False),
    Column("title", String(300), nullable=False, default=""),
    Column("options_count", Integer, nullable=False, default=1),
    Column("items_count", Integer, nullable=False, default=0),
    Column("grand_total", Float, nullable=False, default=0.0),
    Column("status", String(10), nullable=False, default="active"),  # active / void / legacy
    Column("void_reason", Text),
    Column("template_id", Integer),
    Column("data", Text),  # JSON: everything needed to rebuild the Word document
    UniqueConstraint("reference", "revision", name="uq_rq_quotes_ref_rev"),
)

activity = Table(
    "rq_activity",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime, nullable=False, index=True),
    Column("username", String(40), nullable=False, index=True),
    Column("action", String(60), nullable=False),
    Column("details", Text, nullable=False, default=""),
)

catalog_items = Table(
    "rq_catalog",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("description", Text, nullable=False),
    Column("model", String(100), nullable=False, default=""),
    Column("unit_cost", Float, nullable=False, default=0.0),
    Column("availability", String(100), nullable=False, default=""),
    Column("last_quoted", String(10), nullable=False, default=""),  # ISO date
    Column("times_quoted", Integer, nullable=False, default=0),
)

templates = Table(
    "rq_templates",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("uploaded_at", DateTime, nullable=False),
    Column("username", String(40), nullable=False),
    Column("filename", String(200), nullable=False),
    Column("size", Integer, nullable=False),
    Column("data", LargeBinary, nullable=False),
)

settings = Table(
    "rq_settings",
    metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
)

sessions = Table(
    "rq_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),  # only a hash: a leaked table can't sign anyone in
    Column("username", String(40), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False, index=True),
)

drafts = Table(
    "rq_drafts",
    metadata,
    Column("username", String(40), primary_key=True),  # one unfinished quotation per person
    Column("saved_at", DateTime, nullable=False),
    Column("client_name", String(200), nullable=False, default=""),
    Column("data", Text, nullable=False),
)

_CATALOG_FIELDS =["description", "model", "unit_cost", "availability", "last_quoted", "times_quoted"]


# ---------------------------------------------------------------------------
# Passwords (salted PBKDF2: built into Python, nothing to pay for)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.isdigit() and len(set(password)) == 1:
        return "That password is too easy to guess."
    return None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def database_url(config: Mapping[str, Any] | None) -> URL | str:
    """Build the connection URL from the [database] secrets, or use the local file."""
    if os.environ.get("REHOBOTH_DATABASE_URL"):  # used by the tests
        return os.environ["REHOBOTH_DATABASE_URL"]
    if not config:
        LOCAL_DB.parent.mkdir(exist_ok=True)
        return f"sqlite:///{LOCAL_DB.as_posix()}"
    if config.get("url"):
        return config["url"]
    return URL.create(
        "postgresql+psycopg2",
        username=config["user"],
        password=config["password"],  # passed separately, so special characters are fine
        host=config["host"],
        port=int(config.get("port", 5432)),
        database=config.get("dbname", "postgres"),
        query={"sslmode": "require"},
    )


def _row(r) -> dict:
    return dict(r._mapping)


class Store:
    def __init__(self, url: URL | str):
        is_sqlite = str(url).startswith("sqlite")
        options: dict[str, Any] = {"pool_pre_ping": True}
        if is_sqlite:
            options["connect_args"] = {"check_same_thread": False}
        else:
            # Supabase's free plan allows a limited number of connections
            options.update(pool_size=3, max_overflow=2, pool_recycle=300)
        self.engine: Engine = create_engine(url, **options)
        self.is_cloud = not is_sqlite
        metadata.create_all(self.engine)
        if self.is_cloud:
            self._lock_public_api()

    def _lock_public_api(self) -> None:
        """Supabase also exposes tables through its web API. Turning on row level
        security with no rules blocks that route; this app connects directly as the
        database owner, which is not affected."""
        with self.engine.begin() as conn:
            for table in metadata.sorted_tables:
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY')

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.engine.connect() as conn:
            value = conn.execute(select(settings.c.value).where(settings.c.key == key)).scalar()
        return default if value is None else value

    def set_setting(self, key: str, value: str) -> None:
        with self.engine.begin() as conn:
            done = conn.execute(update(settings).where(settings.c.key == key).values(value=value))
            if not done.rowcount:
                conn.execute(insert(settings).values(key=key, value=value))

    # -- activity log ---------------------------------------------------------
    def log(self, username: str, action: str, details: str = "") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(activity).values(at=now(), username=username, action=action, details=details)
            )

    def activity(
        self,
        username: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        q = select(activity).order_by(activity.c.at.desc(), activity.c.id.desc()).limit(limit)
        if username:
            q = q.where(activity.c.username == username)
        if since:
            q = q.where(activity.c.at >= since)
        if until:
            q = q.where(activity.c.at < until)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q)]

    # -- users ---------------------------------------------------------------
    def user_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(users)).scalar_one()

    def get_user(self, username: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(users).where(users.c.username == username.strip().lower())).first()
        return _row(r) if r else None

    def list_users(self) -> list[dict]:
        cols = [c for c in users.c if c.name != "pw_hash"]
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(*cols).order_by(users.c.full_name))]

    def create_user(
        self, username: str, full_name: str, initials: str, role: str, password: str,
        must_change_password: bool = True,
    ) -> None:
        username, initials = username.strip().lower(), initials.strip().upper()
        if not username.replace(".", "").replace("_", "").isalnum():
            raise ValueError("Username can only use letters, numbers, dots and underscores.")
        if not initials.isalpha() or not 2 <= len(initials) <= 4:
            raise ValueError("Initials must be 2 to 4 letters (they go into the quote reference).")
        if problem := password_problem(password):
            raise ValueError(problem)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(users).values(
                        username=username, full_name=full_name.strip(), initials=initials,
                        role=role, pw_hash=hash_password(password), active=True,
                        must_change_password=must_change_password, failed_attempts=0,
                        created_at=now(),
                    )
                )
        except IntegrityError as exc:
            raise ValueError("That username or those initials are already taken.") from exc

    def update_user(self, username: str, **fields) -> None:
        allowed = {"full_name", "role", "active"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if values.get("role") == "staff" or values.get("active") is False:
            target = self.get_user(username)
            others = [
                u for u in self.list_users()
                if u["role"] == "admin" and u["active"] and u["username"] != username
            ]
            if target and target["role"] == "admin" and not others:
                raise ValueError("Keep at least one active admin account.")
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.username == username).values(**values))
        if values.get("active") is False:
            self.end_user_sessions(username)

    def set_password(self, username: str, password: str, must_change: bool = False) -> None:
        """must_change=True is an admin reset: it also signs the person out everywhere."""
        if problem := password_problem(password):
            raise ValueError(problem)
        with self.engine.begin() as conn:
            conn.execute(
                update(users).where(users.c.username == username).values(
                    pw_hash=hash_password(password), must_change_password=must_change,
                    failed_attempts=0, locked_until=None,
                )
            )
        if must_change:
            self.end_user_sessions(username)

    # -- sign-in sessions (the key kept in the browser cookie) -----------------------
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _delete_expired_sessions(self, conn) -> None:
        conn.execute(delete(sessions).where(sessions.c.expires_at <= now()))

    def create_session(self, username: str, idle_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            self._delete_expired_sessions(conn)
            conn.execute(
                insert(sessions).values(
                    token_hash=self._hash_token(token), username=username, created_at=now(),
                    expires_at=now() + dt.timedelta(seconds=idle_seconds),
                )
            )
        return token

    def touch_session(self, token: str, idle_seconds: int) -> dict | None:
        """Check a session key and give it another full idle period.

        Returns the account (without password) if the key is valid and the account is
        active; otherwise deletes the key and returns None. Expired keys of everyone are
        deleted on the way.
        """
        token_hash = self._hash_token(token)
        with self.engine.begin() as conn:
            self._delete_expired_sessions(conn)
            row = conn.execute(
                select(users).join(sessions, sessions.c.username == users.c.username)
                .where(sessions.c.token_hash == token_hash)
            ).first()
            if row is None or not row.active:
                conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash))
                return None
            conn.execute(
                update(sessions).where(sessions.c.token_hash == token_hash)
                .values(expires_at=now() + dt.timedelta(seconds=idle_seconds))
            )
        user = _row(row)
        user.pop("pw_hash", None)
        return user

    def end_session(self, token: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == self._hash_token(token)))

    def end_user_sessions(self, username: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.username == username))

    def session_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(sessions)).scalar_one()

    # -- drafts (unfinished quotations, saved as people work) ------------------------
    def save_draft(self, username: str, client_name: str, data: dict) -> None:
        values = {"saved_at": now(), "client_name": client_name[:200], "data": json.dumps(data, default=str)}
        with self.engine.begin() as conn:
            done = conn.execute(update(drafts).where(drafts.c.username == username).values(**values))
            if not done.rowcount:
                conn.execute(insert(drafts).values(username=username, **values))

    def get_draft(self, username: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(drafts).where(drafts.c.username == username)).first()
        if not r:
            return None
        out = _row(r)
        out["data"] = json.loads(out["data"])
        return out

    def delete_draft(self, username: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(drafts).where(drafts.c.username == username))

    def list_drafts(self) -> list[dict]:
        cols = [c for c in drafts.c if c.name != "data"]
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(*cols).order_by(drafts.c.saved_at.desc()))]

    def authenticate(self, username: str, password: str) -> tuple[dict | None, str]:
        """Returns (user, "") on success, or (None, reason). Locks after repeated failures."""
        user = self.get_user(username)
        if user is None:
            verify_password(password, _DUMMY_HASH)  # same delay as a real check
            return None, "Wrong username or password."
        if not user["active"]:
            return None, "This account is switched off. Please speak to the admin."
        if user["locked_until"] and user["locked_until"] > now():
            minutes = max(1, int((user["locked_until"] - now()).total_seconds() // 60) + 1)
            return None, f"Too many wrong attempts. Try again in {minutes} minute(s)."
        if not verify_password(password, user["pw_hash"]):
            failed = user["failed_attempts"] + 1
            values: dict[str, Any] = {"failed_attempts": failed}
            if failed >= MAX_ATTEMPTS:
                values = {"failed_attempts": 0, "locked_until": now() + dt.timedelta(minutes=LOCK_MINUTES)}
            with self.engine.begin() as conn:
                conn.execute(update(users).where(users.c.id == user["id"]).values(**values))
            if failed >= MAX_ATTEMPTS:
                return None, f"Too many wrong attempts. The account is locked for {LOCK_MINUTES} minutes."
            return None, "Wrong username or password."
        with self.engine.begin() as conn:
            conn.execute(
                update(users).where(users.c.id == user["id"]).values(
                    failed_attempts=0, locked_until=None, last_login=now()
                )
            )
        user.pop("pw_hash", None)
        return user, ""

    # -- quotations ------------------------------------------------------------
    def next_reference(self, initials: str, on: dt.date) -> str:
        prefix = qe.reference_prefix(initials, on)
        with self.engine.connect() as conn:
            used = conn.execute(
                select(quotes.c.reference).where(quotes.c.reference.like(prefix + "%"))
            ).scalars().all()
        return qe.next_reference(initials, on, used)

    def latest_for_reference(self, reference: str) -> dict | None:
        q = (
            select(*[c for c in quotes.c if c.name != "data"])
            .where(func.lower(quotes.c.reference) == reference.strip().lower())
            .order_by(quotes.c.revision.desc())
            .limit(1)
        )
        with self.engine.connect() as conn:
            r = conn.execute(q).first()
        return _row(r) if r else None

    def save_quote(
        self, *, reference: str, username: str, client_name: str, title: str,
        options_count: int, items_count: int, grand_total: float,
        template_id: int | None, data: dict,
    ) -> int:
        """Save a new quotation or a new revision of it. Never overwrites. Returns the revision."""
        for _ in range(3):  # two people saving the same reference at once: retry
            with self.engine.connect() as conn:
                last = conn.execute(
                    select(func.max(quotes.c.revision)).where(quotes.c.reference == reference)
                ).scalar()
            revision = (last or 0) + 1
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        insert(quotes).values(
                            reference=reference, revision=revision, created_at=now(),
                            username=username, client_name=client_name, title=title,
                            options_count=options_count, items_count=items_count,
                            grand_total=grand_total, status="active",
                            template_id=template_id, data=json.dumps(data, default=str),
                        )
                    )
                return revision
            except IntegrityError:
                continue
        raise RuntimeError("Could not save the quotation. Please try again.")

    def list_quotes(self, username: str | None = None) -> list[dict]:
        q = select(*[c for c in quotes.c if c.name != "data"]).order_by(
            quotes.c.created_at.desc(), quotes.c.id.desc()
        )
        if username:
            q = q.where(quotes.c.username == username)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q)]

    def get_quote(self, quote_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(quotes).where(quotes.c.id == quote_id)).first()
        if not r:
            return None
        out = _row(r)
        out["data"] = json.loads(out["data"]) if out["data"] else None
        return out

    def void_quote(self, quote_id: int, reason: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(quotes).where(quotes.c.id == quote_id).values(status="void", void_reason=reason)
            )

    # -- saved items (catalog) -------------------------------------------------
    def catalog(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(catalog_items).order_by(catalog_items.c.description))]

    def upsert_catalog(self, entries: list[dict]) -> None:
        with self.engine.begin() as conn:
            for e in entries:
                values = {k: e.get(k) for k in _CATALOG_FIELDS}
                values["model"] = values["model"] or ""
                values["availability"] = values["availability"] or ""
                values["last_quoted"] = values["last_quoted"] or ""
                values["times_quoted"] = int(values["times_quoted"] or 0)
                values["unit_cost"] = float(values["unit_cost"] or 0)
                if e.get("id"):
                    conn.execute(update(catalog_items).where(catalog_items.c.id == int(e["id"])).values(**values))
                else:
                    e["id"] = conn.execute(insert(catalog_items).values(**values)).inserted_primary_key[0]

    def replace_catalog(self, entries: list[dict]) -> None:
        """Save an edited list: rows missing from it are deleted."""
        keep = {int(e["id"]) for e in entries if e.get("id")}
        with self.engine.begin() as conn:
            existing = set(conn.execute(select(catalog_items.c.id)).scalars())
            gone = existing - keep
            if gone:
                conn.execute(delete(catalog_items).where(catalog_items.c.id.in_(gone)))
        self.upsert_catalog(entries)

    # -- Word templates ----------------------------------------------------------
    def add_template(self, data: bytes, filename: str, username: str) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                insert(templates).values(
                    uploaded_at=now(), username=username, filename=filename, size=len(data), data=data
                )
            ).inserted_primary_key[0]

    def list_templates(self) -> list[dict]:
        cols = [c for c in templates.c if c.name != "data"]
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(*cols).order_by(templates.c.id.desc()))]

    def latest_template(self) -> dict | None:
        found = self.list_templates()
        return found[0] if found else None

    def template_bytes(self, template_id: int) -> bytes | None:
        with self.engine.connect() as conn:
            return conn.execute(select(templates.c.data).where(templates.c.id == template_id)).scalar()

    # -- backup and imports --------------------------------------------------------
    def export_backup(self) -> dict:
        with self.engine.connect() as conn:
            data = {
                "exported_at": now().isoformat(),
                "users": self.list_users(),
                "quotations": [_row(r) for r in conn.execute(select(quotes).order_by(quotes.c.id))],
                "activity": [_row(r) for r in conn.execute(select(activity).order_by(activity.c.id))],
                "saved_items": self.catalog(),
                "templates": self.list_templates(),
            }
        for q in data["quotations"]:
            q["data"] = json.loads(q["data"]) if q["data"] else None
        return data

    def import_old_catalog(self, entries: list[dict]) -> int:
        """Merge saved items from the old product_catalog.json."""
        current = self.catalog()
        changed: list[dict] = []
        for old in entries:
            desc = catalog.clean_description(str(old.get("description") or ""))
            price = float(old.get("unit_cost") or 0)
            if not desc or price <= 0:
                continue
            match = catalog.find(desc, str(old.get("model") or ""), current)
            entry = match if match is not None else {}
            if match is None:
                current.append(entry)
            elif str(old.get("last_quoted") or "") < str(match.get("last_quoted") or ""):
                continue  # the database already has a newer price
            entry.update(
                description=desc, model=str(old.get("model") or entry.get("model") or ""),
                unit_cost=price, availability=str(old.get("availability") or ""),
                last_quoted=str(old.get("last_quoted") or ""),
                times_quoted=max(int(old.get("times_quoted") or 0), int(entry.get("times_quoted") or 0)),
            )
            if not any(c is entry for c in changed):
                changed.append(entry)
        self.upsert_catalog(changed)
        return len(changed)

    def import_old_history(self, entries: list[dict]) -> int:
        """Bring in the old quotes_history.json (summary only, so references keep counting)."""
        added = 0
        for old in entries:
            ref = str(old.get("ref") or "").strip()
            if not ref or self.latest_for_reference(ref):
                continue
            try:
                created = dt.datetime.fromisoformat(str(old.get("generated_at")))
            except ValueError:
                created = now()
            with self.engine.begin() as conn:
                conn.execute(
                    insert(quotes).values(
                        reference=ref, revision=1, created_at=created, username="imported",
                        client_name=str(old.get("client") or ""), title=str(old.get("title") or "").strip(),
                        options_count=int(old.get("options") or 1), items_count=0,
                        grand_total=float(old.get("total") or 0), status="legacy",
                    )
                )
            added += 1
        return added

    def import_local_files(self) -> list[str]:
        """One-time import of the old JSON files and template.docx sitting next to the app."""
        if self.get_setting("local_files_imported"):
            return []
        notes = []
        for name, importer in (
            ("product_catalog.json", self.import_old_catalog),
            ("quotes_history.json", self.import_old_history),
        ):
            path = ROOT / name
            if path.exists():
                try:
                    count = importer(json.loads(path.read_text(encoding="utf-8")))
                    notes.append(f"Imported {count} record(s) from {name}")
                except (ValueError, TypeError) as exc:
                    notes.append(f"Could not import {name}: {exc}")
        template = ROOT / "template.docx"
        if template.exists() and self.latest_template() is None:
            self.add_template(template.read_bytes(), template.name, "system")
            notes.append("Stored template.docx in the database")
        self.set_setting("local_files_imported", now().isoformat())
        for note in notes:
            self.log("system", "Imported data", note)
        return notes
