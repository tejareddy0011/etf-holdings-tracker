import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_FILES_DIR = DATA_DIR / "raw_files"
DB_PATH = DATA_DIR / "etf_holdings.db"

# Load local .env file if present (without requiring python-dotenv)
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_FILES_DIR.mkdir(parents=True, exist_ok=True)

# Target ETF Scraping Config
DEFAULT_ETF_SYMBOL = "MFSV"
DEFAULT_ETF_NAME = "MFS Active Value ETF"
TARGET_URL = os.getenv(
    "MFS_TARGET_URL",
    "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSV-active-value-etf.html#",
)
HISTORICAL_BASE_URL = (
    "https://www.mfs.com/en-us/individual-investor/product-strategies/"
    "exchange-traded-funds/full-holdings/MFSV-active-value-etf"
)

PRESET_ETF_TARGETS = [
    {
        "etf_symbol": "MFSV",
        "etf_name": "MFS Active Value ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSV-active-value-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MFSV-active-value-etf",
        "is_active": 1,
    },
    {
        "etf_symbol": "MFSG",
        "etf_name": "MFS Active Growth ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSG-active-growth-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MFSG-active-growth-etf",
        "is_active": 1,
    },
    {
        "etf_symbol": "MMID",
        "etf_name": "MFS Active Mid Cap ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MMID-active-mid-cap-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MMID-active-mid-cap-etf",
        "is_active": 1,
    },
    {
        "etf_symbol": "MFSI",
        "etf_name": "MFS Active International ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSI-active-international-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MFSI-active-international-etf",
        "is_active": 1,
    },
    {
        "etf_symbol": "BRCE",
        "etf_name": "MFS Blended Research Core Equity ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/BRCE-blended-research-core-equity-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/BRCE-blended-research-core-equity-etf",
        "is_active": 1,
    },
]

# Schedule Config: 7:00 PM PT daily
SCHEDULE_TIMEZONE = "America/Los_Angeles"
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "19"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))

# Error Notification Config
ALERT_RECIPIENT_EMAIL = os.getenv("ALERT_RECIPIENT_EMAIL", "gvarun@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "etf-scraper-bot@localhost")

# Optional Proxy Rotation & Cloud Storage Config
PROXY_LIST = [p.strip() for p in os.getenv("SCRAPER_PROXIES", "").split(",") if p.strip()]
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")
