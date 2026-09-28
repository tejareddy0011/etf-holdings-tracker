import random
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

from app.config import (
    DEFAULT_ETF_SYMBOL,
    GCS_BUCKET_NAME,
    HISTORICAL_BASE_URL,
    PROXY_LIST,
    RAW_FILES_DIR,
    SCHEDULE_TIMEZONE,
    TARGET_URL,
)
from app.database import (
    get_etf_target,
    get_settings,
    log_scrape_finish,
    log_scrape_start,
    save_holdings,
)
from app.notifier import KnownErrorType, ScraperBotError, send_failure_notification
from app.parser import parse_mfs_xls_file

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

MFS_SPLASH_PREFERENCE_COOKIES = (
    "bh_user_role=inv; bh_user_remRole=inv; bh_user_loc=us; "
    "user_locale=en_us|us|inv; OptanonAlertBoxClosed=2026-01-01T00:00:00.000Z"
)


def get_etf_raw_dir(etf_symbol: str) -> Path:
    """Returns the dedicated directory for a specific ETF's downloaded .xls / .csv files."""
    d = RAW_FILES_DIR / etf_symbol.strip().upper()
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_table2excel_xls(product_name: str, holding_date_raw: str, table_html: str, disclosure_html: str = "") -> str:
    sheet_name = "Daily Holdings"
    return (
        '<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:x="urn:schemas-microsoft-com:office:excel" '
        'xmlns="http://www.w3.org/TR/REC-html40">'
        "<head>"
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        "<!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet>"
        f"<x:Name>{sheet_name}</x:Name>"
        "<x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>"
        "</x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]-->"
        "</head><body>"
        f"{table_html}"
        f"{disclosure_html}"
        "</body></html>"
    )


def fetch_mfs_page_with_splash_bypass(url: str) -> str:
    settings = get_settings()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": MFS_SPLASH_PREFERENCE_COOKIES,
    }

    handlers = []
    if settings.get("proxy_enabled") == "true" and PROXY_LIST:
        proxy_url = random.choice(PROXY_LIST)
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))

    opener = urllib.request.build_opener(*handlers)
    clean_url = url.split("#")[0]
    req = urllib.request.Request(clean_url, headers=headers)

    try:
        with opener.open(req, timeout=20) as resp:
            status_code = resp.getcode()
            final_url = resp.geturl()
            html = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            f"HTTP {e.code} error fetching target URL {clean_url}: {e.reason}",
        ) from e
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            f"Target URL unreachable ({clean_url}): {e}",
        ) from e

    if status_code != 200 or not html or len(html) < 500:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            f"Invalid response from target URL (status={status_code}, length={len(html)})",
        )

    if "select-your-role" in final_url.lower() or (
        "js-daily-holdings-page" not in html and "js-full-holding-table" not in html
    ):
        if "individual investor" in html.lower() and "save my preferences" in html.lower() and "js-full-holding-table" not in html:
            raise ScraperBotError(
                KnownErrorType.POPUPS_FAILED_TO_CLEAR,
                "Splash screen ('Individual Investor' / 'Save my preferences' pop-ups) blocked access to holdings content.",
            )
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            "Page layout changed: expected holdings container (.js-daily-holdings-page / .js-full-holding-table) not found.",
        )

    return html


def extract_and_save_xls(
    html: str,
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    is_historical: bool = False,
    fallback_date: Optional[str] = None,
) -> Path:
    soup = BeautifulSoup(html, "html.parser")

    download_btn = soup.find("a", class_="js-download-btn")
    if not download_btn:
        raise ScraperBotError(
            KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
            'Could not locate the "Download Daily Fund Holdings" link (.js-download-btn) on the page.',
        )

    btn_text = download_btn.get_text(" ", strip=True)
    if not is_historical and "download daily fund holdings" not in btn_text.lower():
        raise ScraperBotError(
            KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
            f'Download link text was altered (expected "Download Daily Fund Holdings", found "{btn_text}").',
        )

    table = soup.find("table", class_="js-full-holding-table")
    if not table:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            "Holdings data table (.js-full-holding-table) missing from page layout.",
        )

    product_el = soup.find(class_="product-name")
    product_name = product_el.get_text(strip=True).replace(" ", "_") if product_el else f"{etf_symbol}_ETF"
    securities_date = table.get("data-daily-holdings-securitiesdate") or fallback_date or datetime.now().strftime("%m-%d-%y")

    disclosure_el = soup.find(class_="fullholding-disclosure")
    disclosure_html = str(disclosure_el) if disclosure_el else ""

    xls_content = build_table2excel_xls(
        product_name=product_name,
        holding_date_raw=str(securities_date),
        table_html=str(table),
        disclosure_html=disclosure_html,
    )

    if not xls_content or len(xls_content) < 200:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            "Generated Excel (.xls) payload is empty or corrupted.",
        )

    download_stamp = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d")
    base_filename = f"{product_name}-Daily_Holdings_{securities_date}"
    stamped_filename = f"{base_filename}_downloaded_{download_stamp}.xls"
    canonical_filename = f"{base_filename}.xls"

    # Store inside the per-ETF subdirectory (data/raw_files/<ETF_SYMBOL>/)
    etf_dir = get_etf_raw_dir(etf_symbol)
    stamped_path = etf_dir / stamped_filename
    canonical_path = etf_dir / canonical_filename

    try:
        stamped_path.write_text(xls_content, encoding="utf-8")
        canonical_path.write_text(xls_content, encoding="utf-8")
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Failed to write downloaded Excel file to storage directory: {e}",
        ) from e

    if GCS_BUCKET_NAME:
        try:
            from google.cloud import storage  # type: ignore
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(f"mfs_etf_holdings/{etf_symbol.upper()}/{stamped_filename}")
            blob.upload_from_filename(str(stamped_path))
        except Exception as gcs_err:
            print(f"Warning: GCS bucket upload failed ({gcs_err}); local copy saved at {stamped_path}")

    return stamped_path


def run_daily_scrape(
    trigger_type: str = "MANUAL",
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    url: Optional[str] = None,
    simulate_error: Optional[str] = None,
) -> Dict[str, Any]:
    sym = etf_symbol.strip().upper()
    target_cfg = get_etf_target(sym)
    target_url = url or (target_cfg["daily_url"] if target_cfg else TARGET_URL)
    file_fmt = (target_cfg.get("file_format") if target_cfg else "XLS") or "XLS"
    run_id = log_scrape_start(trigger_type=trigger_type, etf_symbol=sym)

    try:
        if simulate_error:
            error_descriptions = {
                KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED: (
                    "Simulated diagnostic check: Target URL returned 503 / layout container missing."
                ),
                KnownErrorType.POPUPS_FAILED_TO_CLEAR: (
                    'Simulated diagnostic check: "Individual Investor" / "Save my preferences" modal failed to dismiss.'
                ),
                KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED: (
                    'Simulated diagnostic check: "Download Daily Fund Holdings" (.js-download-btn) link missing from DOM.'
                ),
                KnownErrorType.EXCEL_FAILED_OR_CORRUPTED: (
                    "Simulated diagnostic check: Downloaded .xls/.csv file truncated (0 bytes) or corrupted header."
                ),
            }
            raise ScraperBotError(
                simulate_error,
                error_descriptions.get(simulate_error, f"Simulated failure for {simulate_error}"),
            )

        if target_url.lower().endswith(".csv") or (file_fmt.upper() == "CSV" and "mfs.com" not in target_url.lower()):
            session = get_anonymous_session()
            resp = session.get(target_url, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code != 200 or not resp.text.strip():
                raise ScraperBotError(
                    KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                    f"CSV target URL returned HTTP {resp.status_code}",
                )
            download_stamp = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d")
            etf_dir = get_etf_raw_dir(sym)
            raw_file_path = etf_dir / f"{sym}_Daily_Holdings_downloaded_{download_stamp}.csv"
            raw_file_path.write_text(resp.text, encoding="utf-8")
        else:
            html = fetch_mfs_page_with_splash_bypass(target_url)
            raw_file_path = extract_and_save_xls(html, etf_symbol=sym, is_historical=False)

        records = parse_mfs_xls_file(raw_file_path)
        inserted_count = save_holdings(
            records=records,
            etf_symbol=sym,
            source_file=f"{sym}/{raw_file_path.name}",
        )
        holding_date = records[0]["holding_date"]

        log_scrape_finish(
            run_id=run_id,
            status="SUCCESS",
            holding_date=holding_date,
            raw_file_path=f"{sym}/{raw_file_path.name}",
            records_parsed=inserted_count,
        )
        return {
            "run_id": run_id,
            "etf_symbol": sym,
            "status": "SUCCESS",
            "holding_date": holding_date,
            "raw_file": f"{sym}/{raw_file_path.name}",
            "records_inserted": inserted_count,
        }

    except ScraperBotError as bot_err:
        log_scrape_finish(
            run_id=run_id,
            status="FAILED",
            error_type=bot_err.error_type,
            error_message=bot_err.message,
        )
        alert_info = send_failure_notification(
            error_type=bot_err.error_type,
            error_details=f"[ETF: {sym}] {bot_err.message}",
        )
        return {
            "run_id": run_id,
            "etf_symbol": sym,
            "status": "FAILED",
            "error_type": bot_err.error_type,
            "error_message": bot_err.message,
            "alert_sent": alert_info,
        }
    except Exception as unexpected_err:
        err_type = KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED
        err_msg = f"Unexpected scraper exception: {unexpected_err}"
        log_scrape_finish(
            run_id=run_id,
            status="FAILED",
            error_type=err_type,
            error_message=err_msg,
        )
        alert_info = send_failure_notification(
            error_type=err_type,
            error_details=f"[ETF: {sym}] {err_msg}",
        )
        return {
            "run_id": run_id,
            "etf_symbol": sym,
            "status": "FAILED",
            "error_type": err_type,
            "error_message": err_msg,
            "alert_sent": alert_info,
        }


def seed_historical_mfs_dates(
    dates: Optional[List[str]] = None,
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
) -> List[Dict[str, Any]]:
    sym = etf_symbol.strip().upper()
    target_cfg = get_etf_target(sym)
    hist_base = (target_cfg.get("historical_base_url") if target_cfg else None) or HISTORICAL_BASE_URL
    target_dates = dates or ["2026-08-31", "2026-07-31", "2026-06-30", "2026-03-31", "2025-12-31"]
    results = []
    for d in target_dates:
        hist_url = f"{hist_base}-date-{d}.html"
        run_id = log_scrape_start(trigger_type=f"HISTORICAL_SYNC ({d})", etf_symbol=sym)
        try:
            html = fetch_mfs_page_with_splash_bypass(hist_url)
            raw_file_path = extract_and_save_xls(html, etf_symbol=sym, is_historical=True, fallback_date=d)
            records = parse_mfs_xls_file(raw_file_path, fallback_date=d)
            for r in records:
                r["holding_date"] = d
            inserted = save_holdings(
                records=records,
                etf_symbol=sym,
                source_file=f"{sym}/{raw_file_path.name}",
            )
            log_scrape_finish(
                run_id=run_id,
                status="SUCCESS",
                holding_date=d,
                raw_file_path=f"{sym}/{raw_file_path.name}",
                records_parsed=inserted,
            )
            results.append({"etf_symbol": sym, "date": d, "status": "SUCCESS", "records": inserted, "file": f"{sym}/{raw_file_path.name}"})
        except Exception as e:
            log_scrape_finish(
                run_id=run_id,
                status="FAILED",
                holding_date=d,
                error_type=KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                error_message=str(e),
            )
            results.append({"etf_symbol": sym, "date": d, "status": "FAILED", "error": str(e)})
    return results
