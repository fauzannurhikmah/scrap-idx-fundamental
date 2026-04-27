from contextlib import contextmanager
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2.extras import Json
from psycopg2 import sql

from config.settings import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_TABLE,
    POSTGRES_URL,
    POSTGRES_USER,
)


def _build_dsn() -> str:
    if POSTGRES_URL:
        try:
            parsed = urlsplit(POSTGRES_URL)
            query_items = parse_qsl(parsed.query, keep_blank_values=True)
            filtered_items = []
            for key, value in query_items:
                if key.lower() == "schema":
                    continue
                filtered_items.append((key, value))

            cleaned_query = urlencode(filtered_items)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, cleaned_query, parsed.fragment))
        except Exception:
            return POSTGRES_URL
    return (
        f"host={POSTGRES_HOST} "
        f"port={POSTGRES_PORT} "
        f"dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} "
        f"password={POSTGRES_PASSWORD}"
    )


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _normalize_period(period: str) -> str:
    return (period or "").strip().upper()


LOCAL_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
LOCAL_CACHE_FILE = LOCAL_CACHE_DIR / "fundamental_results.json"


def _cache_key(symbol: str, year: int, period: str) -> str:
    return f"{_normalize_symbol(symbol)}|{int(year)}|{_normalize_period(period) or 'AUDIT'}"


def _load_local_cache() -> dict:
    try:
        if not LOCAL_CACHE_FILE.exists():
            return {}
        with LOCAL_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_local_cache(cache_data: dict) -> None:
    LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCAL_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False)


def _get_local_cached_payload(symbol: str, year: int, quarter: str) -> dict | None:
    cache_data = _load_local_cache()
    return cache_data.get(_cache_key(symbol, year, quarter))


def _save_local_cached_payload(payload: dict) -> None:
    meta = payload.get("meta") or {}
    symbol = _normalize_symbol(meta.get("kode_emiten"))
    year = meta.get("tahun")
    period = _normalize_period(meta.get("periode")) or "AUDIT"

    if not symbol or year in (None, ""):
        return

    try:
        year = int(year)
    except (TypeError, ValueError):
        return

    cache_data = _load_local_cache()
    cache_data[_cache_key(symbol, year, period)] = payload
    _save_local_cache(cache_data)


@contextmanager
def _get_conn():
    conn = psycopg2.connect(_build_dsn())
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        id BIGSERIAL PRIMARY KEY,
                        kode_emiten TEXT,
                        tahun INTEGER,
                        periode TEXT,
                        meta JSONB NOT NULL,
                        financials JSONB NOT NULL,
                        market JSONB NOT NULL,
                        ratios JSONB NOT NULL,
                        growth JSONB NOT NULL,
                        raw_flags JSONB NOT NULL,
                        ai_summary TEXT,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                ).format(sql.Identifier(POSTGRES_TABLE))
            )

            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS financials JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS market JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ratios JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS growth JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS raw_flags JSONB NOT NULL DEFAULT '{}'::jsonb;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ai_summary TEXT;"
                ).format(sql.Identifier(POSTGRES_TABLE))
            )
            cursor.execute(
                sql.SQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (kode_emiten, tahun, periode);"
                ).format(
                    sql.Identifier(f"{POSTGRES_TABLE}_key"),
                    sql.Identifier(POSTGRES_TABLE),
                )
            )
        conn.commit()


def get_fundamental_result(symbol: str, year: int, quarter: str | None = None):
    normalized_symbol = _normalize_symbol(symbol)
    normalized_quarter = _normalize_period(quarter) or "AUDIT"

    row = None
    try:
        with _get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT payload, meta, financials, market, ratios, growth, raw_flags, ai_summary
                        FROM {}
                        WHERE kode_emiten = %s AND tahun = %s AND periode = %s
                        LIMIT 1;
                        """
                    ).format(sql.Identifier(POSTGRES_TABLE)),
                    (normalized_symbol, year, normalized_quarter),
                )
                row = cursor.fetchone()
    except Exception:
        row = None

    if not row:
        return _get_local_cached_payload(normalized_symbol, year, normalized_quarter)

    payload, meta, financials, market, ratios, growth, raw_flags, ai_summary = row
    if payload:
        return payload

    return {
        "meta": meta or {},
        "financials": financials or {},
        "market": market or {},
        "ratios": ratios or {},
        "growth": growth or {},
        "raw_flags": raw_flags or {},
        "ai_summary": ai_summary or "",
    }


def save_fundamental_result(payload: dict) -> None:
    meta = payload.get("meta") or {}
    financials = payload.get("financials") or {}
    market = payload.get("market") or {}
    ratios = payload.get("ratios") or {}
    growth = payload.get("growth") or {}
    raw_flags = payload.get("raw_flags") or {}
    ai_summary = payload.get("ai_summary")

    kode_emiten = _normalize_symbol(meta.get("kode_emiten"))
    periode = _normalize_period(meta.get("periode"))

    try:
        with _get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            kode_emiten, tahun, periode, meta, financials, market, ratios, growth, raw_flags, ai_summary, payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (kode_emiten, tahun, periode)
                        DO UPDATE SET
                            meta = EXCLUDED.meta,
                            financials = EXCLUDED.financials,
                            market = EXCLUDED.market,
                            ratios = EXCLUDED.ratios,
                            growth = EXCLUDED.growth,
                            raw_flags = EXCLUDED.raw_flags,
                            ai_summary = EXCLUDED.ai_summary,
                            payload = EXCLUDED.payload;
                        """
                    ).format(sql.Identifier(POSTGRES_TABLE)),
                    (
                        kode_emiten,
                        meta.get("tahun"),
                        periode,
                        Json(meta),
                        Json(financials),
                        Json(market),
                        Json(ratios),
                        Json(growth),
                        Json(raw_flags),
                        ai_summary,
                        Json(payload),
                    ),
                )
            conn.commit()
    except Exception:
        pass

    _save_local_cached_payload(payload)
