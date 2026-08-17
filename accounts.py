"""Manual accounts (cash, debt, or any account not backed by ticker holdings).

No file I/O and no closed type enum here — persistence lives in db.py,
scoped per user_id, and account_type/institution are free text a friend
types themselves (fully generic, per the multi-tenant design). Two things
that used to be inferred from the personal instance's closed type enum
can't be inferred from an arbitrary free-text string, so they're explicit
booleans set by the user in the add-account form instead:
  - is_liability: subtracts from net worth instead of adding.
  - is_invested: counts toward CoastFIRE/FIRE's "invested" total (e.g. a
    robo-advisor cash-like balance that's actually holding ETFs) rather
    than being idle cash — see planning.py's emergency_fund() and
    server.py's _investable_total(), which now just sum this flag instead
    of matching against {"Taxable","Retirement","Crypto"} type strings.
"""

import datetime

COLUMNS = ["name", "account_type", "institution", "is_liability", "is_invested", "balance", "notes", "updated"]


def is_liability(account):
    """Read the explicit boolean flag — no enum lookup, since type is free text."""
    return bool(account.get("is_liability", False))


def is_invested(account):
    return bool(account.get("is_invested", False))


def _coerce(rows):
    out = []
    for r in rows or []:
        name = str(r.get("name", "") or "").strip()
        account_type = str(r.get("account_type", "") or r.get("type", "") or "").strip() or "Other"
        institution = str(r.get("institution", "") or "").strip()
        liability = bool(r.get("is_liability", False))
        invested = bool(r.get("is_invested", False))
        try:
            raw_bal = r.get("balance", 0)
            balance = float(raw_bal) if raw_bal not in (None, "") else 0.0
            if balance != balance:  # NaN guard
                continue
        except (TypeError, ValueError):
            continue
        if not name and balance == 0:
            continue
        notes = str(r.get("notes", "") or "").strip()
        updated = str(r.get("updated", "") or "").strip() or datetime.date.today().isoformat()
        row = {
            "name": name or account_type,
            "account_type": account_type,
            "institution": institution,
            "is_liability": liability,
            "is_invested": invested,
            "balance": balance,
            "notes": notes,
            "updated": updated,
        }
        if "id" in r:
            row["id"] = r["id"]
        out.append(row)
    return out


def summarize(accounts):
    """Return totals broken out by asset vs liability + by-type breakdown."""
    accounts = _coerce(accounts)
    total_assets = 0.0
    total_liabilities = 0.0
    by_type = {}
    for a in accounts:
        bal = a["balance"]
        by_type.setdefault(a["account_type"], 0.0)
        by_type[a["account_type"]] += bal
        if is_liability(a):
            total_liabilities += abs(bal)
        else:
            total_assets += bal
    return {
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net": total_assets - total_liabilities,
        "by_type": by_type,
        "accounts": accounts,
    }


def net_worth(portfolio_value, accounts_summary):
    """Combine holdings value + other accounts into net worth breakdown."""
    pv = float(portfolio_value or 0)
    assets = pv + accounts_summary["total_assets"]
    liabilities = accounts_summary["total_liabilities"]
    return {
        "investments": pv,
        "other_assets": accounts_summary["total_assets"],
        "total_assets": assets,
        "total_liabilities": liabilities,
        "net_worth": assets - liabilities,
    }
