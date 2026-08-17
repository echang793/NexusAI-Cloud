"""Postgres persistence layer — every function below takes a user_id and
scopes its query to that user. Replaces the personal instance's flat-JSON-
file load_X()/save_X() functions across accounts.py/profile.py/portfolio.py/
watchlist.py/nw_snapshots.py; those modules keep their pure validation
logic (_coerce, DEFAULTS, etc.) and this module calls into it before every
write, same validation just against Postgres instead of files.

Uses SQLAlchemy Core (not the ORM) — plain parameterized SQL via `text()`,
no session/unit-of-work ceremony needed at this scale.
"""

import datetime
import os

from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash, generate_password_hash

import accounts as ac
import profile as pr
import watchlist as wl

_DATABASE_URL = os.environ["DATABASE_URL"]
# Railway (and most providers) hand out "postgres://" or "postgresql://" —
# SQLAlchemy defaults either to the psycopg2 dialect, but requirements.txt
# installs psycopg (v3), so force the "+psycopg" driver explicitly.
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif _DATABASE_URL.startswith("postgresql://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(_DATABASE_URL, pool_pre_ping=True)


def init_db():
    """Create tables if they don't exist yet. Idempotent — safe to call on every boot."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                institution TEXT NOT NULL DEFAULT '',
                is_liability BOOLEAN NOT NULL DEFAULT false,
                is_invested BOOLEAN NOT NULL DEFAULT false,
                balance NUMERIC(14,2) NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                updated DATE NOT NULL DEFAULT CURRENT_DATE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS holdings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                ticker TEXT NOT NULL,
                shares NUMERIC(18,6) NOT NULL,
                avg_cost NUMERIC(14,4) NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_holdings_user ON holdings(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ticker TEXT NOT NULL,
                buy_below NUMERIC(14,4),
                sell_above NUMERIC(14,4),
                note TEXT NOT NULL DEFAULT ''
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS nw_snapshots (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                period TEXT NOT NULL,
                date DATE NOT NULL,
                value NUMERIC(14,2) NOT NULL,
                investments NUMERIC(14,2) NOT NULL,
                other_assets NUMERIC(14,2) NOT NULL,
                liabilities NUMERIC(14,2) NOT NULL,
                recorded_at DATE NOT NULL,
                PRIMARY KEY (user_id, period)
            )
        """))


# --- Users / auth ------------------------------------------------------------
def create_user(email: str, password: str):
    """Create a user + an empty default profile row. Returns the new user_id, or
    None if the email is already taken."""
    email = email.strip().lower()
    pw_hash = generate_password_hash(password)
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT id FROM users WHERE email=:e"), {"e": email}).first()
        if existing:
            return None
        row = conn.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:e, :p) RETURNING id"),
            {"e": email, "p": pw_hash},
        ).first()
        user_id = row[0]
        conn.execute(
            text("INSERT INTO profiles (user_id, settings) VALUES (:u, CAST(:s AS jsonb))"),
            {"u": user_id, "s": _json(pr.DEFAULTS)},
        )
    return user_id


def get_user_by_email(email: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, email, password_hash FROM users WHERE email=:e"),
            {"e": email.strip().lower()},
        ).first()
    return dict(row._mapping) if row else None


def get_user_by_id(user_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, email, password_hash FROM users WHERE id=:u"), {"u": user_id}
        ).first()
    return dict(row._mapping) if row else None


def verify_password(user_row: dict, password: str) -> bool:
    return check_password_hash(user_row["password_hash"], password)


# --- Profile -------------------------------------------------------------------
def load_profile(user_id: int) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT settings FROM profiles WHERE user_id=:u"), {"u": user_id}
        ).first()
    return pr._coerce(row[0] if row else {})


def save_profile(user_id: int, profile: dict) -> dict:
    clean = pr._coerce(profile)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO profiles (user_id, settings) VALUES (:u, CAST(:s AS jsonb))
            ON CONFLICT (user_id) DO UPDATE SET settings = CAST(:s AS jsonb)
        """), {"u": user_id, "s": _json(clean)})
    return clean


# --- Accounts --------------------------------------------------------------------
def load_accounts(user_id: int) -> list:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, name, account_type, institution, is_liability, is_invested, balance, notes, updated
            FROM accounts WHERE user_id=:u ORDER BY id
        """), {"u": user_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["balance"] = float(d["balance"])
        d["updated"] = d["updated"].isoformat() if hasattr(d["updated"], "isoformat") else d["updated"]
        out.append(d)
    return out


def save_account(user_id: int, account: dict) -> dict:
    """Insert (no id) or update (has id) one account row. Returns the clean row w/ id."""
    clean = ac._coerce([account])[0]
    with engine.begin() as conn:
        if account.get("id"):
            conn.execute(text("""
                UPDATE accounts SET name=:name, account_type=:account_type, institution=:institution,
                    is_liability=:is_liability, is_invested=:is_invested, balance=:balance,
                    notes=:notes, updated=:updated
                WHERE id=:id AND user_id=:u
            """), {**clean, "id": account["id"], "u": user_id})
            clean["id"] = account["id"]
        else:
            row = conn.execute(text("""
                INSERT INTO accounts (user_id, name, account_type, institution, is_liability, is_invested, balance, notes, updated)
                VALUES (:u, :name, :account_type, :institution, :is_liability, :is_invested, :balance, :notes, :updated)
                RETURNING id
            """), {**clean, "u": user_id}).first()
            clean["id"] = row[0]
    return clean


def delete_account(user_id: int, account_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts WHERE id=:id AND user_id=:u"), {"id": account_id, "u": user_id})


def resolve_account_by_name(user_id: int, name: str) -> int:
    """Case-insensitive match against the user's existing accounts by name;
    auto-creates a new (holding-only, zero-balance) account if unmatched.
    Used by CSV import, where a friend's own account naming can't be known
    in advance — see portfolio.from_csv_raw()."""
    name = (name or "").strip() or "Imported"
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM accounts WHERE user_id=:u AND lower(name)=lower(:n) LIMIT 1
        """), {"u": user_id, "n": name}).first()
    if row:
        return row[0]
    created = save_account(user_id, {"name": name, "account_type": "Taxable", "institution": "",
                                      "is_liability": False, "is_invested": True, "balance": 0})
    return created["id"]


# --- Holdings ----------------------------------------------------------------------
def load_holdings(user_id: int) -> list:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, account_id, ticker, shares, avg_cost FROM holdings WHERE user_id=:u ORDER BY id
        """), {"u": user_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["shares"] = float(d["shares"])
        d["avg_cost"] = float(d["avg_cost"])
        out.append(d)
    return out


def save_holding(user_id: int, holding: dict) -> dict:
    """Insert (no id) or update (has id) one holding row."""
    clean = {
        "ticker": str(holding.get("ticker", "")).strip().upper(),
        "shares": float(holding.get("shares", 0) or 0),
        "avg_cost": float(holding.get("avg_cost", 0) or 0),
        "account_id": int(holding["account_id"]),
    }
    with engine.begin() as conn:
        if holding.get("id"):
            conn.execute(text("""
                UPDATE holdings SET ticker=:ticker, shares=:shares, avg_cost=:avg_cost, account_id=:account_id
                WHERE id=:id AND user_id=:u
            """), {**clean, "id": holding["id"], "u": user_id})
            clean["id"] = holding["id"]
        else:
            row = conn.execute(text("""
                INSERT INTO holdings (user_id, account_id, ticker, shares, avg_cost)
                VALUES (:u, :account_id, :ticker, :shares, :avg_cost) RETURNING id
            """), {**clean, "u": user_id}).first()
            clean["id"] = row[0]
    return clean


def delete_holding(user_id: int, holding_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM holdings WHERE id=:id AND user_id=:u"), {"id": holding_id, "u": user_id})


def all_known_tickers() -> set:
    """Union of every user's tickers — used to prune the shared market-data
    price/period caches without evicting a ticker another user still needs."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT ticker FROM holdings")).all()
    return {r[0] for r in rows}


# --- Watchlist ----------------------------------------------------------------------
def load_watchlist(user_id: int) -> list:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, ticker, buy_below, sell_above, note FROM watchlist WHERE user_id=:u ORDER BY id
        """), {"u": user_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["buy_below"] = float(d["buy_below"]) if d["buy_below"] is not None else None
        d["sell_above"] = float(d["sell_above"]) if d["sell_above"] is not None else None
        out.append(d)
    return out


def save_watchlist_item(user_id: int, item: dict) -> dict:
    clean = wl._coerce_item(item)
    if clean is None:
        return None
    with engine.begin() as conn:
        if item.get("id"):
            conn.execute(text("""
                UPDATE watchlist SET ticker=:ticker, buy_below=:buy_below, sell_above=:sell_above, note=:note
                WHERE id=:id AND user_id=:u
            """), {**clean, "id": item["id"], "u": user_id})
            clean["id"] = item["id"]
        else:
            row = conn.execute(text("""
                INSERT INTO watchlist (user_id, ticker, buy_below, sell_above, note)
                VALUES (:u, :ticker, :buy_below, :sell_above, :note) RETURNING id
            """), {**clean, "u": user_id}).first()
            clean["id"] = row[0]
    return clean


def delete_watchlist_item(user_id: int, item_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM watchlist WHERE id=:id AND user_id=:u"), {"id": item_id, "u": user_id})


# --- Net-worth snapshots (replaces nw_snapshots.py's file-backed version) ----------
def record_snapshot(user_id: int, net_worth: float, investments: float = 0.0,
                     other_assets: float = 0.0, liabilities: float = 0.0) -> None:
    """Record/update the current calendar month's snapshot (idempotent per user per month)."""
    if net_worth is None:
        return
    today = datetime.date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO nw_snapshots (user_id, period, date, value, investments, other_assets, liabilities, recorded_at)
            VALUES (:u, :period, :date, :value, :investments, :other_assets, :liabilities, :recorded_at)
            ON CONFLICT (user_id, period) DO UPDATE SET
                value = :value, investments = :investments, other_assets = :other_assets,
                liabilities = :liabilities, recorded_at = :recorded_at
        """), {
            "u": user_id, "period": period, "date": f"{period}-01",
            "value": round(float(net_worth)), "investments": round(float(investments)),
            "other_assets": round(float(other_assets)), "liabilities": round(float(liabilities)),
            "recorded_at": today.isoformat(),
        })


def load_nw_history(user_id: int) -> list:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT date, value, investments, other_assets, liabilities, recorded_at
            FROM nw_snapshots WHERE user_id=:u ORDER BY period
        """), {"u": user_id}).mappings().all()
    return [{
        "date": r["date"].isoformat(), "value": float(r["value"]),
        "investments": float(r["investments"]), "otherAssets": float(r["other_assets"]),
        "liabilities": float(r["liabilities"]), "recordedAt": r["recorded_at"].isoformat(),
    } for r in rows]


def has_real_history(user_id: int, min_points: int = 2) -> bool:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM nw_snapshots WHERE user_id=:u"), {"u": user_id}).first()
    return (row[0] if row else 0) >= min_points


def _json(d):
    import json
    return json.dumps(d)
