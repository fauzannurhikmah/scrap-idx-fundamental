from __future__ import annotations

from tradingview_ta import Interval, TA_Handler


def _normalize_emiten(emiten: str) -> str:
    cleaned = (emiten or "").strip().upper()
    if not cleaned:
        return ""

    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]

    return cleaned


def fetch_technical_analysis(emiten: str) -> dict:
    symbol = _normalize_emiten(emiten)
    if not symbol:
        raise ValueError("Invalid emiten/symbol.")

    handler = TA_Handler(
        symbol=symbol,
        screener="indonesia",
        exchange="IDX",
        interval=Interval.INTERVAL_1_DAY,
    )

    analysis = handler.get_analysis()

    return {
        "symbol": symbol,
        "exchange": "IDX",
        "screener": "indonesia",
        "interval": "1d",
        "summary": analysis.summary,
        "oscillators": analysis.oscillators,
        "moving_averages": analysis.moving_averages,
        "indicators": analysis.indicators,
    }
