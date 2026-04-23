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


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@bp.route("/api/fundamental", methods=["POST"])
def get_fundamental():
    body = request.get_json(silent=True) or {}

    symbol = body.get("symbol", "").strip()
    year = body.get("year")
    quarter = str(body.get("quarter", "")).strip().upper()

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
    if not quarter:
        errors.append("'quarter' is required (Q1, Q2, Q3, or Q4)")
    elif quarter not in VALID_QUARTERS:
        errors.append(f"'quarter' must be one of {sorted(VALID_QUARTERS)}")

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    try:
        cached_payload = get_fundamental_result(symbol, year, quarter)
        if cached_payload:
            return jsonify(cached_payload)
    except Exception:
        # Continue to scrape when database read fails.
        pass

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

    # EXISTING AI SUMMARY (UNCHANGED)
    try:
        summary = summarize_fundamental(fundamental_data)
    except Exception:
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
            "periode": quarter,
            "tahun": year,
            "tanggal_laporan": tanggal_laporan,
        },
        "financials": {
            "revenue": current_data.get("revenue"),
            "net_income": current_data.get("net_profit"),
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
            "revenue_yoy": current_data.get("revenue_yoy"),
            "net_income_yoy": current_data.get("net_income_yoy"),
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