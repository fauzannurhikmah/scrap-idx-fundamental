from flask import Blueprint, request, jsonify, current_app
from app.scraper import scrape_fundamental
from app.db import get_fundamental_result, save_fundamental_result
from utils.ai import summarize_fundamental, extract_financial_metrics
from utils.market import fetch_market_snapshot

bp = Blueprint("main", __name__)

VALID_QUARTERS = {"Q1", "Q2", "Q3", "Q4"}


def _pick_first(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _to_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        try:
            return float(cleaned)
        except (TypeError, ValueError):
            return None
    return None


def _pick_metric(payload, *field_names):
    if not isinstance(payload, dict):
        return None

    for container_name in ("financials", "data", "raw_response"):
        container = payload.get(container_name) or {}
        if not isinstance(container, dict):
            continue

        for field_name in field_names:
            value = container.get(field_name)
            if value not in (None, ""):
                return value

    return None


def _calculate_yoy(current_value, previous_value):
    current_number = _to_number(current_value)
    previous_number = _to_number(previous_value)

    if current_number is None or previous_number in (None, 0):
        return None

    try:
        return round(((current_number - previous_number) / previous_number) * 100, 2)
    except Exception:
        return None


def _load_previous_period_fundamental(symbol, year, quarter):
    previous_year = year - 1
    request_period = quarter or "AUDIT"

    try:
        cached_previous = get_fundamental_result(symbol, previous_year, request_period)
        if cached_previous:
            return cached_previous
    except Exception:
        pass

    try:
        return scrape_fundamental(symbol, previous_year, quarter)
    except Exception:
        return None


def _enrich_growth(payload, symbol, year, quarter):
    if not isinstance(payload, dict):
        return payload

    growth = payload.get("growth") or {}
    if not isinstance(growth, dict):
        growth = {}

    needs_revenue = growth.get("revenue_yoy") in (None, "")
    needs_net_income = growth.get("net_income_yoy") in (None, "")

    if not (needs_revenue or needs_net_income):
        payload["growth"] = growth
        return payload

    previous_payload = _load_previous_period_fundamental(symbol, year, quarter)
    if not previous_payload:
        payload["growth"] = growth
        return payload

    current_revenue = _pick_metric(payload, "revenue", "Revenue", "TotalRevenue", "Sales")
    current_net_income = _pick_metric(payload, "net_income", "net_profit", "NetProfit", "ProfitForTheYear", "ProfitLoss")
    previous_revenue = _pick_metric(previous_payload, "revenue", "Revenue", "TotalRevenue", "Sales")
    previous_net_income = _pick_metric(previous_payload, "net_income", "net_profit", "NetProfit", "ProfitForTheYear", "ProfitLoss")

    if needs_revenue:
        growth["revenue_yoy"] = _calculate_yoy(current_revenue, previous_revenue)
    if needs_net_income:
        growth["net_income_yoy"] = _calculate_yoy(current_net_income, previous_net_income)

    payload["growth"] = growth
    return payload


def _normalize_current_data(current_data):
    if not isinstance(current_data, dict):
        return {}

    normalized = dict(current_data)
    normalized["net_income"] = _pick_first(normalized.get("net_income"), normalized.get("net_profit"))
    normalized["operating_profit"] = _pick_first(normalized.get("operating_profit"), normalized.get("OperatingProfit"), normalized.get("OperatingIncome"))
    normalized["operating_expense"] = _pick_first(normalized.get("operating_expense"), normalized.get("OperatingExpense"), normalized.get("OperatingExpenses"))
    return normalized


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@bp.route("/api/fundamental", methods=["POST"])
def get_fundamental():
    body = request.get_json(silent=True) or {}

    symbol = body.get("symbol", "").strip()
    year = body.get("year")
    quarter = str(body.get("quarter", "")).strip().upper()
    quarter = quarter or None

    errors = []
    if not symbol:
        errors.append("'symbol' is required (e.g. 'BBCA')")
    if not year:
        errors.append("'year' is required (e.g. 2024)")
    else:
        try:
            year = int(year)
        except (ValueError, TypeError):
            errors.append("'year' must be a valid integer")
    if quarter and quarter not in VALID_QUARTERS:
        errors.append(f"'quarter' must be one of {sorted(VALID_QUARTERS)}")

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    request_period = quarter or "AUDIT"

    try:
        fundamental_data = scrape_fundamental(symbol, year, quarter)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to retrieve data from IDX. Please try again later."
        }), 502

    market_snapshot = fetch_market_snapshot(symbol)

    # EXISTING EXTRACTION 
    try:
        extracted_metrics = extract_financial_metrics(fundamental_data)
        current_data = fundamental_data.get("data") or {}

        for key, value in extracted_metrics.items():
            if current_data.get(key) in (None, "") and value not in (None, ""):
                current_data[key] = value

        fundamental_data["data"] = current_data
    except Exception:
        current_data = fundamental_data.get("data") or {}

    for key, value in market_snapshot.items():
        if value not in (None, ""):
            current_data[key] = value

    current_app.logger.info(
        "Market snapshot merged for %s: %s",
        symbol,
        market_snapshot,
    )

    #  CALCULATION LAYER (SAFE ADDITION)
    try:
        revenue = current_data.get("revenue")
        net_income = current_data.get("net_profit")
        total_assets = current_data.get("total_assets")
        total_equity = current_data.get("total_equity")
        total_liabilities = current_data.get("total_liabilities")
        shares = current_data.get("shares_outstanding")
        price = current_data.get("price")

        # NPM
        if current_data.get("npm") in (None, "") and revenue and net_income:
            try:
                current_data["npm"] = round((net_income / revenue) * 100, 2)
            except Exception:
                pass

        # Total Liabilities
        if current_data.get("total_liabilities") in (None, "") and total_assets and total_equity:
            try:
                current_data["total_liabilities"] = total_assets - total_equity
            except Exception:
                pass

        # Operating Profit (fallback): Operating Profit = Revenue - Operating Expense
        if current_data.get("operating_profit") in (None, ""):
            try:
                revenue_value = _to_number(revenue)
                operating_expense_value = _to_number(current_data.get("operating_expense"))
                if revenue_value is not None and operating_expense_value is not None:
                    current_data["operating_profit"] = revenue_value - operating_expense_value
            except Exception:
                pass

        # Current Ratio (fallback): Current Assets / Current Liabilities
        if current_data.get("current_ratio") in (None, ""):
            try:
                current_assets = _to_number(_pick_first(current_data.get("current_assets"), total_assets))
                current_liabilities = _to_number(_pick_first(current_data.get("current_liabilities"), total_liabilities))
                if current_liabilities not in (None, 0) and current_assets is not None:
                    current_data["current_ratio"] = round(current_assets / current_liabilities, 4)
            except Exception:
                pass

        # DER (fallback): Total Liabilities / Total Equity
        if current_data.get("der") in (None, ""):
            try:
                liabilities_value = _to_number(_pick_first(current_data.get("total_liabilities"), total_liabilities))
                equity_value = _to_number(total_equity)
                if liabilities_value is not None and equity_value not in (None, 0):
                    current_data["der"] = round(liabilities_value / equity_value, 4)
            except Exception:
                pass

        # ROA fallback
        if current_data.get("roa") in (None, ""):
            try:
                net_income_value = _to_number(net_income)
                assets_value = _to_number(total_assets)
                if net_income_value is not None and assets_value not in (None, 0):
                    current_data["roa"] = round((net_income_value / assets_value) * 100, 2)
            except Exception:
                pass

        # ROE fallback
        if current_data.get("roe") in (None, ""):
            try:
                net_income_value = _to_number(net_income)
                equity_value = _to_number(total_equity)
                if net_income_value is not None and equity_value not in (None, 0):
                    current_data["roe"] = round((net_income_value / equity_value) * 100, 2)
            except Exception:
                pass

        # EPS
        if current_data.get("eps") in (None, "") and net_income and shares:
            try:
                current_data["eps"] = net_income / shares
            except Exception:
                pass

        # BVPS
        if current_data.get("book_value_per_share") in (None, "") and total_equity and shares:
            try:
                current_data["book_value_per_share"] = total_equity / shares
            except Exception:
                pass

        # PER
        if current_data.get("per") in (None, "") and price and current_data.get("eps"):
            try:
                current_data["per"] = price / current_data.get("eps")
            except Exception:
                pass

        # PBR
        if current_data.get("pbr") in (None, "") and price and current_data.get("book_value_per_share"):
            try:
                current_data["pbr"] = price / current_data.get("book_value_per_share")
            except Exception:
                pass

    except Exception:
        pass

    fundamental_data = _enrich_growth(fundamental_data, symbol, year, quarter)
    current_data = _normalize_current_data(fundamental_data.get("data") or {})
    if current_data:
        fundamental_data["data"] = current_data

    # EXISTING AI SUMMARY 
    try:
        summary = summarize_fundamental(fundamental_data)
    except Exception as e:
        current_app.logger.error(
            "Failed to generate AI summary for %s %d %s: %s",
            symbol,
            year,
            request_period,
            str(e),
        )
        summary = "AI summarization is currently unavailable. Please try again later."

    parsed_data = fundamental_data.get("data") or {}
    raw_data = fundamental_data.get("raw_response") or {}
    attachments = raw_data.get("Attachments") or []
    first_attachment = attachments[0] if attachments else {}

    nama_emiten = _pick_first(
        fundamental_data.get("nama_emiten"),
        parsed_data.get("nama_emiten"),
        raw_data.get("NamaEmiten"),
        first_attachment.get("NamaEmiten"),
    )

    sektor = _pick_first(
        fundamental_data.get("sector"),
        parsed_data.get("sector"),
        raw_data.get("Sector"),
        raw_data.get("Sektor"),
    )

    sub_sektor = _pick_first(
        fundamental_data.get("sub_sector"),
        parsed_data.get("sub_sector"),
        raw_data.get("SubSector"),
        raw_data.get("Sub_Sector"),
        raw_data.get("SubSektor"),
    )

    tanggal_laporan = _pick_first(
        fundamental_data.get("report_date"),
        parsed_data.get("tanggal_laporan"),
        raw_data.get("TanggalLaporan"),
        raw_data.get("Report_Date"),
        raw_data.get("File_Modified"),
    )

    response = {
        "meta": {
            "kode_emiten": _pick_first(fundamental_data.get("symbol"), parsed_data.get("kode_emiten")),
            "nama_emiten": nama_emiten,
            "sektor": sektor,
            "sub_sektor": sub_sektor,
            "periode": request_period,
            "tahun": year,
            "tanggal_laporan": tanggal_laporan,
        },
        "financials": {
            "revenue": current_data.get("revenue"),
            "net_income": _pick_first(current_data.get("net_income"), current_data.get("net_profit")),
            "operating_profit": current_data.get("operating_profit"),
            "operating_expense": current_data.get("operating_expense"),
            "total_assets": current_data.get("total_assets"),
            "total_equity": current_data.get("total_equity"),
            "total_liabilities": current_data.get("total_liabilities"),
        },
        "market": {
            "price": current_data.get("price"),
            "shares_outstanding": current_data.get("shares_outstanding"),
            "market_cap": current_data.get("market_cap"),
        },
        "ratios": {
            "profitability": {
                "roe": current_data.get("roe"),
                "roa": current_data.get("roa"),
                "net_margin": current_data.get("npm"),
            },
            "leverage": {
                "debt_to_equity": current_data.get("der"),
            },
            "liquidity": {
                "current_ratio": current_data.get("current_ratio"),
            },
            "valuation": {
                "eps": current_data.get("eps"),
                "book_value_per_share": current_data.get("book_value_per_share"),
                "per": current_data.get("per"),
                "pbr": current_data.get("pbr"),
            },
        },
        "growth": {
            "revenue_yoy": (fundamental_data.get("growth") or {}).get("revenue_yoy"),
            "net_income_yoy": (fundamental_data.get("growth") or {}).get("net_income_yoy"),
        },
        "raw_flags": {
            "has_cogs": current_data.get("has_cogs", False),
            "has_current_assets": current_data.get("has_current_assets", False),
        },
        "ai_summary": summary,
    }

    try:
        save_fundamental_result(response)
    except Exception:
        # Keep API response successful even when database is unavailable.
        pass

    return jsonify(response)
