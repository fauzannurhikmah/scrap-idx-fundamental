from contextlib import contextmanager

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

    if not row:
        return None

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
