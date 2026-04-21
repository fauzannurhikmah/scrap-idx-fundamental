from openai import OpenAI
from config.settings import OPENAI_API_KEY, OPENAI_MODEL, BASE_URL_AI


def summarize_fundamental(data: dict) -> str:
    if not OPENAI_API_KEY:
        return "OpenAI API key not configured."

    client = OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=BASE_URL_AI
    )

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
