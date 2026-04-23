from __future__ import annotations

import logging
from functools import lru_cache

import yfinance as yf


logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _ticker_candidates(symbol: str) -> list[str]:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return []

    if not normalized.endswith(".JK"):
        return [f"{normalized}.JK", normalized]
    return [normalized]


def _coerce_number(value):
    if value in (None, ""):
        return None

    try:
        if isinstance(value, str):
            value = value.replace(",", "")
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number.is_integer():
        return int(number)
    return number


def _extract_market_snapshot(ticker: yf.Ticker) -> dict:
    ticker_symbol = _normalize_symbol(getattr(ticker, "ticker", "") or getattr(ticker, "symbol", ""))

    fast_info = {}
    try:
        fast_info = dict(getattr(ticker, "fast_info", {}) or {})
    except Exception:
        fast_info = {}

    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    history_price = None
    try:
        history = ticker.history(period="5d", interval="1d", auto_adjust=False, actions=False)
        if not history.empty and "Close" in history:
            closes = history["Close"].dropna()
            if not closes.empty:
                history_price = _coerce_number(closes.iloc[-1])
    except Exception as exc:
        logger.info("Market history lookup failed for %s: %s", ticker_symbol or "unknown", exc)

    info_price = _coerce_number(
        fast_info.get("last_price")
        or fast_info.get("regular_market_price")
        or info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )
    price = history_price if history_price is not None else info_price
    shares_outstanding = _coerce_number(
        info.get("sharesOutstanding")
        or info.get("impliedSharesOutstanding")
        or fast_info.get("shares_outstanding")
    )
    market_cap = _coerce_number(
        info.get("marketCap")
        or fast_info.get("market_cap")
    )

    if market_cap in (None, "") and price is not None and shares_outstanding is not None:
        market_cap = price * shares_outstanding

    logger.info(
        "Market snapshot resolved ticker=%s history_price=%s info_price=%s shares_outstanding=%s market_cap=%s",
        ticker_symbol or "unknown",
        history_price,
        info_price,
        shares_outstanding,
        market_cap,
    )

    return {
        "price": price,
        "shares_outstanding": shares_outstanding,
        "market_cap": market_cap,
    }


@lru_cache(maxsize=128)
def fetch_market_snapshot(symbol: str) -> dict:
    for ticker_symbol in _ticker_candidates(symbol):
        try:
            logger.info("Market lookup started for symbol=%s candidate=%s", symbol, ticker_symbol)
            ticker = yf.Ticker(ticker_symbol)
            snapshot = _extract_market_snapshot(ticker)
            if any(value not in (None, "") for value in snapshot.values()):
                logger.info("Market lookup succeeded for symbol=%s candidate=%s", symbol, ticker_symbol)
                return snapshot
            logger.info("Market lookup produced empty snapshot for symbol=%s candidate=%s", symbol, ticker_symbol)
        except Exception:
            logger.exception("Market lookup failed for symbol=%s candidate=%s", symbol, ticker_symbol)
            continue

    logger.info("Market lookup exhausted for symbol=%s with no usable result", symbol)
    return {
        "price": None,
        "shares_outstanding": None,
        "market_cap": None,
    }