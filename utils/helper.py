from app.scraper import scrape_fundamental, find_shareholders
from app.db import get_fundamental_result, save_fundamental_result

VALID_QUARTERS = {"Q1", "Q2", "Q3", "Q4"}
INVALID_SHAREHOLDER_KEYWORDS = ("penerbitan", "modal", "capital", "issued", "treasury")

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


def _normalize_shareholder_response(payload):
    if not isinstance(payload, dict):
        return payload

    shareholder = payload.get("shareholder")
    if not isinstance(shareholder, dict):
        payload["shareholder"] = {"largest": []}
        return payload

    largest = shareholder.get("largest")
    if isinstance(largest, list):
        payload["shareholder"] = {"largest": largest}
        return payload
    if isinstance(largest, dict):
        payload["shareholder"] = {"largest": [largest]}
        return payload

    payload["shareholder"] = {"largest": []}
    return payload


def _to_int_number(value):
    number = _to_number(value)
    if number is None:
        return None
    try:
        return int(number)
    except Exception:
        return None


def _normalize_shareholder_entry(entry):
    if not isinstance(entry, dict):
        return None

    name = str(entry.get("name") or "").strip()
    if not name:
        return None

    if any(keyword in name.lower() for keyword in INVALID_SHAREHOLDER_KEYWORDS):
        return None

    shares = _to_int_number(entry.get("shares"))
    if shares is None or shares <= 0:
        return None

    ownership = _to_number(entry.get("ownership"))
    if ownership is not None:
        try:
            ownership = float(ownership)
        except Exception:
            ownership = None
    if ownership is None:
        return None
    if not (0 < ownership <= 100):
        ownership = None
    if ownership is None:
        return None

    return {
        "name": name,
        "shares": shares,
        "ownership": ownership,
    }