"""Investor profile validation: risk tolerance, horizon, goals, planning inputs.

No file I/O here — db.py loads/saves the JSONB `profiles.settings` blob per
user_id and runs it through _coerce() before every write, same validation
as the personal instance just against Postgres instead of profile.json.
"""

DEFAULTS = {
    "name": "",
    "risk_tolerance": "moderate",
    "horizon_years": 10,
    "goals": ["retirement"],
    "age": 35,
    "income_stability": "stable",
    "emergency_fund": True,
    "notes": "",
    "coastfire_retire_age": 65,
    "coastfire_annual_spend": 0,
    "coastfire_return_pct": 7.0,
    "coastfire_monthly_contribution": 0,
    "contrib_401k_ytd": 0,
    "contrib_hsa_ytd": 0,
    "contrib_roth_ytd": 0,
    "target_stock_pct": 90.0,
    "tlh_threshold_pct": -10.0,
    "monthly_expense": 0,
}

VALID_RISK = {"conservative", "moderate", "aggressive"}
VALID_GOALS = {"retirement", "wealth_building", "income", "preservation"}
VALID_STABILITY = {"stable", "variable", "uncertain"}

_FLOAT_FIELDS = [
    ("coastfire_annual_spend", 0.0, None),
    ("coastfire_return_pct", 0.0, 20.0),
    ("coastfire_monthly_contribution", 0.0, None),
    ("contrib_401k_ytd", 0.0, None),
    ("contrib_hsa_ytd", 0.0, None),
    ("contrib_roth_ytd", 0.0, None),
    ("target_stock_pct", 0.0, 100.0),
    ("tlh_threshold_pct", -100.0, 0.0),
    ("monthly_expense", 0.0, None),
]


def _clamped_float(p, field, lo, hi):
    try:
        v = float(p.get(field, DEFAULTS[field]))
    except (TypeError, ValueError):
        v = DEFAULTS[field]
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _coerce(p):
    """Validate and clamp profile fields to safe ranges."""
    p = p or {}

    risk = str(p.get("risk_tolerance", DEFAULTS["risk_tolerance"])).strip().lower()
    if risk not in VALID_RISK:
        risk = DEFAULTS["risk_tolerance"]

    try:
        horizon = int(p.get("horizon_years", DEFAULTS["horizon_years"]))
    except (TypeError, ValueError):
        horizon = DEFAULTS["horizon_years"]
    horizon = max(1, min(40, horizon))

    raw_goals = p.get("goals", DEFAULTS["goals"])
    if isinstance(raw_goals, str):
        raw_goals = [raw_goals]
    goals = [g for g in (raw_goals or []) if g in VALID_GOALS]
    if not goals:
        goals = list(DEFAULTS["goals"])

    try:
        age = int(p.get("age", DEFAULTS["age"]))
    except (TypeError, ValueError):
        age = DEFAULTS["age"]
    age = max(18, min(100, age))

    stability = str(p.get("income_stability", DEFAULTS["income_stability"])).strip().lower()
    if stability not in VALID_STABILITY:
        stability = DEFAULTS["income_stability"]

    emergency = bool(p.get("emergency_fund", DEFAULTS["emergency_fund"]))
    notes = str(p.get("notes", "")).strip()
    name = str(p.get("name", DEFAULTS["name"])).strip()

    try:
        cf_retire_age = int(p.get("coastfire_retire_age", DEFAULTS["coastfire_retire_age"]))
    except (TypeError, ValueError):
        cf_retire_age = DEFAULTS["coastfire_retire_age"]
    cf_retire_age = max(18, min(100, cf_retire_age))

    out = {
        "name": name,
        "risk_tolerance": risk,
        "horizon_years": horizon,
        "goals": goals,
        "age": age,
        "income_stability": stability,
        "emergency_fund": emergency,
        "notes": notes,
        "coastfire_retire_age": cf_retire_age,
    }
    for field, lo, hi in _FLOAT_FIELDS:
        out[field] = _clamped_float(p, field, lo, hi)
    return out


def profile_summary(profile):
    """One-line summary for sidebar display."""
    risk = (profile or {}).get("risk_tolerance", "?").title()
    horizon = (profile or {}).get("horizon_years", "?")
    return f"{risk} · {horizon}yr horizon"
