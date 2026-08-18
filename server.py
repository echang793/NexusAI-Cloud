"""NexusAI Cloud — multi-tenant Flask backend, Postgres-backed, per-user auth.

Adapted from the personal single-user instance: every route is scoped to
current_user.id, the account/holdings schema is fully generic (no hardcoded
Fidelity/Webull buckets — see db.py's accounts/holdings tables), and the AI
advisor chat is out of scope for v1 (rule-based signals/insights only, no
shared Anthropic API key cost/abuse risk).
"""

import datetime
import json
import math
import os
import re
import sys
import threading
import time

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(__file__))

import auth
import coastfire as cf
import contributions as ct
import db
import dividends as dv
import fire as fr
import insights as ins
import mail
import planning as plan
import portfolio as pf
import profile as pr
import rebalance as rb
import taxloss as tlh
import totp as totp_lib
import config
from data import DataError, fetch_data, get_dividend_info, get_fundamentals, get_next_earnings
from indicators import add_indicators, latest_snapshot

DESIGN_DIR = os.path.join(os.path.dirname(__file__), "design")

import logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nexusai_cloud")

app = Flask(__name__, static_folder=DESIGN_DIR)
app.logger.setLevel("WARNING")
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") != "development",
)
auth.init_login_manager(app)
db.init_db()

# In-memory storage — fine for Render's single free instance; resets on
# restart, which is an acceptable tradeoff at friends-app scale (not
# protecting against a determined distributed attacker, just blunting
# casual brute force / signup spam). Applied per-route below.
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


@app.errorhandler(429)
def _rate_limited(e):
    # flask-limiter's default 429 body is plain text, not JSON — every
    # auth page's frontend does `.then(r => r.json())` on the response, so
    # without this handler a rate-limited request throws in the browser and
    # surfaces as a generic "try again" instead of explaining what happened.
    return jsonify({"ok": False, "error": "Too many attempts — please wait a minute and try again."}), 429


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if os.getenv("FLASK_ENV") != "development":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    # No Content-Security-Policy here — the frontend uses inline <script>/
    # style="..." throughout (sections-*.js, index.html); a CSP strict
    # enough to matter would break it wholesale. Accepted tradeoff, not an
    # oversight — the other headers above still block clickjacking/MIME-
    # sniffing attacks without touching the frontend.
    return resp

# ---------------------------------------------------------------------------
# Sector cache (fetched lazily, stored in memory) — shared market data,
# same for every user, safe to stay a single process-global cache.
# ---------------------------------------------------------------------------
_sector_cache: dict[str, str] = {}
_sector_lock = threading.Lock()


def _get_sector(ticker: str) -> str:
    with _sector_lock:
        if ticker in _sector_cache:
            return _sector_cache[ticker]
    try:
        fund = get_fundamentals(ticker)
        sector = fund.get("sector") or "—"
    except Exception:
        sector = "—"
    with _sector_lock:
        _sector_cache[ticker] = sector
    return sector


def _prefetch_sectors(tickers: list[str]) -> None:
    def _run():
        for t in tickers:
            _get_sector(t)
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Dividend rate cache ($/share/year, fetched lazily) — feeds dividends.py.
# Shared market data, safe to stay global.
# ---------------------------------------------------------------------------
_div_cache: dict[str, float] = {}
_div_lock = threading.Lock()


def _prefetch_dividends(tickers: list[str]) -> None:
    for t in tickers:
        with _div_lock:
            if t in _div_cache:
                continue
        try:
            info = get_dividend_info(t)
            rate = info.get("annual_div") or 0.0
        except Exception:
            rate = 0.0
        with _div_lock:
            _div_cache[t] = rate


# ---------------------------------------------------------------------------
# Price cache (TTL from config, batch via yfinance) — shared market data.
# In-memory only (no price_cache.json — Railway's filesystem is ephemeral;
# a cold-start re-fetch from yfinance is cheap and simpler than wiring a
# persistent cache store for market data alone).
# ---------------------------------------------------------------------------
_pcache: dict[str, float | None] = {}
_pcache_ts: dict[str, float] = {}
_pcache_lock = threading.Lock()
_KEEP_TICKERS = {"SPY"}


def _prune_pcache() -> None:
    """Union of every user's tickers — a save from user A must not evict
    prices user B still needs."""
    try:
        keep = db.all_known_tickers() | _KEEP_TICKERS
    except Exception:
        return
    with _pcache_lock:
        for t in [k for k in _pcache if k not in keep]:
            _pcache.pop(t, None)
            _pcache_ts.pop(t, None)


def batch_prices(tickers: list[str]) -> dict[str, float | None]:
    import yfinance as yf
    now = time.time()
    ttl = config.CACHE_TTL_SECONDS

    with _pcache_lock:
        need = [t for t in tickers if now - _pcache_ts.get(t, 0) >= ttl]

    if need:
        try:
            raw = yf.download(need, period="2d", auto_adjust=True, progress=False, threads=True)
            close = raw["Close"] if "Close" in raw else raw
            with _pcache_lock:
                for t in need:
                    try:
                        col = close[t] if len(need) > 1 else close
                        _pcache[t] = float(col.dropna().iloc[-1])
                    except Exception:
                        _pcache[t] = None
                    _pcache_ts[t] = now
        except Exception:
            with _pcache_lock:
                for t in need:
                    if t not in _pcache:
                        _pcache[t] = None
                    _pcache_ts[t] = now

    with _pcache_lock:
        return {t: _pcache.get(t) for t in tickers}


def single_price(ticker: str) -> float | None:
    return batch_prices([ticker]).get(ticker)


# ---------------------------------------------------------------------------
# Period-return cache — price N trading days ago, for P/L horizon toggle.
# In-memory only, same reasoning as _pcache.
# ---------------------------------------------------------------------------
_period_cache: dict[str, dict] = {}
_period_lock = threading.Lock()
_HORIZON_OFFSETS = {"1M": 21, "3M": 63, "6M": 126, "1Y": 251}


def compute_period_prices(tickers: list[str]) -> None:
    import yfinance as yf
    if not tickers:
        return
    try:
        raw = yf.download(tickers, period="1y", auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception as e:
        log.debug("compute_period_prices download failed: %s", e)
        return

    out: dict[str, dict] = {}
    for t in tickers:
        try:
            series = close[t] if len(tickers) > 1 else close
            series = series.dropna()
            if len(series) < 2:
                continue
            horizons = {}
            n = len(series)
            for label, offset in _HORIZON_OFFSETS.items():
                idx = n - 1 - offset
                if idx < 0:
                    idx = 0
                horizons[label] = float(series.iloc[idx])
            recent = series.iloc[-126:] if n > 126 else series
            step = max(1, len(recent) // 24)
            spark = [round(float(v), 2) for v in recent.iloc[::step].tolist()][-24:]
            out[t] = {"h": horizons, "spark": spark}
        except Exception:
            continue

    with _period_lock:
        _period_cache.update(out)


def _period_entry(ticker: str):
    with _period_lock:
        e = _period_cache.get(ticker)
    if not e:
        return None, None
    return e.get("h"), e.get("spark")


def _position_periods(ticker: str, shares: float, price: float, avg_cost: float, pl: float, pl_pct: float) -> dict:
    periods = {"ALL": {"pl": round(pl, 2), "pct": round(pl_pct, 2)}}
    hz, _ = _period_entry(ticker)
    if hz and price:
        for label, then in hz.items():
            if then and then > 0:
                period_pl = (price - then) * shares
                period_pct = (price / then - 1) * 100
                periods[label] = {"pl": round(period_pl, 2), "pct": round(period_pct, 2)}
    return periods


def _position_spark(ticker: str):
    _, spark = _period_entry(ticker)
    return spark if spark and len(spark) >= 2 else None


# ---------------------------------------------------------------------------
# Portfolio risk metrics (Sharpe/Sortino/MaxDD) — PER-USER, in-process dict
# keyed by user_id (this is computed from one user's specific holdings, not
# shared market data — must not leak between users).
# ---------------------------------------------------------------------------
_risk_cache: dict[int, dict] = {}
_risk_lock = threading.Lock()


def compute_portfolio_risk(user_id: int, holdings) -> None:
    import yfinance as yf
    from indicators import sharpe_ratio, sortino_ratio, max_drawdown

    if not holdings:
        return
    tickers = [h["ticker"] for h in holdings]

    try:
        raw = yf.download(list(set(tickers + ["SPY"])), period="1y", auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception:
        return

    try:
        prices = batch_prices(tickers)
        weights = {}
        total = 0.0
        for h in holdings:
            px = prices.get(h["ticker"]) or h["avg_cost"]
            val = h["shares"] * px
            weights[h["ticker"]] = weights.get(h["ticker"], 0.0) + val
            total += val
        if total <= 0:
            return

        port_ret = None
        used_w = 0.0
        for t, val in weights.items():
            try:
                s = close[t] if t in getattr(close, "columns", []) else None
                if s is None:
                    continue
                s = s.dropna()
                if len(s) < 30:
                    continue
                ret = s.pct_change().dropna()
                w = val / total
                contrib = ret * w
                port_ret = contrib if port_ret is None else port_ret.add(contrib, fill_value=0)
                used_w += w
            except Exception:
                continue

        result = {"sharpe": None, "sortino": None, "maxDrawdown": None,
                  "benchmarkSharpe": None, "coverage": round(used_w * 100, 0)}

        if port_ret is not None and len(port_ret) >= 30:
            if used_w > 0:
                port_ret = port_ret / used_w
            cum = (1 + port_ret).cumprod()
            result["sharpe"] = sharpe_ratio(port_ret)
            result["sortino"] = sortino_ratio(port_ret)
            md = max_drawdown(cum)
            result["maxDrawdown"] = round(md * 100, 1) if md is not None else None

        try:
            spy = (close["SPY"] if "SPY" in getattr(close, "columns", []) else close).dropna()
            spy_ret = spy.pct_change().dropna()
            result["benchmarkSharpe"] = sharpe_ratio(spy_ret)
        except Exception:
            pass

        for k in ("sharpe", "sortino", "benchmarkSharpe"):
            if result[k] is not None:
                result[k] = round(result[k], 2)

        with _risk_lock:
            _risk_cache[user_id] = result
    except Exception as e:
        log.debug("compute_portfolio_risk failed: %s", e)
        return


def _risk_metrics(user_id: int) -> dict:
    with _risk_lock:
        r = _risk_cache.get(user_id)
    return dict(r) if r else {"sharpe": None, "sortino": None, "maxDrawdown": None, "benchmarkSharpe": None, "coverage": 0}


# ---------------------------------------------------------------------------
# Account list + positions — fully generic (holdings.account_id is a real
# FK to a user-created account row; no institution/bucket guessing).
# ---------------------------------------------------------------------------
def _build_account_list(positions: list, user_accounts: list) -> list:
    """One card per account row. An account's balance is its manual/cash
    component PLUS the live value of any holdings tagged to it — a single
    account (e.g. a brokerage) can legitimately hold both uninvested cash
    (the manual `balance` field) and stock positions (holdings.account_id)
    at once, and both must count toward net worth. `_computed` just means
    "this card includes a live-priced holdings component", not "the manual
    balance field is ignored" — the personal single-user instance's synthetic
    holdings-only cards conflated those two things because it never had a
    real account row that could carry both at once."""
    held_value: dict[int, float] = {}
    for p in positions:
        held_value[p["account_id"]] = held_value.get(p["account_id"], 0.0) + p["value"]

    accts = []
    for a in user_accounts:
        has_holdings = a["id"] in held_value
        balance = a["balance"] + held_value.get(a["id"], 0.0)
        accts.append({
            "id": a["id"],
            "name": a["name"],
            "type": a["account_type"],
            "institution": a["institution"],
            # Edit forms must write back `a["balance"]` (the manual/cash
            # component only), never the combined `balance` below — else
            # every edit re-adds the holdings portion on top of itself.
            "manualBalance": a["balance"],
            "isLiability": a["is_liability"],
            "isInvested": a["is_invested"],
            "balance": -abs(balance) if a["is_liability"] else balance,
            "updated": a["updated"],
            "_computed": has_holdings,
        })
    return accts


def _build_positions_fast(holdings):
    """Build positions using cached/avg-cost price — instant, no network."""
    positions = []
    total_value = 0.0
    total_cost = 0.0
    for h in holdings:
        price = _pcache.get(h["ticker"]) or h["avg_cost"]
        cost = h["shares"] * h["avg_cost"]
        value = h["shares"] * price
        pl = value - cost
        pl_pct = (pl / cost * 100) if cost else 0.0
        positions.append({
            "id": h.get("id"), "ticker": h["ticker"], "shares": h["shares"], "avg_cost": h["avg_cost"], "price": price,
            "sector": _sector_cache.get(h["ticker"], "—"), "account_id": h["account_id"],
            "value": value, "cost": cost, "pl": pl, "plPct": pl_pct, "weight": 0.0,
            "periods": _position_periods(h["ticker"], h["shares"], price, h["avg_cost"], pl, pl_pct),
            "spark": _position_spark(h["ticker"]),
        })
        total_value += value
        total_cost += cost
    for p in positions:
        p["weight"] = (p["value"] / total_value * 100) if total_value else 0.0
    positions.sort(key=lambda x: -x["value"])
    return positions, total_value, total_cost


def _placeholder_featured(ticker: str, price: float) -> dict:
    return {
        "ticker": ticker, "name": ticker, "price": round(price, 2),
        "change": 0.0, "changePct": 0.0, "sector": "—", "industry": "—",
        "marketCap": "—", "pe": "—", "peFwd": "—", "beta": "—",
        "high52": "—", "low52": "—", "divYield": 0.0, "annualDiv": 0.0,
        "target": 0.0, "upside": 0.0, "rating": "—", "nextEarnings": "—",
        "signal": "HOLD", "action": "HOLD", "confidence": "low",
        "thesis": "Loading…", "technical": "", "fundamental": "", "newsSummary": "",
        "risks": [], "catalysts": [], "news": [],
    }


# ---------------------------------------------------------------------------
# Rule-based technical signal — replaces the personal instance's AI verdict
# (analyst.analyze_ticker). Deterministic: RSI + trend only, no LLM.
# ---------------------------------------------------------------------------
def _rule_based_verdict(snap: dict) -> dict:
    rsi = snap.get("rsi")
    close = snap.get("close")
    sma50 = snap.get("sma50")
    sma200 = snap.get("sma200")
    uptrend = close and sma50 and sma200 and close > sma50 > sma200
    downtrend = close and sma50 and sma200 and close < sma50 < sma200

    if rsi is not None and rsi <= config.RSI_BUY_BELOW and not downtrend:
        signal, confidence = "BUY", "medium" if uptrend else "low"
        thesis = f"RSI at {rsi:.0f} suggests oversold conditions" + (", with price above both moving averages (uptrend intact)." if uptrend else ".")
    elif rsi is not None and rsi >= config.RSI_SELL_ABOVE:
        signal, confidence = "SELL", "medium"
        thesis = f"RSI at {rsi:.0f} suggests overbought conditions — consider trimming or waiting for a pullback."
    elif uptrend:
        signal, confidence = "BUY", "low"
        thesis = "Price is above both the 50 and 200-day moving averages (uptrend), RSI is neutral."
    elif downtrend:
        signal, confidence = "SELL", "low"
        thesis = "Price is below both the 50 and 200-day moving averages (downtrend), RSI is neutral."
    else:
        signal, confidence = "HOLD", "low"
        thesis = "No strong technical signal — RSI and moving averages are mixed/neutral."
    return {"signal": signal, "action": signal, "confidence": confidence, "thesis": thesis}


# ---------------------------------------------------------------------------
# Core data builder — PER-USER cache (dict keyed by user_id). Two-phase:
# fast synchronous path + background enrichment thread.
# ---------------------------------------------------------------------------
_data_cache: dict[int, dict] = {}
_data_cache_ts: dict[int, float] = {}
_DATA_TTL = 300.0  # 5 min
_bg_running: dict[int, bool] = {}
_bg_lock = threading.Lock()

_INVESTED_MANUAL_TYPES_NOTE = "see accounts.is_invested — replaces the closed-enum type match from the personal instance"


def _investable_total(total_value: float, user_accounts: list) -> float:
    manual_invested = sum(a["balance"] for a in user_accounts if a.get("is_invested"))
    return total_value + manual_invested


def _cash_total(user_accounts: list) -> float:
    """Everything that's neither a liability nor explicitly invested — the
    generic replacement for the personal instance's {"Cash","HYSA"} type match."""
    return sum(a["balance"] for a in user_accounts if not a.get("is_liability") and not a.get("is_invested"))


def _synthetic_nw_history(current: float, months: int = 12) -> list:
    start = current * 0.42
    out = []
    base = datetime.date.today().replace(day=1)
    # walk back `months` months from today
    y, m = base.year, base.month
    dates = []
    for i in range(months):
        mm = m - (months - 1 - i)
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        dates.append(datetime.date(yy, mm, 1))
    for i, d in enumerate(dates):
        t = i / max(months - 1, 1)
        trend = start + (current - start) * (t * t * (3 - 2 * t))
        noise = (math.sin(i * 1.3) + math.sin(i * 0.7)) * 0.018 * trend
        v = trend + noise
        out.append({"date": d.isoformat(), "value": round(v)})
    if out:
        out[-1]["value"] = round(current)
    return out


def _net_worth_history(user_id: int, net_worth: float, total_value: float, acct_list: list) -> list:
    liabilities = sum(abs(a["balance"]) for a in acct_list if a.get("isLiability"))
    investments = total_value
    other_assets = net_worth - investments
    try:
        db.record_snapshot(user_id, net_worth, investments, other_assets, liabilities)
    except Exception:
        pass
    if db.has_real_history(user_id, 2):
        return db.load_nw_history(user_id)
    return _synthetic_nw_history(net_worth, 12)


ASSET_CLASSES = ["US Equity", "International", "Bonds", "Real Assets & Crypto", "Cash"]
_CLASS_OVERRIDE = {
    "VXUS": "International", "VEU": "International", "EFA": "International",
    "VWO": "International", "IEFA": "International", "EEM": "International",
    "BND": "Bonds", "AGG": "Bonds", "BNDX": "Bonds", "TLT": "Bonds",
    "SGOV": "Cash", "BIL": "Cash", "SHV": "Cash", "VMFXX": "Cash",
    "GLD": "Real Assets & Crypto", "SLV": "Real Assets & Crypto",
    "FBTC": "Real Assets & Crypto", "IBIT": "Real Assets & Crypto",
    "VNQ": "Real Assets & Crypto", "SCHH": "Real Assets & Crypto",
}
_CLASS_PICK = {
    "International": ("VXUS", "Total ex-US — broadest international exposure at the lowest fee."),
    "Bonds": ("BND", "Total US bond market — your core stability sleeve."),
    "Real Assets & Crypto": ("VNQ", "REIT exposure to round out the real-assets sleeve."),
    "Cash": ("SGOV", "0-3mo T-bills — yield on cash with near-zero duration risk."),
    "US Equity": ("VTI", "Total US market — low-cost broad equity beta."),
}
_TARGETS = {
    "conservative": {"US Equity": 35, "International": 12, "Bonds": 40, "Real Assets & Crypto": 5, "Cash": 8},
    "moderate":     {"US Equity": 50, "International": 18, "Bonds": 22, "Real Assets & Crypto": 6, "Cash": 4},
    "aggressive":   {"US Equity": 60, "International": 20, "Bonds": 8,  "Real Assets & Crypto": 9, "Cash": 3},
}
_LEVERAGED = {"TQQQ", "UPRO", "SOXL", "TECL", "UDOW", "SPXL", "TNA", "FNGU", "QLD", "SSO"}


def _classify_asset_class(ticker: str, sector: str) -> str:
    t = (ticker or "").upper()
    if t in _CLASS_OVERRIDE:
        return _CLASS_OVERRIDE[t]
    s = (sector or "").lower()
    if any(k in s for k in ("international", "ex-us", "emerging", "developed mkt")):
        return "International"
    if any(k in s for k in ("bond", "fixed income", "treasur", "t-bill", "t-bills")):
        return "Bonds"
    if "cash" in s:
        return "Cash"
    if any(k in s for k in ("crypto", "commodit", "real estate", "reit", "gold", "metals")):
        return "Real Assets & Crypto"
    return "US Equity"


def _advisor_plan(positions: list, raw_profile: dict) -> dict:
    """Rule-based target-allocation advisor — deterministic, no AI."""
    total = sum(p["value"] for p in positions) or 1.0
    risk = (raw_profile.get("risk_tolerance") or "moderate").lower()
    if risk not in _TARGETS:
        risk = "moderate"
    horizon = int(raw_profile.get("horizon_years", 10) or 10)

    current = {c: 0.0 for c in ASSET_CLASSES}
    for p in positions:
        cls = _classify_asset_class(p["ticker"], p.get("sector"))
        current[cls] += p.get("weight", 0.0)

    targets = dict(_TARGETS[risk])
    if horizon < 5:
        take = min(15, targets["US Equity"] - 20)
        targets["US Equity"] -= take
        targets["Bonds"] += take * 0.6
        targets["Cash"] += take * 0.4
    elif horizon < 10:
        take = min(7, targets["US Equity"] - 20)
        targets["US Equity"] -= take
        targets["Bonds"] += take
    tsum = sum(targets.values()) or 1
    targets = {k: v / tsum * 100 for k, v in targets.items()}

    target_rows = []
    for c in ASSET_CLASSES:
        cur = round(current[c], 1)
        tgt = round(targets[c], 1)
        target_rows.append({"category": c, "target": tgt, "current": cur, "gap": round(tgt - cur, 1)})

    actions = []
    held_tickers = {p["ticker"] for p in positions}
    by_gap = sorted(target_rows, key=lambda r: r["gap"], reverse=True)
    prio = 1
    for row in by_gap:
        if row["gap"] > 3 and prio <= 3:
            pick, why = _CLASS_PICK.get(row["category"], (None, ""))
            if pick:
                verb = "Add to" if pick in held_tickers else "Start"
                actions.append({
                    "priority": prio, "action": "buy", "ticker": pick,
                    "desc": f"{verb} {pick} — {row['category']} is {abs(row['gap']):.0f}pts under target ({row['current']:.0f}% vs {row['target']:.0f}%)",
                    "reason": why,
                })
                prio += 1

    for p in sorted(positions, key=lambda x: -x.get("weight", 0)):
        if p.get("weight", 0) > 15 and prio <= 5:
            actions.append({
                "priority": prio, "action": "trim", "ticker": p["ticker"],
                "desc": f"Trim {p['ticker']} — {p['weight']:.0f}% of portfolio, above the 15% single-name limit",
                "reason": "Reduce idiosyncratic concentration risk; redeploy into under-target classes.",
            })
            prio += 1

    for p in positions:
        if p["ticker"] in _LEVERAGED and prio <= 5:
            actions.append({
                "priority": prio, "action": "trim", "ticker": p["ticker"],
                "desc": f"Review {p['ticker']} — leveraged ETF, not buy-and-hold",
                "reason": "Daily-reset leverage decays over time; size carefully and rebalance often.",
            })
            prio += 1
            break

    if not actions:
        actions.append({"priority": 1, "action": "hold", "ticker": "Portfolio",
                         "desc": "Allocation is within ~3pts of every target",
                         "reason": "No rebalancing needed right now — review quarterly."})

    suggested = []
    for row in by_gap:
        if row["gap"] > 3:
            pick, why = _CLASS_PICK.get(row["category"], (None, ""))
            if pick:
                suggested.append({"ticker": pick, "category": row["category"], "weight": round(row["target"], 0), "rationale": why})
        if len(suggested) >= 4:
            break

    risks = []
    top = sorted(positions, key=lambda x: -x.get("weight", 0))[:5]
    if top:
        risks.append(f"Concentration: top 5 holdings = {sum(p['weight'] for p in top):.0f}% of portfolio")
    biggest_under = min(target_rows, key=lambda r: r["gap"]) if target_rows else None
    if biggest_under and biggest_under["gap"] < -5:
        risks.append(f"{biggest_under['category']} is {abs(biggest_under['gap']):.0f}pts under target ({biggest_under['current']:.0f}%)")
    lev = [p["ticker"] for p in positions if p["ticker"] in _LEVERAGED]
    if lev:
        risks.append(f"Leveraged ETFs held ({', '.join(lev)}) — path-dependent decay; not buy-and-hold")
    underwater = [p["ticker"] for p in positions if (p.get("plPct") or 0) < -10]
    if underwater:
        risks.append(f"Underwater positions (>10% loss): {', '.join(underwater[:6])} — review for tax-loss harvesting")
    if not risks:
        risks.append("No major allocation or concentration risks detected.")

    if positions:
        top_str = ", ".join(f"{p['ticker']} ({p['weight']:.0f}%)" for p in top[:4])
        worst = min(target_rows, key=lambda r: r["gap"])
        over = max(target_rows, key=lambda r: r["gap"])
        fit = (
            f"Your {len(positions)}-position portfolio is worth ${total:,.0f}. Largest holdings: {top_str}. "
            f"For a {risk} profile / {horizon}yr horizon, you're most under-target on "
            f"{worst['category']} ({worst['current']:.0f}% vs {worst['target']:.0f}%) and most over on "
            f"{over['category']} ({over['current']:.0f}% vs {over['target']:.0f}%)."
        )
    else:
        fit = "Add holdings to see your allocation fit against your risk profile."

    return {"fit": fit, "targets": target_rows, "actions": actions, "suggested": suggested,
            "risks": risks, "rebalance": "Quarterly, or when any class drifts >5% from target."}


def build_nexus_data(user_id: int, force: bool = False) -> dict:
    now = time.time()
    if not force and user_id in _data_cache and now - _data_cache_ts.get(user_id, 0) < _DATA_TTL:
        with _bg_lock:
            return dict(_data_cache[user_id])

    holdings = db.load_holdings(user_id)
    raw_profile = db.load_profile(user_id)
    user_accounts = db.load_accounts(user_id)
    wl_items = db.load_watchlist(user_id)

    positions, total_value, total_cost = _build_positions_fast(holdings)
    total_pl = total_value - total_cost
    total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0

    acct_list = _build_account_list(positions, user_accounts)
    net_worth = sum(a["balance"] for a in acct_list if not a.get("isLiability"))
    nw_history = _net_worth_history(user_id, net_worth, total_value, acct_list)
    invested_total = _investable_total(total_value, user_accounts)
    coastfire_status = cf.compute(raw_profile, invested_total)

    sector_map: dict[str, float] = {}
    for p in positions:
        s = p["sector"] or "—"
        sector_map[s] = sector_map.get(s, 0.0) + p["weight"]
    sector_weights = [{"sector": k, "weight": round(v, 2)} for k, v in sorted(sector_map.items(), key=lambda x: -x[1])]

    contributions_status = ct.compute(raw_profile)
    rebalance_status = rb.compute(raw_profile, positions)
    tax_loss_candidates = tlh.scan(positions, float(raw_profile.get("tlh_threshold_pct", -10) or -10))
    dividends_status = dv.compute(positions, _div_cache)
    emergency_fund_status = plan.emergency_fund(raw_profile, _cash_total(user_accounts))
    nw_percentile = plan.net_worth_percentile(int(raw_profile.get("age", 30) or 30), net_worth)

    fire_status = fr.compute(raw_profile, invested_total)
    years_available = max(0, int(raw_profile.get("coastfire_retire_age", 65) or 65) - int(raw_profile.get("age", 30) or 30))
    if fire_status.get("enabled"):
        monte_carlo_status = fr.monte_carlo(
            invested_total, float(raw_profile.get("coastfire_monthly_contribution", 0) or 0) * 12.0,
            years_available, fire_status.get("fireNumber", 0),
            mean_return=float(raw_profile.get("coastfire_return_pct", 7.0) or 7.0) / 100.0,
        )
    else:
        monte_carlo_status = {"successPct": None, "trials": 0}
    fire_status["monteCarlo"] = monte_carlo_status

    insights_list = ins.build(
        extra_accounts=[a for a in user_accounts if not any(p["account_id"] == a["id"] for p in positions)],
        contributions=contributions_status, rebalance=rebalance_status,
        tax_loss=tax_loss_candidates, coastfire=coastfire_status,
    )

    raw_name = raw_profile.get("name", "")
    # Plain db call (not the current_user proxy) — this function also runs
    # from background threads with no Flask request context. Also carries
    # totp_enabled for the Settings panel's 2FA section.
    user_row = db.get_user_by_id(user_id)
    if not raw_name:
        raw_name = user_row["email"].split("@")[0] if user_row else "Investor"
    name = raw_name
    initials = "".join(w[0].upper() for w in name.split()[:2]) or "??"
    profile_out = {
        "name": name, "initials": initials, "age": raw_profile.get("age", 35),
        "risk": raw_profile.get("risk_tolerance", "moderate").title(),
        "horizon": raw_profile.get("horizon_years", 10),
        "goals": [g.replace("_", " ").title() for g in raw_profile.get("goals", [])],
        "income_stability": raw_profile.get("income_stability", "stable").title(),
        "emergency_fund": raw_profile.get("emergency_fund", True),
        "notes": raw_profile.get("notes", ""),
        "tlhThresholdPct": raw_profile.get("tlh_threshold_pct", -10.0),
        "totpEnabled": bool(user_row and user_row.get("totp_enabled")),
    }

    wl_out = [{
        "id": w.get("id"), "ticker": w.get("ticker", ""), "price": _pcache.get(w.get("ticker", "")) or 0.0,
        "buyBelow": w.get("buy_below"), "sellAbove": w.get("sell_above"), "note": w.get("note", ""), "change": 0.0,
    } for w in wl_items]

    featured_ticker = positions[0]["ticker"] if positions else None
    featured_price = positions[0]["price"] if positions else 0.0

    result = {
        "profile": profile_out, "positions": positions, "accounts": acct_list,
        "portfolioValue": round(total_value, 2), "totalCost": round(total_cost, 2),
        "totalPL": round(total_pl, 2), "totalPLPct": round(total_pl_pct, 4),
        "netWorth": round(net_worth, 2), "netWorthHistory": nw_history,
        "coastFire": coastfire_status, "fire": fire_status, "contributions": contributions_status,
        "rebalance": rebalance_status, "taxLossHarvest": tax_loss_candidates, "dividends": dividends_status,
        "emergencyFund": emergency_fund_status, "netWorthPercentile": nw_percentile, "insights": insights_list,
        "watchlist": wl_out, "news": [],
        "featured": _placeholder_featured(featured_ticker or "SPY", featured_price),
        "featuredHistory": [], "sectorWeights": sector_weights,
        "advisorPlan": _advisor_plan(positions, raw_profile),
        "riskMetrics": _risk_metrics(user_id), "chatSeed": [],
    }

    _data_cache[user_id] = result
    _data_cache_ts[user_id] = now

    if holdings:
        _start_bg_enrichment(user_id, holdings, featured_ticker, raw_profile)

    return result


def _start_bg_enrichment(user_id: int, holdings, featured_ticker, raw_profile):
    with _bg_lock:
        if _bg_running.get(user_id):
            return
        _bg_running[user_id] = True

    def _run():
        try:
            tickers = [h["ticker"] for h in holdings]
            batch_prices(tickers)
            compute_period_prices(tickers)
            compute_portfolio_risk(user_id, holdings)

            positions, total_value, total_cost = _build_positions_fast(holdings)
            total_pl = total_value - total_cost
            total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0

            user_accounts = db.load_accounts(user_id)
            acct_list = _build_account_list(positions, user_accounts)
            net_worth = sum(a["balance"] for a in acct_list if not a.get("isLiability"))
            nw_history = _net_worth_history(user_id, net_worth, total_value, acct_list)
            invested_total = _investable_total(total_value, user_accounts)
            coastfire_status = cf.compute(raw_profile, invested_total)

            featured_ticker_use = positions[0]["ticker"] if positions else featured_ticker
            featured, featured_history = _placeholder_featured(featured_ticker_use or "SPY", positions[0]["price"] if positions else 0.0), []
            if featured_ticker_use:
                try:
                    fund = get_fundamentals(featured_ticker_use)
                    price = _pcache.get(featured_ticker_use) or (positions[0]["price"] if positions else 0.0)
                    div = get_dividend_info(featured_ticker_use) or {}
                    earnings = get_next_earnings(featured_ticker_use) or {}
                    target = fund.get("target_mean_price") or price
                    upside = ((target - price) / price * 100) if price else 0.0
                    df = fetch_data(featured_ticker_use, period="1y")
                    df = add_indicators(df)
                    snap = latest_snapshot(df)
                    verdict = _rule_based_verdict(snap)
                    featured = {
                        "ticker": featured_ticker_use, "name": fund.get("name", featured_ticker_use),
                        "price": round(float(price), 2), "change": 0.0, "changePct": 0.0,
                        "sector": fund.get("sector", "—"), "industry": fund.get("industry", "—"),
                        "marketCap": fund.get("market_cap", "—"), "pe": fund.get("pe_trailing") or "—",
                        "peFwd": fund.get("pe_forward") or "—", "beta": fund.get("beta") or "—",
                        "high52": fund.get("fifty_two_week_high") or "—", "low52": fund.get("fifty_two_week_low") or "—",
                        "divYield": round((div.get("div_yield") or 0.0) * 100, 2), "annualDiv": div.get("annual_div") or 0.0,
                        "target": round(float(target), 2), "upside": round(upside, 1),
                        "rating": (fund.get("recommendation") or "Hold").title(),
                        "nextEarnings": earnings.get("next_earnings_date", "N/A"),
                        **verdict, "technical": "", "fundamental": "", "newsSummary": "", "risks": [], "catalysts": [], "news": [],
                    }
                    featured_history = _featured_history(featured_ticker_use)
                except Exception:
                    pass

            _prefetch_sectors(tickers)
            sector_map: dict[str, float] = {}
            for p in positions:
                s = _sector_cache.get(p["ticker"], "—")
                p["sector"] = s
                sector_map[s] = sector_map.get(s, 0.0) + p["weight"]
            sector_weights = [{"sector": k, "weight": round(v, 2)} for k, v in sorted(sector_map.items(), key=lambda x: -x[1])]

            _prefetch_dividends(tickers)
            contributions_status = ct.compute(raw_profile)
            rebalance_status = rb.compute(raw_profile, positions)
            tax_loss_candidates = tlh.scan(positions, float(raw_profile.get("tlh_threshold_pct", -10) or -10))
            dividends_status = dv.compute(positions, _div_cache)
            emergency_fund_status = plan.emergency_fund(raw_profile, _cash_total(user_accounts))
            nw_percentile = plan.net_worth_percentile(int(raw_profile.get("age", 30) or 30), net_worth)

            fire_status = fr.compute(raw_profile, invested_total)
            years_available = max(0, int(raw_profile.get("coastfire_retire_age", 65) or 65) - int(raw_profile.get("age", 30) or 30))
            if fire_status.get("enabled"):
                monte_carlo_status = fr.monte_carlo(
                    invested_total, float(raw_profile.get("coastfire_monthly_contribution", 0) or 0) * 12.0,
                    years_available, fire_status.get("fireNumber", 0),
                    mean_return=float(raw_profile.get("coastfire_return_pct", 7.0) or 7.0) / 100.0,
                )
            else:
                monte_carlo_status = {"successPct": None, "trials": 0}
            fire_status["monteCarlo"] = monte_carlo_status

            insights_list = ins.build(
                extra_accounts=[a for a in user_accounts if not any(p["account_id"] == a["id"] for p in positions)],
                contributions=contributions_status, rebalance=rebalance_status,
                tax_loss=tax_loss_candidates, coastfire=coastfire_status,
            )

            with _bg_lock:
                if user_id in _data_cache:
                    _data_cache[user_id].update({
                        "positions": positions, "accounts": acct_list,
                        "portfolioValue": round(total_value, 2), "totalCost": round(total_cost, 2),
                        "totalPL": round(total_pl, 2), "totalPLPct": round(total_pl_pct, 4),
                        "netWorth": round(net_worth, 2), "netWorthHistory": nw_history,
                        "coastFire": coastfire_status, "fire": fire_status, "contributions": contributions_status,
                        "rebalance": rebalance_status, "taxLossHarvest": tax_loss_candidates,
                        "dividends": dividends_status, "emergencyFund": emergency_fund_status,
                        "netWorthPercentile": nw_percentile, "insights": insights_list,
                        "featured": featured, "featuredHistory": featured_history,
                        "sectorWeights": sector_weights, "advisorPlan": _advisor_plan(positions, raw_profile),
                        "riskMetrics": _risk_metrics(user_id),
                    })
                    _data_cache_ts[user_id] = time.time()
            _prune_pcache()
        except Exception as e:
            app.logger.error(f"BG enrichment failed for user {user_id}: {e}", exc_info=True)
        finally:
            with _bg_lock:
                _bg_running[user_id] = False

    threading.Thread(target=_run, daemon=True).start()


def _featured_history(ticker: str) -> list:
    try:
        df = fetch_data(ticker, period="1y")
        df = add_indicators(df)
        out = []
        for ts, row in df.iterrows():
            sma50 = row.get("SMA50")
            sma200 = row.get("SMA200")
            out.append({
                "date": ts.strftime("%Y-%m-%dT%H:%M:%S"), "close": round(float(row["Close"]), 2),
                "sma50": round(float(sma50), 2) if sma50 == sma50 else None,
                "sma200": round(float(sma200), 2) if sma200 == sma200 else None,
            })
        return out
    except Exception:
        return []


def _invalidate(user_id: int) -> None:
    _data_cache_ts[user_id] = 0.0


def _refresh_after_holdings_change(user_id: int, holdings) -> None:
    _invalidate(user_id)

    def _warm():
        try:
            tickers = [h["ticker"] for h in holdings]
            if tickers:
                batch_prices(tickers)
                compute_period_prices(tickers)
                compute_portfolio_risk(user_id, holdings)
                _prefetch_sectors(tickers)
            _invalidate(user_id)
            build_nexus_data(user_id, force=True)
        except Exception:
            pass
    threading.Thread(target=_warm, daemon=True).start()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


def _absolute_url(path: str) -> str:
    """Build an absolute link for emails. Falls back to the live request's
    own host if APP_BASE_URL isn't set (e.g. local dev)."""
    base = APP_BASE_URL or request.host_url.rstrip("/")
    return f"{base}{path}"


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def signup_page():
    if request.method == "GET":
        return send_from_directory(DESIGN_DIR, "signup.html")
    body = request.get_json(force=True) if request.is_json else request.form
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or "@" not in email or len(password) < 8:
        return jsonify({"ok": False, "error": "Valid email + password (8+ chars) required."}), 400
    user_id = db.create_user(email, password)
    if user_id is None:
        return jsonify({"ok": False, "error": "That email is already registered."}), 409
    token = db.create_auth_token(user_id, "verify_email", ttl_hours=24)
    mail.send_verification_email(email, _absolute_url(f"/verify-email/{token}"))
    # Deliberately NOT logging in yet — email verification is required
    # before the dashboard is reachable (per confirmed decision).
    return jsonify({"ok": True, "needsVerification": True})


@app.route("/verify-email/<token>")
def verify_email_page(token):
    user_id = db.consume_auth_token(token, "verify_email")
    if user_id is None:
        return redirect("/login?verify=expired")
    db.mark_email_verified(user_id)
    login_user(auth.User.get(user_id))
    return redirect("/")


@app.route("/resend-verification", methods=["POST"])
@limiter.limit("5 per minute")
def resend_verification():
    """An unverified user has no other path forward — same
    doesn't-leak-existence response shape as forgot-password."""
    body = request.get_json(force=True) if request.is_json else request.form
    email = (body.get("email") or "").strip()
    row = db.get_user_by_email(email) if email else None
    if row and not row["email_verified"]:
        token = db.create_auth_token(row["id"], "verify_email", ttl_hours=24)
        mail.send_verification_email(row["email"], _absolute_url(f"/verify-email/{token}"))
    return jsonify({"ok": True, "message": "If that email needs verifying, a new link is on its way."})


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login_page():
    if request.method == "GET":
        return send_from_directory(DESIGN_DIR, "login.html")
    body = request.get_json(force=True) if request.is_json else request.form
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    user, row = auth.User.authenticate(email, password)
    if row and db.is_locked(row):
        return jsonify({"ok": False, "error": "Too many failed attempts — try again in a few minutes."}), 429
    if not user:
        if row:
            db.record_failed_login(row["id"])
        return jsonify({"ok": False, "error": "Invalid email or password."}), 401
    if not user.email_verified:
        return jsonify({"ok": False, "error": "Please verify your email first — check your inbox for the link.",
                         "needsVerification": True}), 403
    db.reset_failed_login(user.id)
    if user.totp_enabled:
        session["pending_2fa_user_id"] = user.id
        return jsonify({"ok": True, "needs2fa": True})
    login_user(user)
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
@login_required
def logout_page():
    logout_user()
    session.pop("pending_2fa_user_id", None)
    return jsonify({"ok": True})


@app.route("/2fa/verify", methods=["POST"])
@limiter.limit("10 per minute")
def api_2fa_verify():
    user_id = session.get("pending_2fa_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "No pending login — sign in again."}), 400
    row = db.get_user_by_id(user_id)
    if not row:
        session.pop("pending_2fa_user_id", None)
        return jsonify({"ok": False, "error": "Session expired — sign in again."}), 400
    if db.is_locked(row):
        return jsonify({"ok": False, "error": "Too many failed attempts — try again in a few minutes."}), 429
    body = request.get_json(force=True) or {}
    code = (body.get("code") or "").strip()
    use_backup = bool(body.get("backup"))
    ok = db.consume_backup_code(user_id, code) if use_backup else totp_lib.verify_code(row["totp_secret"], code)
    if not ok:
        db.record_failed_login(user_id)
        return jsonify({"ok": False, "error": "Invalid code."}), 401
    db.reset_failed_login(user_id)
    session.pop("pending_2fa_user_id", None)
    login_user(auth.User.get(user_id))
    return jsonify({"ok": True})


@app.route("/2fa/setup")
@login_required
def api_2fa_setup():
    """Generates (or regenerates) a pending secret — not enabled until
    /2fa/enable confirms a code, so an abandoned setup never half-protects
    the account."""
    secret = totp_lib.generate_secret()
    db.set_totp_secret(current_user.id, secret)
    uri = totp_lib.totp_uri(secret, current_user.email)
    return jsonify({"ok": True, "qr": totp_lib.qr_data_uri(uri), "secret": secret})


@app.route("/2fa/enable", methods=["POST"])
@login_required
def api_2fa_enable():
    row = db.get_user_by_id(current_user.id)
    secret = row.get("totp_secret")
    body = request.get_json(force=True) or {}
    code = (body.get("code") or "").strip()
    if not secret or not totp_lib.verify_code(secret, code):
        return jsonify({"ok": False, "error": "Invalid code — check your authenticator app and try again."}), 400
    db.set_totp_enabled(current_user.id, True)
    codes = totp_lib.generate_backup_codes()
    db.store_backup_codes(current_user.id, [generate_password_hash(c) for c in codes])
    _invalidate(current_user.id)  # profile.totpEnabled is cached in _data_cache — must refresh
    return jsonify({"ok": True, "backupCodes": codes})


@app.route("/2fa/disable", methods=["POST"])
@login_required
def api_2fa_disable():
    body = request.get_json(force=True) or {}
    password = body.get("password") or ""
    row = db.get_user_by_id(current_user.id)
    if not db.verify_password(row, password):
        return jsonify({"ok": False, "error": "Incorrect password."}), 401
    db.set_totp_enabled(current_user.id, False)
    _invalidate(current_user.id)
    return jsonify({"ok": True})


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def forgot_password_page():
    if request.method == "GET":
        return send_from_directory(DESIGN_DIR, "forgot-password.html")
    body = request.get_json(force=True) if request.is_json else request.form
    email = (body.get("email") or "").strip()
    row = db.get_user_by_email(email) if email else None
    if row:
        token = db.create_auth_token(row["id"], "reset_password", ttl_hours=1)
        mail.send_password_reset_email(row["email"], _absolute_url(f"/reset-password/{token}"))
    # Same response whether or not the email exists — avoids leaking which
    # emails have accounts (account enumeration).
    return jsonify({"ok": True, "message": "If that email has an account, a reset link is on its way."})


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def reset_password_page(token):
    if request.method == "GET":
        return send_from_directory(DESIGN_DIR, "reset-password.html")
    body = request.get_json(force=True) if request.is_json else request.form
    password = body.get("password") or ""
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters."}), 400
    user_id = db.consume_auth_token(token, "reset_password")
    if user_id is None:
        return jsonify({"ok": False, "error": "This reset link is invalid or expired — request a new one."}), 400
    db.set_password(user_id, password)
    db.reset_failed_login(user_id)
    login_user(auth.User.get(user_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    return send_from_directory(DESIGN_DIR, "index.html")


@app.route("/data.js")
@login_required
def data_js():
    try:
        data = build_nexus_data(current_user.id)
    except Exception as e:
        app.logger.error(f"build_nexus_data failed: {e}", exc_info=True)
        return Response("window.NEXUS_DATA = {positions:[],accounts:[],watchlist:[]};", mimetype="application/javascript")

    js_payload = json.dumps(data, default=str)
    js = f"""// NexusAI Cloud — dynamic data from server.py
window.NEXUS_DATA = (function() {{
  const raw = {js_payload};
  if (raw.netWorthHistory) raw.netWorthHistory = raw.netWorthHistory.map(d => ({{...d, date: new Date(d.date)}}));
  if (raw.featuredHistory) raw.featuredHistory = raw.featuredHistory.map(d => ({{...d, date: new Date(d.date)}}));
  if (!raw.chatSeed) raw.chatSeed = [];
  if (!raw.news) raw.news = [];
  if (!raw.watchlist) raw.watchlist = [];
  return raw;
}})();

window.fmt$ = (n, opts = {{}}) => {{
  if (n == null || isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (opts.compact && abs >= 1000000) return (n >= 0 ? "$" : "-$") + (abs/1000000).toFixed(2) + "M";
  if (opts.compact && abs >= 10000)   return (n >= 0 ? "$" : "-$") + (abs/1000).toFixed(1) + "K";
  const sign = n < 0 ? "-" : (opts.signed ? "+" : "");
  return sign + "$" + abs.toLocaleString("en-US", {{minimumFractionDigits: opts.dec ?? 2, maximumFractionDigits: opts.dec ?? 2}});
}};
window.fmtPct = (n, signed = true) => {{
  if (n == null || isNaN(n)) return "—";
  const sign = n > 0 && signed ? "+" : "";
  return sign + n.toFixed(2) + "%";
}};
window.fmtNum = (n, dec = 2) => {{
  if (n == null || isNaN(n)) return "—";
  return n.toLocaleString("en-US", {{minimumFractionDigits: dec, maximumFractionDigits: dec}});
}};
"""
    return Response(js, mimetype="application/javascript")


_TICKER_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")


@app.route("/api/analyze/<ticker>")
@login_required
def api_analyze(ticker: str):
    ticker = ticker.strip().upper()
    if not _TICKER_RE.match(ticker):
        return jsonify({"ok": False, "error": "Invalid ticker format"}), 400
    try:
        df = fetch_data(ticker, period="1y")
        df = add_indicators(df)
        snap = latest_snapshot(df)
        fund = get_fundamentals(ticker)
        price = single_price(ticker) or snap.get("close") or 0.0
        div = get_dividend_info(ticker) or {}
        earnings = get_next_earnings(ticker) or {}
        target = fund.get("target_mean_price") or price
        upside = ((target - price) / price * 100) if price else 0.0
        verdict = _rule_based_verdict(snap)
        featured = {
            "ticker": ticker, "name": fund.get("name", ticker), "price": round(float(price), 2),
            "change": 0.0, "changePct": 0.0, "sector": fund.get("sector", "—"), "industry": fund.get("industry", "—"),
            "marketCap": fund.get("market_cap", "—"), "pe": fund.get("pe_trailing") or "—",
            "peFwd": fund.get("pe_forward") or "—", "beta": fund.get("beta") or "—",
            "high52": fund.get("fifty_two_week_high") or "—", "low52": fund.get("fifty_two_week_low") or "—",
            "divYield": round((div.get("div_yield") or 0.0) * 100, 2), "annualDiv": div.get("annual_div") or 0.0,
            "target": round(float(target), 2), "upside": round(upside, 1),
            "rating": (fund.get("recommendation") or "Hold").title(),
            "nextEarnings": earnings.get("next_earnings_date", "N/A"),
            **verdict, "technical": "", "fundamental": "", "newsSummary": "", "risks": [], "catalysts": [], "news": [],
        }
        history = _featured_history(ticker)
        return jsonify({"featured": featured, "featuredHistory": history, "ok": True})
    except DataError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/accounts", methods=["POST"])
@login_required
def api_save_account():
    """Insert or update ONE account (id present = update)."""
    body = request.get_json(force=True) or {}
    row = db.save_account(current_user.id, body)
    _invalidate(current_user.id)
    return jsonify({"ok": True, "account": row})


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
@login_required
def api_delete_account(account_id: int):
    db.delete_account(current_user.id, account_id)
    _invalidate(current_user.id)
    return jsonify({"ok": True})


@app.route("/api/holdings", methods=["POST"])
@login_required
def api_save_holding_route():
    """Insert or update ONE holding (id present = update)."""
    body = request.get_json(force=True) or {}
    try:
        row = db.save_holding(current_user.id, body)
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": f"Invalid holding: {e}"}), 400
    _refresh_after_holdings_change(current_user.id, db.load_holdings(current_user.id))
    return jsonify({"ok": True, "holding": row})


@app.route("/api/holdings/<int:holding_id>", methods=["DELETE"])
@login_required
def api_delete_holding(holding_id: int):
    db.delete_holding(current_user.id, holding_id)
    _refresh_after_holdings_change(current_user.id, db.load_holdings(current_user.id))
    return jsonify({"ok": True})


@app.route("/api/portfolio/import", methods=["POST"])
@login_required
def api_import_portfolio():
    """CSV import: columns ticker, shares, avg_cost, account_name (any brokerage export
    reformatted to this generic shape — no Fidelity/Webull-specific column sniffing)."""
    MAX_BYTES = 2 * 1024 * 1024
    raw = None
    if request.files.get("file"):
        raw = request.files["file"].read(MAX_BYTES + 1)
    elif request.data:
        raw = request.data[:MAX_BYTES + 1]
    if not raw:
        return jsonify({"ok": False, "error": "No CSV provided"}), 400
    if len(raw) > MAX_BYTES:
        return jsonify({"ok": False, "error": "CSV exceeds 2MB limit"}), 400
    try:
        raw_rows = pf.from_csv_raw(bytes(raw))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not parse CSV: {e}"}), 400

    if not raw_rows:
        return jsonify({"ok": False, "error": "CSV has no rows."}), 400
    missing_cols = {"ticker", "shares"} - set(raw_rows[0].keys())
    if missing_cols:
        return jsonify({"ok": False, "error": (
            f"CSV is missing column(s): {', '.join(sorted(missing_cols))}. "
            "Expected headers: ticker, shares, avg_cost, account_name."
        )}), 400

    resolved = []
    for r in raw_rows:
        account_name = str(r.get("account_name") or r.get("account") or "").strip()
        r["account_id"] = db.resolve_account_by_name(current_user.id, account_name)
        resolved.append(r)
    clean = pf._coerce(resolved)

    # Explain what got dropped instead of a bare count — a friend's first
    # CSV import failing silently is the #1 reason they'd give up on this.
    skipped_reasons = []
    for r in resolved:
        ticker = str(r.get("ticker", "")).strip()
        if not ticker:
            skipped_reasons.append("a row with no ticker")
            continue
        try:
            shares = float(r.get("shares", 0) or 0)
        except (TypeError, ValueError):
            skipped_reasons.append(f"{ticker}: shares isn't a number")
            continue
        if shares <= 0:
            skipped_reasons.append(f"{ticker}: shares must be greater than 0")

    if not clean:
        return jsonify({"ok": False, "error": "No valid rows found. " + (
            "; ".join(skipped_reasons[:5]) if skipped_reasons else
            "Check that ticker/shares/avg_cost/account_name columns are filled in."
        )}), 400
    for h in clean:
        db.save_holding(current_user.id, h)
    _refresh_after_holdings_change(current_user.id, db.load_holdings(current_user.id))
    resp = {"ok": True, "count": len(clean), "dropped": len(raw_rows) - len(clean)}
    if skipped_reasons:
        resp["skipped"] = skipped_reasons[:5]
    return jsonify(resp)


@app.route("/api/snapshot-now", methods=["POST"])
@login_required
def api_snapshot_now():
    _invalidate(current_user.id)
    try:
        data = build_nexus_data(current_user.id, force=True)
        return jsonify({"ok": True, "value": data["netWorth"], "points": len(data["netWorthHistory"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    _invalidate(current_user.id)
    return jsonify({"ok": True})


@app.route("/api/watchlist", methods=["POST"])
@login_required
def api_save_watchlist_item():
    body = request.get_json(force=True) or {}
    row = db.save_watchlist_item(current_user.id, {
        "id": body.get("id"), "ticker": body.get("ticker", ""),
        "buy_below": body.get("buyBelow"), "sell_above": body.get("sellAbove"), "note": body.get("note", ""),
    })
    _invalidate(current_user.id)
    return jsonify({"ok": True, "item": row})


@app.route("/api/watchlist/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_watchlist_item(item_id: int):
    db.delete_watchlist_item(current_user.id, item_id)
    _invalidate(current_user.id)
    return jsonify({"ok": True})


@app.route("/api/profile", methods=["POST"])
@login_required
def api_save_profile():
    body = request.get_json(force=True) or {}
    existing = db.load_profile(current_user.id)
    if body.get("name") is not None:
        existing["name"] = str(body["name"]).strip()
    if body.get("risk_tolerance"):
        existing["risk_tolerance"] = str(body["risk_tolerance"]).strip().lower()
    if body.get("horizon_years"):
        try:
            existing["horizon_years"] = int(body["horizon_years"])
        except (TypeError, ValueError):
            pass
    if body.get("age"):
        try:
            existing["age"] = int(body["age"])
        except (TypeError, ValueError):
            pass
    if body.get("notes") is not None:
        existing["notes"] = str(body["notes"]).strip()
    if body.get("coastfire_retire_age"):
        try:
            existing["coastfire_retire_age"] = int(body["coastfire_retire_age"])
        except (TypeError, ValueError):
            pass
    for field, _lo, _hi in pr._FLOAT_FIELDS:
        if body.get(field) is not None:
            try:
                existing[field] = float(body[field])
            except (TypeError, ValueError):
                pass
    db.save_profile(current_user.id, existing)
    _invalidate(current_user.id)
    return jsonify({"ok": True})


@app.route("/api/export/gains")
@login_required
def api_export_gains():
    data = build_nexus_data(current_user.id)
    lines = ["ticker,account_id,shares,avg_cost,price,cost_basis,market_value,unrealized_pl,unrealized_pl_pct"]
    for p in data["positions"]:
        lines.append(
            f'{p["ticker"]},{p.get("account_id","")},{p["shares"]},{p["avg_cost"]},'
            f'{p["price"]},{round(p["cost"],2)},{round(p["value"],2)},{round(p["pl"],2)},{round(p["plPct"],2)}'
        )
    csv_text = "\n".join(lines) + "\n"
    return Response(csv_text, mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=nexusai_unrealized_gains.csv"})


@app.route("/api/snapshot")
@login_required
def api_snapshot():
    try:
        data = build_nexus_data(current_user.id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True, "positions": data["positions"], "portfolioValue": data["portfolioValue"],
        "totalCost": data["totalCost"], "totalPL": data["totalPL"], "totalPLPct": data["totalPLPct"],
        "netWorth": data["netWorth"], "accounts": data["accounts"], "watchlist": data["watchlist"],
        "sectorWeights": data["sectorWeights"], "advisorPlan": data["advisorPlan"], "riskMetrics": data["riskMetrics"],
        "coastFire": data["coastFire"], "fire": data["fire"], "contributions": data["contributions"],
        "rebalance": data["rebalance"], "taxLossHarvest": data["taxLossHarvest"], "dividends": data["dividends"],
        "emergencyFund": data["emergencyFund"], "netWorthPercentile": data["netWorthPercentile"], "insights": data["insights"],
    })


@app.route("/<path:filename>")
def static_files(filename: str):
    return send_from_directory(DESIGN_DIR, filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"\n  NexusAI Cloud →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_ENV") == "development")
