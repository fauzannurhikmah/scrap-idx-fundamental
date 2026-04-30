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
    import httpx
    http_client = httpx.Client(verify=False)
    return OpenAI(api_key=OPENAI_API_KEY, base_url=BASE_URL_AI, http_client=http_client)


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
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

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

    # Normalize thousand/decimal separators across formats:
    # - 28,032,494 -> 28032494
    # - 1.533.763.445 -> 1533763445
    # - 1,23 or 1.23 stays decimal
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(",") > 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1 and "," not in cleaned:
        cleaned = cleaned.replace(".", "")
    elif "," in cleaned:
        tail = cleaned.split(",")[-1]
        if len(tail) == 3 and cleaned.replace(",", "").isdigit():
            cleaned = cleaned.replace(",", "")
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
        "revenue": _regex_extract_metric(report_text, [r"\bpendapatan\b", r"\brevenue\b", r"\bincome\b"]),
        "gross_profit": _regex_extract_metric(report_text, [r"\blaba kotor\b", r"\bgross profit\b"]),
        "operating_expense": _regex_extract_metric(report_text, [r"\bbeban operasional\b", r"\boperating expense\b", r"\boperating expenses\b"]),
        "operating_profit": _regex_extract_metric(report_text, [r"\blaba operasional\b", r"\boperating profit\b", r"\boperating income\b"]),
        "net_profit": _regex_extract_metric(report_text, [r"\blaba bersih\b", r"\bnet profit\b", r"\bprofit for the period\b", r"\bprofit loss\b"]),
        "total_assets": _regex_extract_metric(report_text, [r"\bjumlah aset\b", r"\btotal aset\b", r"\btotal assets\b"]),
        "total_liabilities": _regex_extract_metric(report_text, [r"\bjumlah liabilitas\b", r"\btotal liabilitas\b", r"\btotal liabilities\b"]),
        "total_equity": _regex_extract_metric(report_text, [r"\bjumlah ekuitas\b", r"\btotal ekuitas\b", r"\btotal equity\b"]),
        "eps": _regex_extract_metric(report_text, [r"\beps\b", r"\bearing per share\b"]),
        "roe": _regex_extract_metric(report_text, [r"\broe\b", r"\breturn on equity\b"]),
        "roa": _regex_extract_metric(report_text, [r"\broa\b", r"\breturn on assets\b"]),
        "der": _regex_extract_metric(report_text, [r"\bder\b", r"\bdebt to equity\b"]),
        "current_ratio": _regex_extract_metric(report_text, [r"\bcurrent ratio\b", r"\brasio lancar\b"]),
        "npm": _regex_extract_metric(report_text, [r"\bnpm\b", r"\bnet profit margin\b"]),
        "per": _regex_extract_metric(report_text, [r"\bper\b", r"\bprice earnings ratio\b"]),
        "pbr": _regex_extract_metric(report_text, [r"\bpbr\b", r"\bprice to book\b"]),
        "cost_of_goods_sold": _regex_extract_metric(report_text, [r"\bbeban pokok\b", r"\bcost of goods sold\b", r"\bcogs\b"]),
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
    report_text = (data.get("report_text") or "").strip()
    if not report_text:
        return {}

    # Deterministic baseline first to improve consistency between requests.
    baseline = _sanitize_extracted_metrics(_extract_metrics_by_regex(report_text))

    if not OPENAI_API_KEY:
        return baseline

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

    try:
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
    except Exception:
        return baseline

    content = response.choices[0].message.content or ""
    parsed = _safe_json_parse(content)

    ai_cleaned = {}
    for field in FINANCIAL_FIELDS:
        ai_cleaned[field] = parsed.get(field)

    ai_cleaned = _sanitize_extracted_metrics(ai_cleaned)

    merged = {}
    for field in FINANCIAL_FIELDS:
        merged[field] = baseline.get(field)
        if merged[field] in (None, "") and ai_cleaned.get(field) not in (None, ""):
            merged[field] = ai_cleaned.get(field)

    return merged


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
        # No document text available - return early without trying API call
        return "Dokumen laporan tidak tersedia atau gagal diekstrak. AI summary memerlukan teks dokumen untuk dianalisis."

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


from openai import OpenAI
import json
from config.settings import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def extract_shareholders_ai(data: dict) -> list[dict]:
    """
    AI fallback untuk shareholder
    - aware symbol / year / quarter
    - PRIORITAS: data dari dokumen
    - OPTIONAL: external lookup
    """

    report_text = (data.get("report_text") or "").strip()
    symbol = data.get("symbol", "")
    year = data.get("year", "")
    quarter = data.get("quarter", "")

    print(f"Running AI shareholder extraction for {symbol} {year} {quarter} with report text length: {len(report_text)} characters.")

    if not report_text:
        return []

    try:
        prompt = f"""
        Kamu adalah sistem data finansial.

        Target:
        - Emiten: {symbol}
        - Tahun: {year}
        - Periode: {quarter}

        TUGAS:
        Cari pemegang saham terbesar.

        RULE:
        1. Cek dokumen terlebih dahulu
        2. Jika ADA di dokumen → gunakan itu
        3. Jika TIDAK ADA di dokumen:
        → gunakan data publik VALID untuk emiten ini
        → contoh: laporan tahunan, data kepemilikan resmi
        4. Jangan mengarang
        5. Ambil hanya pemegang saham utama (bukan direksi kecil)
        6. Nama harus berupa entitas nyata (PT / pemerintah / institusi)

        FORMAT:
        [
        {{
            "name": "...",
            "shares": number/null,
            "ownership": number/null
        }}
        ]

        DOKUMEN:
        {report_text[:12000]}
        """

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON array."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )

        content = response.choices[0].message.content.strip()
        parsed = json.loads(content)

        print(f"Raw content: {content}")
        print(f"Parsed: {parsed}")

        if not isinstance(parsed, list):
            return []

        # VALIDATION LAYER
        clean = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            shares = item.get("shares")

            if not name or not isinstance(name, str):
                continue

            if not isinstance(shares, (int, float)):
                continue

            if shares <= 0:
                continue

            # filter noise
            bad_keywords = ["penerbitan", "modal", "capital", "issued"]
            if any(b in name.lower() for b in bad_keywords):
                continue

            clean.append({
                "name": name.strip(),
                "shares": int(shares),
                "ownership": item.get("ownership"),
            })

        return clean

    except Exception:
        return []

def _safe_json_list_parse(content: str) -> list:
    text = (content or "").strip()
    if not text:
        return []

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _to_int(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except Exception:
            return None
    if isinstance(value, str):
        parsed = _to_number(value)
        if parsed is None:
            return None
        try:
            return int(parsed)
        except Exception:
            return None
    return None


def extract_shareholders_ai(data: dict) -> list[dict]:
    report_text = (data.get("report_text") or "").strip()
    symbol = data.get("symbol", "")
    year = data.get("year", "")
    quarter = data.get("quarter", "")

    print(
        f"Running AI shareholder extraction for {symbol} {year} {quarter} "
        f"with report text length: {len(report_text)} characters."
    )

    if not report_text:
        return []

    try:
        prompt = f"""
        Kamu adalah sistem data finansial.

        Target:
        - Emiten: {symbol}
        - Tahun: {year}
        - Periode: {quarter}

        TUGAS:
        Cari daftar pemegang saham terbesar (minimal 3 jika data tersedia).

        RULE:
        1. Cek dokumen terlebih dahulu.
        2. Jika ADA di dokumen, gunakan data dari dokumen.
        3. Jika TIDAK ADA di dokumen, boleh gunakan data publik valid (laporan tahunan/disclosure resmi).
        4. Jangan mengarang. Jika tidak yakin, kembalikan [].
        5. Ambil hanya pemegang saham utama, bukan baris modal/penerbitan saham.
        6. Nama harus berupa entitas nyata (PT / pemerintah / institusi).
        7. shares wajib integer jumlah lembar saham (tidak boleh null).
        8. ownership wajib persen kepemilikan (0-100) (tidak boleh null).
        9. Jika tidak bisa memberi shares+ownership valid, jangan kirim item itu.

        FORMAT:
        [
        {{
            "name": "...",
            "shares": number/null,
            "ownership": number/null
        }}
        ]

        DOKUMEN:
        {report_text[:12000]}
        """.strip()

        client = _build_client()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON array."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )

        content = response.choices[0].message.content or ""
        parsed = _safe_json_list_parse(content)
        if not parsed:
            parsed_obj = _safe_json_parse(content)
            nested = parsed_obj.get("shareholders") if isinstance(parsed_obj, dict) else None
            if isinstance(nested, list):
                parsed = nested

        print(f"Raw content: {content}")
        print(f"Parsed: {parsed}")

        if not isinstance(parsed, list):
            return []

        clean = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            shares = _to_int(item.get("shares"))
            ownership = _to_number(item.get("ownership"))
            if ownership is not None:
                try:
                    ownership = float(ownership)
                except Exception:
                    ownership = None

            if not name or not isinstance(name, str):
                continue

            if shares is None or shares <= 0:
                continue

            bad_keywords = ["penerbitan", "modal", "capital", "issued", "treasury"]
            if any(b in name.lower() for b in bad_keywords):
                continue

            clean.append(
                {
                    "name": name.strip(),
                    "shares": shares,
                    "ownership": ownership if ownership is not None and (0 < ownership <= 100) else None,
                }
            )

        clean = [
            item
            for item in clean
            if item.get("shares") not in (None, 0)
            and item.get("ownership") is not None
            and 0 < item.get("ownership") <= 100
        ]
        clean.sort(key=lambda item: item.get("shares") or 0, reverse=True)
        return clean

    except Exception:
        return []
