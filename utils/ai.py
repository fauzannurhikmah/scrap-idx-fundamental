from openai import OpenAI
import json
import re
from config.settings import OPENAI_API_KEY, OPENAI_MODEL, BASE_URL_AI


FINANCIAL_FIELDS = [
    "revenue",
    "cost_of_goods_sold",
    "gross_profit",
    "operating_expense",
    "operating_profit",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "eps",
    "book_value_per_share",
    "roe",
    "roa",
    "npm",
    "der",
    "per",
    "pbr",
    "current_ratio",
]

MONETARY_FIELDS = {
    "revenue",
    "cost_of_goods_sold",
    "gross_profit",
    "operating_expense",
    "operating_profit",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "book_value_per_share",
}

RATIO_FIELDS = {
    "roe",
    "roa",
    "npm",
    "der",
    "per",
    "pbr",
    "current_ratio",
}


def _build_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY, base_url=BASE_URL_AI)


def _safe_json_parse(content: str) -> dict:
    text = (content or "").strip()
    if not text:
        return {}

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _to_number(value: str):
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return None

    multiplier = 1
    if "triliun" in cleaned:
        multiplier = 1_000_000_000_000
    elif "miliar" in cleaned:
        multiplier = 1_000_000_000
    elif "juta" in cleaned:
        multiplier = 1_000_000

    cleaned = re.sub(r"[^0-9,.-]", "", cleaned)
    if not cleaned or cleaned in {"-", ".", ","}:
        return None

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")

    try:
        number = float(cleaned)
    except ValueError:
        return None

    number = number * multiplier
    if number.is_integer():
        return int(number)
    return round(number, 4)


def _regex_extract_metric(report_text: str, patterns: list[str]):
    lines = [line.strip() for line in (report_text or "").splitlines() if line.strip()]
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(re.search(pattern, lower) for pattern in patterns):
            candidates = [line]
            if i + 1 < len(lines):
                candidates.append(lines[i + 1])
            if i - 1 >= 0:
                candidates.append(lines[i - 1])

            for candidate in candidates:
                matches = re.findall(r"-?\d[\d\.,]*\s*(triliun|miliar|juta|%)?", candidate, flags=re.IGNORECASE)
                raw_matches = re.finditer(r"-?\d[\d\.,]*\s*(?:triliun|miliar|juta|%)?", candidate, flags=re.IGNORECASE)
                if matches:
                    for m in raw_matches:
                        value = _to_number(m.group(0))
                        if value is not None:
                            return value
    return None


def _extract_metrics_by_regex(report_text: str) -> dict:
    return {
        "revenue": _regex_extract_metric(report_text, [r"\bpendapatan\b", r"\brevenue\b"]),
        "gross_profit": _regex_extract_metric(report_text, [r"\blaba kotor\b", r"\bgross profit\b"]),
        "operating_profit": _regex_extract_metric(report_text, [r"\blaba operasional\b", r"\boperating profit\b"]),
        "net_profit": _regex_extract_metric(report_text, [r"\blaba bersih\b", r"\bnet profit\b"]),
        "total_assets": _regex_extract_metric(report_text, [r"\btotal aset\b", r"\btotal assets\b"]),
        "total_liabilities": _regex_extract_metric(report_text, [r"\btotal liabilitas\b", r"\btotal liabilities\b"]),
        "total_equity": _regex_extract_metric(report_text, [r"\btotal ekuitas\b", r"\btotal equity\b"]),
        "eps": _regex_extract_metric(report_text, [r"\beps\b", r"\bearing per share\b"]),
        "roe": _regex_extract_metric(report_text, [r"\broe\b", r"\breturn on equity\b"]),
        "roa": _regex_extract_metric(report_text, [r"\broa\b", r"\breturn on assets\b"]),
        "der": _regex_extract_metric(report_text, [r"\bder\b", r"\bdebt to equity\b"]),
        "current_ratio": _regex_extract_metric(report_text, [r"\bcurrent ratio\b", r"\brasio lancar\b"]),
        "npm": _regex_extract_metric(report_text, [r"\bnpm\b", r"\bnet profit margin\b"]),
        "per": _regex_extract_metric(report_text, [r"\bper\b", r"\bprice earnings ratio\b"]),
        "pbr": _regex_extract_metric(report_text, [r"\bpbr\b", r"\bprice to book\b"]),
        "cost_of_goods_sold": None,
        "operating_expense": None,
        "book_value_per_share": None,
    }


def _sanitize_extracted_metrics(metrics: dict) -> dict:
    cleaned = {}
    for field in FINANCIAL_FIELDS:
        value = metrics.get(field)
        if value in (None, ""):
            cleaned[field] = None
            continue

        if isinstance(value, str):
            parsed = _to_number(value)
            value = parsed if parsed is not None else value

        if not isinstance(value, (int, float)):
            cleaned[field] = value
            continue

        if field in MONETARY_FIELDS and abs(value) < 1_000:
            cleaned[field] = None
            continue
        if field in RATIO_FIELDS and not (-100 <= value <= 1_000):
            cleaned[field] = None
            continue
        if field == "eps" and abs(value) > 1_000_000:
            cleaned[field] = None
            continue

        cleaned[field] = value

    return cleaned


def extract_financial_metrics(data: dict) -> dict:
    if not OPENAI_API_KEY:
        return {}

    report_text = (data.get("report_text") or "").strip()
    if not report_text:
        return {}

    symbol = data.get("symbol", "")
    year = data.get("year", "")
    quarter = data.get("quarter", "")

    fields_text = "\n".join([f"- {field}" for field in FINANCIAL_FIELDS])
    trimmed_text = report_text[:18000]

    prompt = f"""
Ekstrak nilai metrik finansial dari teks dokumen emiten {symbol} periode {quarter} {year}.

Gunakan hanya angka yang benar-benar disebutkan di teks. Jika tidak ada, isi null.
Jika ada beberapa angka untuk metrik sama, pilih yang paling relevan untuk periode laporan.

Return HANYA JSON object valid, tanpa markdown/code fence, dengan key berikut:
{fields_text}

Isi dokumen:
{trimmed_text}
""".strip()

    client = _build_client()
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Kamu adalah analis keuangan yang mengekstrak angka dari dokumen. "
                    "Jawab strict JSON object saja."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=600,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    parsed = _safe_json_parse(content)

    cleaned = {}
    for field in FINANCIAL_FIELDS:
        cleaned[field] = parsed.get(field)

    if not any(value not in (None, "") for value in cleaned.values()):
        cleaned = _extract_metrics_by_regex(report_text)

    cleaned = _sanitize_extracted_metrics(cleaned)

    return cleaned


def summarize_fundamental(data: dict) -> str:
    if not OPENAI_API_KEY:
        return "OpenAI API key not configured."

    client = _build_client()

    symbol = data.get("symbol", "")
    year = data.get("year", "")
    quarter = data.get("quarter", "")
    report_text = (data.get("report_text") or "").strip()
    report_documents = data.get("report_documents") or []
    core_data = data.get("data") or {}

    if not report_text:
        return "Dokumen laporan tidak tersedia atau gagal diekstrak, jadi AI summary belum bisa dibuat."

    docs_text = "\n".join(
        [
            f"- {doc.get('file_name', 'unknown')} ({doc.get('file_type', 'unknown')}) chars={doc.get('extracted_chars', 0)}"
            for doc in report_documents
        ]
    )

    prompt = f"""
Berikut adalah data dokumen fundamental saham {symbol} untuk periode {quarter} {year}.

Metadata inti:
- Emiten: {core_data.get('nama_emiten', 'N/A')}
- Tanggal laporan: {core_data.get('tanggal_laporan', 'N/A')}
- Periode laporan: {core_data.get('periode_laporan', 'N/A')}

Dokumen yang diproses:
{docs_text}

Isi dokumen (hasil ekstraksi teks):
{report_text}

Tugas:
- Buat ringkasan fundamental dalam Bahasa Indonesia.
- Sorot poin penting: pendapatan, laba, aset-liabilitas, risiko, dan sentimen umum.
- Jika ada angka yang ambigu/tidak lengkap, jelaskan sebagai keterbatasan data.
- Tutup dengan pandangan singkat untuk investor ritel (bukan financial advice).
""".strip()

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Kamu adalah analis saham profesional yang membantu investor ritel "
                    "memahami data fundamental saham Indonesia dengan bahasa yang mudah dipahami."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=500,
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()
