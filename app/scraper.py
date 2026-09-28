import json
import random
import urllib.error
import urllib.parse
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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


def get_pt_timestamp_str() -> str:
    """Returns a PT date-and-time stamp string (YYYY-MM-DD_HH-MM-SS_PT) for downloaded files."""
    return datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d_%H-%M-%S_PT")


def get_configured_proxies() -> List[str]:
    settings = get_settings()
    db_proxies = [p.strip() for p in settings.get("proxy_urls", "").split(",") if p.strip()]
    env_proxies = [p.strip() for p in PROXY_LIST if p.strip()]
    return list(dict.fromkeys(db_proxies + env_proxies))



def http_get_text(url: str, extra_headers: Optional[Dict[str, str]] = None, timeout: int = 25) -> str:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    clean_url = url.split("#")[0]

    proxies = get_configured_proxies()
    shuffled_proxies = list(proxies)
    random.shuffle(shuffled_proxies)
    # Try configured proxies in random order, then fall back to direct connection (None)
    attempts: List[Optional[str]] = shuffled_proxies + [None]

    last_err: Optional[Exception] = None
    for proxy_url in attempts:
        handlers = []
        if proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(clean_url, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            last_err = e
            # If proxy returned 403/407/429/5xx, try next proxy or direct
            if proxy_url is not None:
                continue
            raise ScraperBotError(
                KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                f"HTTP {e.code} error fetching {url}: {e.reason}",
            ) from e
        except Exception as e:
            last_err = e
            if proxy_url is not None:
                continue
            raise ScraperBotError(
                KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                f"Target URL unreachable ({url}): {e}",
            ) from e

    raise ScraperBotError(
        KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
        f"Target URL unreachable ({url}): {last_err}",
    )



def write_stamped_and_canonical_file(etf_symbol: str, base_stem: str, ext: str, content: str) -> Path:
    """
    Writes both a timestamped copy (with download date and time in PT) and a canonical copy
    inside data/raw_files/<ETF_SYMBOL>/ for 5+ year retention.
    """
    if not content or len(content) < 50:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded {ext} payload for {etf_symbol} is empty or corrupted.",
        )
    ts = get_pt_timestamp_str()
    clean_ext = ext if ext.startswith(".") else f".{ext}"
    stamped_filename = f"{base_stem}_downloaded_{ts}{clean_ext}"
    canonical_filename = f"{base_stem}{clean_ext}"

    etf_dir = get_etf_raw_dir(etf_symbol)
    stamped_path = etf_dir / stamped_filename
    canonical_path = etf_dir / canonical_filename

    try:
        stamped_path.write_text(content, encoding="utf-8")
        canonical_path.write_text(content, encoding="utf-8")
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Failed to write downloaded file to storage directory: {e}",
        ) from e

    if GCS_BUCKET_NAME:
        try:
            from google.cloud import storage  # type: ignore
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(f"etf_holdings/{etf_symbol.upper()}/{stamped_filename}")
            blob.upload_from_filename(str(stamped_path))
        except Exception as gcs_err:
            print(f"Warning: GCS bucket upload failed ({gcs_err}); local copy saved at {stamped_path}")

    return stamped_path


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
    html = http_get_text(url, extra_headers={"Cookie": MFS_SPLASH_PREFERENCE_COOKIES})
    if len(html) < 500:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            f"Invalid response from MFS URL (length={len(html)})",
        )
    if "js-daily-holdings-page" not in html and "js-full-holding-table" not in html:
        if "individual investor" in html.lower() and "save my preferences" in html.lower():
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

    base_stem = f"{product_name}-Daily_Holdings_{securities_date}"
    return write_stamped_and_canonical_file(etf_symbol, base_stem, ".xls", xls_content)


def scrape_lsvd_csv(target_url: str, etf_symbol: str = "LSVD") -> Path:
    """
    Scrapes LSVD (https://www.lsvasset.com/disciplined-value-etf/):
    Locates the "All Fund Holdings CSV Download" link and downloads LSVD-holdings.csv.
    """
    if target_url.lower().endswith(".csv"):
        csv_url = target_url
    else:
        page_html = http_get_text(target_url)
        soup = BeautifulSoup(page_html, "html.parser")
        csv_link = None
        for a in soup.find_all("a"):
            txt = " ".join(a.get_text(" ", strip=True).split()).lower()
            href = a.get("href", "")
            if "all fund holdings csv download" in txt or "lsvd-holdings.csv" in href.lower():
                csv_link = href
                break
        if not csv_link:
            raise ScraperBotError(
                KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
                'Could not locate "All Fund Holdings CSV Download" link on LSVD page.',
            )
        csv_url = urllib.parse.urljoin(target_url, csv_link)

    csv_text = http_get_text(csv_url)
    if "Ticker" not in csv_text and "ISIN" not in csv_text:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            "Downloaded LSVD CSV is missing expected header columns (Name, Ticker, ISIN).",
        )
    return write_stamped_and_canonical_file(etf_symbol, "LSVD-holdings", ".csv", csv_text)


def scrape_vflo_csv(target_url: str, etf_symbol: str = "VFLO") -> Path:
    """
    Scrapes VFLO (https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-free-cash-flow-etf):
    Verifies the "All Holdings" link (#allholdingsCSVExport), calls the VictoryShares AllHoldings API,
    and generates VictorySharesFreeCashFlowETF_<MM_DD_YYYY>.csv.
    """
    page_html = http_get_text(target_url)
    soup = BeautifulSoup(page_html, "html.parser")

    export_btn = soup.find("a", id="allholdingsCSVExport")
    if not export_btn or "all holdings" not in export_btn.get_text(" ", strip=True).lower():
        raise ScraperBotError(
            KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
            'Could not locate the "All Holdings" (#allholdingsCSVExport) link on VictoryShares page.',
        )

    cfg_input = soup.find("input", {"id": "productDetailConfigJson"})
    labels_input = soup.find("input", {"id": "productLabels"})
    api_key_input = soup.find("input", {"id": "fundApiKey"})
    if not cfg_input or not labels_input or not api_key_input:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            "VictoryShares page layout changed: missing productDetailConfigJson or fundApiKey inputs.",
        )

    try:
        cfg = json.loads(cfg_input["value"])
        labels = json.loads(labels_input["value"])
        api_key = api_key_input["value"]
        api_url = cfg["allholdings"]
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            f"Failed to parse VictoryShares holdings configuration JSON: {e}",
        ) from e

    raw_json = http_get_text(api_url, extra_headers={"x-api-key": api_key})
    try:
        arr_data = json.loads(raw_json)
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Invalid JSON returned from VictoryShares AllHoldings endpoint: {e}",
        ) from e

    if not isinstance(arr_data, list) or not arr_data:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            "VictoryShares AllHoldings returned zero rows.",
        )

    etf_data_div = soup.find("div", class_="etf-fund-data")
    active_tmpl = etf_data_div.get("data-active-etf-template", "false") if etf_data_div else "false"
    json_row = labels.get("etfActiveAllholdings") if active_tmpl == "true" else labels.get("etfPassiveAllholdings")
    if not json_row:
        json_row = {
            "as_of_date": "Date",
            "stock_symbol": "Stock Symbol",
            "etfname": "ETF Name",
            "isin": "ISIN",
            "holding_name": "Holding",
            "security_type": "Security Type",
            "shares": "Shares",
            "market_value": "Market Value",
            "portfolio_percentage": "Portfolio %",
        }

    fund_name = "VictoryShares Free Cash Flow ETF"
    header_line = ",".join(json_row.values())
    lines = [header_line]
    for item in arr_data:
        item["etfname"] = fund_name
        row_cells = []
        for k in json_row.keys():
            val = item.get(k)
            s_val = "" if val is None else str(val)
            row_cells.append(f'"{s_val}"')
        lines.append(",".join(row_cells) + ",")

    csv_content = "\r\n".join(lines) + "\r\n"
    date_part = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%m_%d_%Y")
    if arr_data and arr_data[0].get("as_of_date"):
        date_part = str(arr_data[0]["as_of_date"]).replace("/", "_")
    base_stem = f"VictorySharesFreeCashFlowETF_{date_part}"
    return write_stamped_and_canonical_file(etf_symbol, base_stem, ".csv", csv_content)


def scrape_ivv_xls(target_url: str, etf_symbol: str = "IVV") -> Path:
    """
    Scrapes IVV (https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf):
    Locates the "Data download" link and downloads iShares-Core-SP-500-ETF_fund.xls.
    """
    page_html = http_get_text(target_url)
    soup = BeautifulSoup(page_html, "html.parser")
    dl_href = None
    for a in soup.find_all("a"):
        txt = " ".join(a.get_text(" ", strip=True).split()).lower()
        href = a.get("href", "")
        if "data download" in txt or "component=funddownload" in href.lower():
            dl_href = href
            break

    if not dl_href:
        raise ScraperBotError(
            KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
            'Could not locate the "Data download" link on iShares IVV page.',
        )

    xls_url = urllib.parse.urljoin(target_url, dl_href)
    xls_content = http_get_text(xls_url, timeout=45)
    if "<ss:workbook" not in xls_content.lower() and "<table" not in xls_content.lower():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            "Downloaded iShares IVV file is not a valid Excel workbook.",
        )
    return write_stamped_and_canonical_file(etf_symbol, "iShares-Core-SP-500-ETF_fund", ".xls", xls_content)


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

        url_lower = target_url.lower()
        if sym == "LSVD" or "lsvasset.com" in url_lower:
            raw_file_path = scrape_lsvd_csv(target_url, etf_symbol=sym)
        elif sym == "VFLO" or "vcm.com" in url_lower:
            raw_file_path = scrape_vflo_csv(target_url, etf_symbol=sym)
        elif sym == "IVV" or "ishares.com" in url_lower or "blackrock.com" in url_lower:
            raw_file_path = scrape_ivv_xls(target_url, etf_symbol=sym)
        elif url_lower.endswith(".csv") or (file_fmt.upper() == "CSV" and "mfs.com" not in url_lower):
            csv_text = http_get_text(target_url)
            raw_file_path = write_stamped_and_canonical_file(sym, f"{sym}_Daily_Holdings", ".csv", csv_text)
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
