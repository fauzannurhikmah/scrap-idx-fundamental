import requests
import cloudscraper
from io import BytesIO
from pypdf import PdfReader
from openpyxl import load_workbook
import xlrd
from config.settings import REQUEST_TIMEOUT

QUARTER_MAP = {
    "Q1": "tw1",
    "Q2": "tw2",
    "Q3": "tw3",
    "Q4": "tw4",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.idx.co.id/",
}

BASE_URL = "https://www.idx.co.id"
SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls"}
MAX_ATTACHMENTS_TO_PARSE = 2
MAX_CHARS_PER_FILE = 8000
MAX_TOTAL_CHARS = 14000


def _create_scraper():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

def _get(url: str, params: dict) -> dict:
    """Helper function to make GET requests to IDX API"""
    try:
        # IDX often blocks plain requests clients; cloudscraper handles anti-bot checks.
        scraper = _create_scraper()
        response = scraper.get(
            url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        raise RuntimeError(
            f"IDX API returned HTTP {exc.response.status_code} for {url}"
        ) from exc
    except ValueError:
        # Fallback to plain requests when response body is not valid JSON from scraper.
        response = requests.get(
            url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Request to IDX API failed: {exc}") from exc


def _download_file(url: str) -> bytes:
    try:
        scraper = _create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download IDX attachment: {exc}") from exc


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def _extract_xlsx_text(content: bytes) -> str:
    wb = load_workbook(filename=BytesIO(content), read_only=True, data_only=True)
    rows = []
    for sheet in wb.worksheets:
        rows.append(f"[Sheet: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            clean_cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
            if clean_cells:
                rows.append(" | ".join(clean_cells))
    wb.close()
    return "\n".join(rows).strip()


def _extract_xls_text(content: bytes) -> str:
    wb = xlrd.open_workbook(file_contents=content)
    rows = []
    for sheet in wb.sheets():
        rows.append(f"[Sheet: {sheet.name}]")
        for i in range(sheet.nrows):
            values = sheet.row_values(i)
            clean_cells = [str(cell).strip() for cell in values if str(cell).strip()]
            if clean_cells:
                rows.append(" | ".join(clean_cells))
    return "\n".join(rows).strip()


def _extract_attachment_text(file_name: str, content: bytes) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith(".pdf"):
        return _extract_pdf_text(content)
    if lower_name.endswith(".xlsx"):
        return _extract_xlsx_text(content)
    if lower_name.endswith(".xls"):
        return _extract_xls_text(content)
    return ""


def _collect_report_text(raw_data: dict) -> tuple[str, list[dict]]:
    attachments = raw_data.get("Attachments") or []
    eligible = []

    for item in attachments:
        file_name = str(item.get("File_Name") or "")
        ext = str(item.get("File_Type") or "").lower()
        if not ext and "." in file_name:
            ext = "." + file_name.lower().split(".")[-1]
        if ext in SUPPORTED_EXTENSIONS:
            eligible.append(item)

    parsed_docs = []
    text_chunks = []
    total_chars = 0

    for item in eligible[:MAX_ATTACHMENTS_TO_PARSE]:
        file_name = str(item.get("File_Name") or "unknown")
        file_path = str(item.get("File_Path") or "")
        file_url = file_path if file_path.startswith("http") else f"{BASE_URL}{file_path}"

        try:
            content = _download_file(file_url)
            extracted = _extract_attachment_text(file_name, content)
        except Exception:
            extracted = ""

        extracted = extracted.strip()
        extracted = extracted[:MAX_CHARS_PER_FILE]

        parsed_docs.append(
            {
                "file_name": file_name,
                "file_type": item.get("File_Type"),
                "file_url": file_url,
                "extracted_chars": len(extracted),
            }
        )

        if extracted:
            remaining = MAX_TOTAL_CHARS - total_chars
            if remaining <= 0:
                break
            excerpt = extracted[:remaining]
            total_chars += len(excerpt)
            text_chunks.append(f"### Dokumen: {file_name}\n{excerpt}")

    return "\n\n".join(text_chunks).strip(), parsed_docs

def get_financial_report(symbol: str, year: int, quarter: str) -> dict:
    """
    Get financial report from IDX API
    
    Args:
        symbol: Stock code (e.g., 'BBRI', 'ASII')
        year: Year of report (e.g., 2024)
        quarter: Quarter (Q1, Q2, Q3, Q4)
    
    Returns:
        dict: Financial report data
    """
    periode = QUARTER_MAP.get(quarter.upper(), "tw1")
    
    url = "https://www.idx.co.id/primary/ListedCompany/GetFinancialReport"
    
    params = {
        "ReportType": "PDF",
        "KodeEmiten": symbol.upper(),
        "Year": str(year),
        "SortColumn": "KodeEmiten",
        "SortOrder": "asc",
        "EmitenType": "s",
        "Periode": periode,
        "indexfrom": 0,
        "pagesize": 0,
    }
    
    try:
        response_data = _get(url, params)
        if response_data.get("ResultCount", 0) > 0 and response_data.get("Results"):
            return response_data["Results"][0]

        # Fallback for potential API changes in period casing.
        fallback_params = {**params, "Periode": periode.upper()}
        fallback_response_data = _get(url, fallback_params)
        if fallback_response_data.get("ResultCount", 0) > 0 and fallback_response_data.get("Results"):
            return fallback_response_data["Results"][0]

        raise RuntimeError(f"No financial data found for {symbol} - {year} {periode}")
            
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch financial report: {exc}") from exc

def parse_financial_data(raw_data: dict) -> dict:
    """Parse raw IDX API response into standardized format"""
    return {
        "kode_emiten": raw_data.get("KodeEmiten"),
        "nama_emiten": raw_data.get("NamaEmiten"),
        "periode_laporan": raw_data.get("PeriodeLaporan"),
        "tanggal_laporan": raw_data.get("TanggalLaporan"),
        "revenue": raw_data.get("Revenue"),
        "cost_of_goods_sold": raw_data.get("CostOfGoodsSold"),
        "gross_profit": raw_data.get("GrossProfit"),
        "operating_expense": raw_data.get("OperatingExpense"),
        "operating_profit": raw_data.get("OperatingProfit"),
        "net_profit": raw_data.get("NetProfit"),
        "total_assets": raw_data.get("TotalAssets"),
        "total_liabilities": raw_data.get("TotalLiabilities"),
        "total_equity": raw_data.get("TotalEquity"),
        "eps": raw_data.get("EPS"),
        "book_value_per_share": raw_data.get("BookValuePerShare"),
        "roe": raw_data.get("ROE"),
        "roa": raw_data.get("ROA"),
        "npm": raw_data.get("NPM"),
        "der": raw_data.get("DER"),
        "per": raw_data.get("PER"),
        "pbr": raw_data.get("PBR"),
        "current_ratio": raw_data.get("CurrentRatio"),
    }

def scrape_fundamental(symbol: str, year: int, quarter: str) -> dict:
    """Main function to scrape fundamental data from IDX"""
    raw_data = get_financial_report(symbol, year, quarter)
    parsed_data = parse_financial_data(raw_data)
    report_text, parsed_documents = _collect_report_text(raw_data)
    
    return {
        "symbol": symbol.upper(),
        "year": year,
        "quarter": quarter.upper(),
        "data": parsed_data,
        "report_text": report_text,
        "report_documents": parsed_documents,
        "raw_response": raw_data,
    }