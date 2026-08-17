"""Watchlist validation: price targets and notes per ticker.

No file I/O — db.py persists this per user_id in Postgres.
"""


def _coerce_item(item):
    ticker = str(item.get("ticker", "")).strip().upper()
    if not ticker:
        return None
    buy_below = item.get("buy_below")
    sell_above = item.get("sell_above")
    out = {
        "ticker": ticker,
        "buy_below": float(buy_below) if buy_below not in (None, 0, 0.0, "") else None,
        "sell_above": float(sell_above) if sell_above not in (None, 0, 0.0, "") else None,
        "note": str(item.get("note", "")).strip(),
    }
    if "id" in item:
        out["id"] = item["id"]
    return out


def coerce_list(items):
    clean = [_coerce_item(i) for i in (items or [])]
    return [i for i in clean if i is not None]


def check_alerts(items, prices):
    """Check price targets against current prices.

    prices: dict of {ticker: float}
    Returns list of {ticker, type, message}.
    """
    alerts = []
    for item in (items or []):
        ticker = item.get("ticker")
        price = prices.get(ticker)
        if price is None:
            continue
        buy_below = item.get("buy_below")
        sell_above = item.get("sell_above")
        if buy_below is not None and price <= buy_below:
            alerts.append({
                "ticker": ticker, "type": "buy",
                "message": f"🔴 **{ticker}** at ${price:.2f} — at or below your buy target of ${buy_below:.2f}",
            })
        if sell_above is not None and price >= sell_above:
            alerts.append({
                "ticker": ticker, "type": "sell",
                "message": f"🟢 **{ticker}** at ${price:.2f} — at or above your sell target of ${sell_above:.2f}",
            })
    return alerts
