from flask import Blueprint, request, jsonify
from app.scraper import scrape_fundamental
from utils.ai import summarize_fundamental, extract_financial_metrics

bp = Blueprint("main", __name__)

VALID_QUARTERS = {"Q1", "Q2", "Q3", "Q4"}


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
        fundamental_data = scrape_fundamental(symbol, year, quarter)
    except Exception:
        return jsonify({"status": "error", "message": "Failed to retrieve data from IDX. Please try again later."}), 502

    try:
        extracted_metrics = extract_financial_metrics(fundamental_data)
        current_data = fundamental_data.get("data") or {}
        for key, value in extracted_metrics.items():
            if current_data.get(key) in (None, "") and value not in (None, ""):
                current_data[key] = value
        fundamental_data["data"] = current_data
    except Exception:
        # Keep request successful even when metric extraction is unavailable.
        pass

    try:
        summary = summarize_fundamental(fundamental_data)
    except Exception:
        summary = "AI summarization is currently unavailable. Please try again later."

    return jsonify(
        {
            "status": "success",
            "symbol": symbol.upper(),
            "year": year,
            "quarter": quarter,
            "fundamental_data": fundamental_data,
            "ai_summary": summary,
        }
    )
