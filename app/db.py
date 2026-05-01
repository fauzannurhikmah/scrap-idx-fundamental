import logging

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker

from config.settings import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_TABLE,
    POSTGRES_URL,
    POSTGRES_USER,
)


logger = logging.getLogger(__name__)

Base = declarative_base()
_engine = None
_session_factory = None
_schema_initialized = False


def _build_database_url():
    if POSTGRES_URL:
        try:
            parsed = make_url(POSTGRES_URL)
            if parsed.query and "schema" in parsed.query:
                query = dict(parsed.query)
                query.pop("schema", None)
                parsed = parsed.set(query=query)
            return parsed
        except Exception:
            return POSTGRES_URL
    return URL.create(
        "postgresql+psycopg2",
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
    )


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _normalize_period(period: str) -> str:
    return (period or "").strip().upper()


class FundamentalResult(Base):
    __tablename__ = POSTGRES_TABLE
    __table_args__ = (UniqueConstraint("kode_emiten", "tahun", "periode", name=f"{POSTGRES_TABLE}_key"),)

    kode_emiten = Column(Text, primary_key=True, index=True)
    tahun = Column(Integer, primary_key=True, index=True)
    periode = Column(Text, primary_key=True, index=True)
    meta = Column(JSONB, nullable=False, default=dict)
    financials = Column(JSONB, nullable=False, default=dict)
    market = Column(JSONB, nullable=False, default=dict)
    ratios = Column(JSONB, nullable=False, default=dict)
    growth = Column(JSONB, nullable=False, default=dict)
    raw_flags = Column(JSONB, nullable=False, default=dict)
    shareholder = Column(JSONB, nullable=False, default=dict)
    ai_summary = Column(Text, nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _get_engine():
    global _engine, _session_factory

    if _engine is None:
        database_url = _build_database_url()
        _engine = create_engine(database_url, pool_pre_ping=True, future=True)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)

    return _engine


def _get_session_factory():
    if _session_factory is None:
        _get_engine()
    return _session_factory


def _ensure_schema() -> None:
    global _schema_initialized

    if _schema_initialized:
        return

    engine = _get_engine()
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    if POSTGRES_TABLE not in existing_tables:
        _schema_initialized = True
        return

    existing_columns = {column["name"] for column in inspector.get_columns(POSTGRES_TABLE)}
    required_columns = {
        "kode_emiten": "TEXT",
        "tahun": "INTEGER",
        "periode": "TEXT",
        "meta": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "financials": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "market": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ratios": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "growth": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "raw_flags": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "shareholder": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ai_summary": "TEXT",
        "payload": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    }

    with engine.begin() as conn:
        for column_name, ddl in required_columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    text(
                        f'ALTER TABLE "{POSTGRES_TABLE}" ADD COLUMN IF NOT EXISTS "{column_name}" {ddl}'
                    )
                )

        try:
            conn.execute(
                text(
                    f'CREATE UNIQUE INDEX IF NOT EXISTS "{POSTGRES_TABLE}_key" ON "{POSTGRES_TABLE}" (kode_emiten, tahun, periode)'
                )
            )
        except Exception:
            logger.exception("Failed to ensure unique index for %s", POSTGRES_TABLE)

    _schema_initialized = True


def init_db() -> None:
    _ensure_schema()


def get_fundamental_result(symbol: str, year: int, quarter: str | None = None):
    normalized_symbol = _normalize_symbol(symbol)
    normalized_quarter = _normalize_period(quarter) or "AUDIT"

    try:
        _ensure_schema()
        session_factory = _get_session_factory()
        with session_factory() as session:
            row = session.execute(
                select(
                    FundamentalResult.payload,
                    FundamentalResult.meta,
                    FundamentalResult.financials,
                    FundamentalResult.market,
                    FundamentalResult.ratios,
                    FundamentalResult.growth,
                    FundamentalResult.raw_flags,
                    FundamentalResult.shareholder,
                    FundamentalResult.ai_summary,
                ).where(
                    FundamentalResult.kode_emiten == normalized_symbol,
                    FundamentalResult.tahun == year,
                    FundamentalResult.periode == normalized_quarter,
                )
            ).first()
    except Exception:
        logger.exception(
            "Failed to load fundamental result from database: %s %s %s",
            normalized_symbol,
            year,
            normalized_quarter,
        )
        return None

    if not row:
        return None

    payload, meta, financials, market, ratios, growth, raw_flags, shareholder, ai_summary = row
    if payload:
        logger.info(
            "Loaded fundamental result from database: %s %s %s",
            normalized_symbol,
            year,
            normalized_quarter,
        )
        return payload

    return {
        "meta": meta or {},
        "financials": financials or {},
        "market": market or {},
        "ratios": ratios or {},
        "growth": growth or {},
        "raw_flags": raw_flags or {},
        "shareholder": shareholder or {"largest": []},
        "ai_summary": ai_summary or "",
    }


def save_fundamental_result(payload: dict) -> None:
    meta = payload.get("meta") or {}
    financials = payload.get("financials") or {}
    market = payload.get("market") or {}
    ratios = payload.get("ratios") or {}
    growth = payload.get("growth") or {}
    raw_flags = payload.get("raw_flags") or {}
    shareholder = payload.get("shareholder") or {"largest": []}
    ai_summary = payload.get("ai_summary")

    kode_emiten = _normalize_symbol(meta.get("kode_emiten"))
    periode = _normalize_period(meta.get("periode"))

    if not kode_emiten or meta.get("tahun") in (None, ""):
        logger.warning(
            "Skipping database save because primary key fields are missing: %s %s %s",
            kode_emiten or "UNKNOWN",
            meta.get("tahun"),
            periode or "AUDIT",
        )
        return

    try:
        tahun = int(meta.get("tahun"))
    except (TypeError, ValueError):
        logger.warning(
            "Skipping database save because year is invalid: %s %s %s",
            kode_emiten or "UNKNOWN",
            meta.get("tahun"),
            periode or "AUDIT",
        )
        return

    try:
        _ensure_schema()
        session_factory = _get_session_factory()
        with session_factory() as session:
            existing = session.execute(
                select(FundamentalResult).where(
                    FundamentalResult.kode_emiten == kode_emiten,
                    FundamentalResult.tahun == tahun,
                    FundamentalResult.periode == periode,
                )
            ).scalar_one_or_none()

            if existing is None:
                existing = FundamentalResult(
                    kode_emiten=kode_emiten,
                    tahun=tahun,
                    periode=periode,
                    meta=meta,
                    financials=financials,
                    market=market,
                    ratios=ratios,
                    growth=growth,
                    raw_flags=raw_flags,
                    shareholder=shareholder,
                    ai_summary=ai_summary,
                    payload=payload,
                )
                session.add(existing)
            else:
                existing.meta = meta
                existing.financials = financials
                existing.market = market
                existing.ratios = ratios
                existing.growth = growth
                existing.raw_flags = raw_flags
                existing.shareholder = shareholder
                existing.ai_summary = ai_summary
                existing.payload = payload

            session.commit()
        logger.info(
            "Saved fundamental result to database: %s %s %s",
            kode_emiten or "UNKNOWN",
            tahun,
            periode or "AUDIT",
        )
    except SQLAlchemyError:
        logger.exception(
            "Failed to save fundamental result to database: %s %s %s",
            kode_emiten or "UNKNOWN",
            tahun,
            periode or "AUDIT",
        )
    except Exception:
        logger.exception(
            "Unexpected error while saving fundamental result: %s %s %s",
            kode_emiten or "UNKNOWN",
            tahun,
            periode or "AUDIT",
        )
