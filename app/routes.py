from flask import Blueprint, request, jsonify, current_app
from app.scraper import scrape_fundamental, find_shareholders
from app.db import get_fundamental_result, save_fundamental_result
from utils.ai import summarize_fundamental, extract_financial_metrics
from utils.market import fetch_market_snapshot
from utils.technical import fetch_technical_analysis
from utils.helper import (
    VALID_QUARTERS,
    _pick_first,
    _to_number,
    _pick_metric,
    _calculate_yoy,
    _load_previous_period_fundamental,
    _normalize_shareholder_entry,
    _normalize_shareholder_response,
    _enrich_growth,
    _normalize_current_data,
)

bp = Blueprint("main", __name__, url_prefix="/api")


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@bp.route("/fundamental", methods=["POST"])
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
        cached_payload = get_fundamental_result(symbol, year, request_period)
        if cached_payload:
            cached_payload = _normalize_shareholder_response(cached_payload)
            cached_largest = ((cached_payload.get("shareholder") or {}).get("largest") or [])
            if isinstance(cached_largest, list) and not cached_largest:
                current_app.logger.info(
                    "Cached payload has empty shareholder list for %s %s %s, refreshing from source.",
                    symbol,
                    year,
                    request_period,
                )
                cached_payload = None

        if cached_payload:
            current_app.logger.info(
                "Returning cached fundamental payload from database for %s %s %s",
                symbol,
                year,
                request_period,
            )
            return jsonify(cached_payload)
    except Exception as exc:
        current_app.logger.warning(
            "Database lookup failed for %s %s %s, continuing with scrape: %s",
            symbol,
            year,
            request_period,
            exc,
        )

    try:
        fundamental_data = scrape_fundamental(symbol, year, quarter)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to retrieve data from IDX. Please try again later."
        }), 502
    
    report_text = fundamental_data.get("report_text") or ""
    shareholders = []

    if report_text:
        try:
            extracted_shareholders = find_shareholders(report_text)
        except Exception as exc:
            current_app.logger.warning(
                "Shareholder screening from report failed for %s %s %s: %s",
                symbol,
                year,
                request_period,
                exc,
            )
            extracted_shareholders = []

        if isinstance(extracted_shareholders, list):
            for item in extracted_shareholders:
                normalized_item = _normalize_shareholder_entry(item)
                if normalized_item:
                    shareholders.append(normalized_item)
        if shareholders:
            shareholders.sort(key=lambda item: item.get("shares") or 0, reverse=True)

    print(f"Extracted shareholders for {symbol} {year} {request_period}: {shareholders}")

    # fallback AI only when report does not provide valid shareholder rows
    if not shareholders:
        from utils.ai import extract_shareholders_ai
        try:
            ai_data = extract_shareholders_ai(fundamental_data)
        except Exception as exc:
            current_app.logger.warning(
                "AI shareholder extraction failed for %s %s %s: %s",
                symbol,
                year,
                request_period,
                exc,
            )
            ai_data = []

        print(f"AI extracted shareholders for {symbol} {year} {request_period}: {ai_data}")
        if isinstance(ai_data, list):
            for item in ai_data:
                normalized_item = _normalize_shareholder_entry(item)
                if normalized_item:
                    shareholders.append(normalized_item)
            if shareholders:
                shareholders.sort(key=lambda item: item.get("shares") or 0, reverse=True)

    fundamental_data["shareholder"] = {
        "largest": shareholders if isinstance(shareholders, list) else [],
    }

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
        "shareholder": fundamental_data.get("shareholder"),
        "ai_summary": summary,
    }
    response = _normalize_shareholder_response(response)

    try:
        save_fundamental_result(response)
    except Exception:
        # Keep API response successful even when database is unavailable.
        pass

    return jsonify(response)


@bp.route("/technical-analysis", methods=["POST"])
def get_technical_analysis():
    body = request.get_json(silent=True) or {}

    emiten = (
        body.get("emiten")
        or body.get("nama_saham")
        or body.get("symbol")
        or ""
    )
    emiten = str(emiten).strip().upper()

    if not emiten:
        return jsonify({
            "status": "error",
            "errors": ["'emiten' is required (e.g. 'BBCA')"],
        }), 400

    try:
        result = fetch_technical_analysis(emiten)
        return jsonify({
            "status": "ok",
            "input": {"emiten": emiten},
            "technical_analysis": result,
        })
    except ValueError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
        }), 400
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch technical analysis. Please try again later.",
        }), 502
