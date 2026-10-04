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

# Retention Policy: Retain original .csv and .xls files for at least 5 years
RETENTION_YEARS = 5
RETENTION_DAYS = 365 * RETENTION_YEARS

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

# Phase 1 Core 4 ETFs (MFSV, LSVD, VFLO, IVV) + MFSG
PRESET_ETF_TARGETS = [
    {
        "etf_symbol": "MFSV",
        "etf_name": "MFS Active Value ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSV-active-value-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MFSV-active-value-etf",
        "file_format": "XLS",
        "is_active": 1,
    },
    {
        "etf_symbol": "LSVD",
        "etf_name": "LSV Disciplined Value ETF",
        "daily_url": "https://www.lsvasset.com/disciplined-value-etf/",
        "historical_base_url": "",
        "file_format": "CSV",
        "is_active": 1,
    },
    {
        "etf_symbol": "VFLO",
        "etf_name": "VictoryShares Free Cash Flow ETF",
        "daily_url": "https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-free-cash-flow-etf",
        "historical_base_url": "",
        "file_format": "CSV",
        "is_active": 1,
    },
    {
        "etf_symbol": "IVV",
        "etf_name": "iShares Core S&P 500 ETF",
        "daily_url": "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf",
        "historical_base_url": "",
        "file_format": "XLS",
        "is_active": 1,
    },
    {
        "etf_symbol": "MFSG",
        "etf_name": "MFS Active Growth ETF",
        "daily_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSG-active-growth-etf.html#",
        "historical_base_url": "https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/full-holdings/MFSG-active-growth-etf",
        "file_format": "XLS",
        "is_active": 0,
    },
]

# Schedule Config: 7:00 PM PT daily
SCHEDULE_TIMEZONE = "America/Los_Angeles"
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "19"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))

# Error Notification Config (Updated per Phase 1 scope to Adam.smith.fintech@gmail.com)
ALERT_RECIPIENT_EMAIL = os.getenv("ALERT_RECIPIENT_EMAIL", "Adam.smith.fintech@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "etf-scraper-bot@localhost")

# Proxy Rotation & Cloud Storage Config (Webshare 100-proxy pool configured by default)
DEFAULT_PROXY_LIST_URL = os.getenv(
    "WEBSHARE_PROXY_LIST_URL",
    "https://proxy.webshare.io/api/v2/proxy/list/download/kyrrojrdbhfqbnbbkqppcfkwvfskrbzytjwkxsys/-/any/username/direct/-/?plan_id=14421298",
)
DEFAULT_WEBSHARE_PROXIES = [
    "http://bbaaotlk:rnb5xxvwcopc@198.46.241.137:6672",
    "http://bbaaotlk:rnb5xxvwcopc@38.154.182.159:7427",
    "http://bbaaotlk:rnb5xxvwcopc@184.174.27.73:6296",
    "http://bbaaotlk:rnb5xxvwcopc@23.26.95.229:5711",
    "http://bbaaotlk:rnb5xxvwcopc@45.38.89.254:6189",
    "http://bbaaotlk:rnb5xxvwcopc@50.114.8.150:7135",
    "http://bbaaotlk:rnb5xxvwcopc@64.137.42.210:5255",
    "http://bbaaotlk:rnb5xxvwcopc@23.236.196.39:6129",
    "http://bbaaotlk:rnb5xxvwcopc@142.111.48.228:7005",
    "http://bbaaotlk:rnb5xxvwcopc@173.211.69.148:6741",
    "http://bbaaotlk:rnb5xxvwcopc@204.217.160.80:7152",
    "http://bbaaotlk:rnb5xxvwcopc@45.39.15.29:6459",
    "http://bbaaotlk:rnb5xxvwcopc@172.120.119.254:5914",
    "http://bbaaotlk:rnb5xxvwcopc@23.129.252.210:6478",
    "http://bbaaotlk:rnb5xxvwcopc@45.61.96.137:6117",
    "http://bbaaotlk:rnb5xxvwcopc@104.164.49.24:7679",
    "http://bbaaotlk:rnb5xxvwcopc@142.202.254.63:6041",
    "http://bbaaotlk:rnb5xxvwcopc@216.74.80.40:6612",
    "http://bbaaotlk:rnb5xxvwcopc@38.154.224.73:6614",
    "http://bbaaotlk:rnb5xxvwcopc@64.188.120.194:7941",
]
PROXY_LIST = [
    p.strip()
    for p in os.getenv("SCRAPER_PROXIES", "").split(",")
    if p.strip()
] or list(DEFAULT_WEBSHARE_PROXIES)
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")

# Dashboard Authentication Config
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "gvarun007")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "tejavarun7$")
AUTH_SECRET = os.getenv("AUTH_SECRET", "etf-holdings-tracker-hmac-secret-2026")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"


