import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_FILES_DIR = DATA_DIR / "raw_files"
DB_PATH = DATA_DIR / "etf_holdings.db"

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
