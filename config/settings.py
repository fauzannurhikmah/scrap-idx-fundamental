import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL_AI = os.getenv("BASE_URL_AI", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
FLASK_RELOAD = os.getenv("FLASK_RELOAD", "true").lower() == "true"

IDX_BASE_URL = "https://www.idx.co.id"
IDX_API_URL = f"{IDX_BASE_URL}/api/FundamentalAnalysis"

REQUEST_TIMEOUT = 30

POSTGRES_URL = os.getenv("POSTGRES_URL", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
POSTGRES_DB = os.getenv("POSTGRES_DB", "scrap_idx")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
POSTGRES_TABLE = os.getenv("POSTGRES_TABLE", "fundamental_results")
