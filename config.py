"""Central configuration: API keys, indicator params, thresholds.

No LLM/chat config here — NexusAI Cloud (this multi-tenant deploy) is
rule-based-fallback only for v1, no shared Anthropic API key. See the
personal single-user instance's config.py if AI chat config is needed.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()  # load .env from cwd if present
except Exception:  # python-dotenv not installed — env vars still work
    pass


# --- API keys --------------------------------------------------------------
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
HAS_FINNHUB = bool(FINNHUB_API_KEY)


# --- Indicator params ------------------------------------------------------
SMA_FAST = 50
SMA_SLOW = 200
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLLINGER_WINDOW = 20
BOLLINGER_STD = 2.0
VOLUME_AVG_WINDOW = 20
SR_LOOKBACK = 60  # bars for support/resistance


# --- Advisory thresholds ---------------------------------------------------
RSI_BUY_BELOW = 45.0
RSI_SELL_ABOVE = 70.0


# --- Portfolio -------------------------------------------------------------
# Flag a position if it exceeds this share of total portfolio value.
CONCENTRATION_THRESHOLD = float(os.getenv("CONCENTRATION_THRESHOLD", "0.25"))

# Rule-based fallback trim suggestion when overbought with a large gain.
TRIM_GAIN_THRESHOLD = 0.25  # +25% unrealized
TRIM_DEFAULT_PCT = 25  # trim 25% of the position


# --- Caching ---------------------------------------------------------------
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))  # 15 min
