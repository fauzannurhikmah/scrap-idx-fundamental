# Scrap IDX Fundamental

Scrape data fundamental saham Indonesia dari IDX (Bursa Efek Indonesia) dan kirim ke OpenAI untuk summarize.

## Struktur Project

```
.
├── app/
│   ├── __init__.py      # Flask app factory
│   ├── main.py          # Flask app instance
│   ├── routes.py        # API endpoints
│   ├── scraper.py       # IDX data scraper
│   └── utils/
│       └── decorators.py
├── config/
│   └── settings.py      # Konfigurasi & API keys
├── utils/
│   ├── __init__.py
│   └── ai.py            # OpenAI integration
├── requirements.txt
├── .env.example
├── run.py               # Entry point untuk jalankan app
└── README.md
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Konfigurasi environment

Copy `.env.example` ke `.env` dan isi nilai-nilainya:

```bash
cp .env.example .env
```

Edit `.env`:

```
OPENAI_API_KEY=sk-your-openai-api-key-here
FLASK_DEBUG=true
FLASK_RELOAD=true
FLASK_PORT=5000
```

Keterangan:

- `FLASK_DEBUG=true` untuk mode development.
- `FLASK_RELOAD=true` agar server otomatis restart saat ada perubahan file.

### 3. Jalankan aplikasi

```bash
python run.py
```

App akan berjalan di `http://localhost:5000`.

## API Endpoints

### `GET /health`

Health check.

**Response:**

```json
{ "status": "ok" }
```

---

### `POST /api/fundamental`

Ambil data fundamental saham dan AI summary.

**Request body (JSON):**

| Field   | Type   | Required | Keterangan                   |
| ------- | ------ | -------- | ---------------------------- |
| symbol  | string | ✅       | Kode emiten (e.g. `BBCA`)    |
| year    | int    | ✅       | Tahun (e.g. `2024`)          |
| quarter | string | ✅       | Kuartal: `Q1`,`Q2`,`Q3`,`Q4` |

**Contoh Request:**

```bash
curl -X POST http://localhost:5000/api/fundamental \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BBCA", "year": 2024, "quarter": "Q3"}'
```

**Contoh Response:**

```json
{
  "status": "success",
  "symbol": "BBCA",
  "year": 2024,
  "quarter": "Q3",
  "fundamental_data": {
    "symbol": "BBCA",
    "year": 2024,
    "quarter": "Q3",
    "profile": {
      "name": "Bank Central Asia Tbk",
      "sector": "Finance",
      "sub_sector": "Bank",
      "listing_date": "2000-05-31",
      "shares_outstanding": 123456789000
    },
    "financials": {
      "revenue": 25000000000000,
      "gross_profit": 20000000000000,
      "operating_profit": 15000000000000,
      "net_profit": 12000000000000,
      "total_assets": 1300000000000000,
      "total_liabilities": 1100000000000000,
      "total_equity": 200000000000000,
      "eps": 975,
      "book_value_per_share": 16250
    },
    "ratios": {
      "roe": 24.5,
      "roa": 3.2,
      "npm": 48.0,
      "der": 5.5,
      "per": 25.0,
      "pbr": 5.5,
      "current_ratio": 1.2
    }
  },
  "ai_summary": "BBCA menunjukkan kinerja yang sangat baik pada Q3 2024..."
}
```

## Tech Stack

- **Flask** — web framework
- **requests** — HTTP client untuk scraping
- **beautifulsoup4** — HTML parsing
- **openai** — AI summarization
- **python-dotenv** — environment variables
