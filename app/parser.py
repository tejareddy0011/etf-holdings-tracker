import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from app.notifier import KnownErrorType, ScraperBotError


def normalize_date(raw_date: str) -> str:
    """Converts MM-DD-YY or YYYY-MM-DD into standard YYYY-MM-DD format."""
    raw = (raw_date or "").strip()
    if not raw:
        raise ValueError("Empty date string")
    for fmt in ("%Y-%m-%d", "%m-%d-%y", "%m-%d-%Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {raw}")


def parse_float(val: str) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[,$%\s]", "", str(val).strip())
    if cleaned in ("", "-", "N/A", "nan"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_csv_holdings(content: str, file_name: str, fallback_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses a CSV or TSV holdings file uploaded manually via the UI.
    Supports MFS CSV exports and standard holdings CSV schemas.
    """
    raw_date = fallback_date
    if not raw_date:
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{2,4})", file_name)
        if m:
            raw_date = m.group(1)
    holding_date = normalize_date(raw_date) if raw_date else datetime.now().strftime("%Y-%m-%d")

    reader = csv.reader(io.StringIO(content))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Uploaded file {file_name} has insufficient rows.",
        )

    header_idx = 0
    for idx, r in enumerate(rows):
        joined = " ".join(r).upper()
        if "TICKER" in joined or "CUSIP" in joined:
            header_idx = idx
            break

    headers = [h.strip().upper() for h in rows[header_idx]]

    def find_col(keywords: List[str], default_idx: int) -> int:
        for i, h in enumerate(headers):
            if any(k in h for k in keywords):
                return i
        return default_idx

    idx_cusip = find_col(["CUSIP", "SEDOL"], 0)
    idx_ticker = find_col(["TICKER", "SYMBOL"], 1)
    idx_name = find_col(["SECURITIES", "COMPANY", "NAME", "DESCRIPTION"], 2)
    idx_shares = find_col(["SHARES", "PAR AMOUNT", "QUANTITY"], 3)
    idx_value = find_col(["VALUE", "MARKET VALUE"], 4)
    idx_pct = find_col(["PERCENT", "WEIGHT", "%"], 5)
    idx_sector = find_col(["GICS", "SECTOR"], 6)
    idx_country = find_col(["COUNTRY"], 7)
    idx_date = find_col(["DATE"], -1)

    records: List[Dict[str, Any]] = []
    for r in rows[header_idx + 1 :]:
        if len(r) < 3:
            continue
        cusip = r[idx_cusip].strip() if idx_cusip < len(r) else ""
        ticker = r[idx_ticker].strip() if idx_ticker < len(r) else ""
        company_name = r[idx_name].strip() if idx_name < len(r) else ""
        shares_raw = r[idx_shares].strip() if idx_shares < len(r) else ""
        value_raw = r[idx_value].strip() if idx_value < len(r) else ""
        percent_raw = r[idx_pct].strip() if idx_pct < len(r) else ""
        gics_sector = r[idx_sector].strip() if idx_sector < len(r) else ""
        country = r[idx_country].strip() if idx_country < len(r) else ""

        row_date = holding_date
        if idx_date >= 0 and idx_date < len(r) and r[idx_date].strip():
            try:
                row_date = normalize_date(r[idx_date].strip())
            except ValueError:
                pass

        if not ticker and not cusip:
            continue

        records.append(
            {
                "holding_date": row_date,
                "ticker": ticker or cusip,
                "cusip": cusip,
                "company_name": company_name,
                "shares": parse_float(shares_raw),
                "shares_raw": shares_raw,
                "value": parse_float(value_raw),
                "value_raw": value_raw,
                "percent_net_assets": parse_float(percent_raw),
                "percent_raw": percent_raw,
                "gics_sector": gics_sector,
                "country": country,
            }
        )

    if not records:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Zero valid holding records found in {file_name}",
        )
    return records


def parse_mfs_xls_file(file_path: Path, fallback_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses the downloaded MFS Daily Fund Holdings Excel (.xls) file or uploaded CSV/XLS file.
    Extracts:
      - Date (YYYY-MM-DD)
      - Ticker
      - CUSIP
      - Company Name
      - Shares
      - Value
      - Percent of Net Assets
      - GICS sector
      - Country
    """
    if not file_path.exists():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded Excel file does not exist at {file_path}",
        )

    size = file_path.stat().st_size
    if size < 40:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded Excel file is empty or truncated ({size} bytes): {file_path.name}",
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Failed to read downloaded Excel file {file_path.name}: {e}",
        ) from e

    # Support manual CSV uploads seamlessly
    if file_path.suffix.lower() == ".csv" or ("<table" not in content.lower() and "," in content):
        return parse_csv_holdings(content, file_path.name, fallback_date=fallback_date)

    if "<table" not in content.lower() and "<tr" not in content.lower():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"File {file_path.name} does not contain valid MFS holdings table structure",
        )

    soup = BeautifulSoup(content, "html.parser")
    table = soup.find("table", class_="js-full-holding-table") or soup.find("table")
    if not table:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"No holdings table found inside {file_path.name}",
        )

    raw_date = fallback_date or (table.get("data-daily-holdings-securitiesdate") if hasattr(table, "get") else None)
    if not raw_date:
        m = re.search(r"\(on\s+(\d{2}-\d{2}-\d{2,4})\)", content)
        if m:
            raw_date = m.group(1)
    if not raw_date:
        m = re.search(r"(\d{2}-\d{2}-\d{2,4}|\d{4}-\d{2}-\d{2})", file_path.name)
        if m:
            raw_date = m.group(1)

    try:
        holding_date = normalize_date(raw_date) if raw_date else datetime.now().strftime("%Y-%m-%d")
    except ValueError as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Could not parse holdings date from {file_path.name}: {e}",
        ) from e

    rows = table.find_all("tr")
    if len(rows) < 3:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Holdings table in {file_path.name} has insufficient rows ({len(rows)})",
        )

    header_cols: List[str] = []
    data_start_idx = 0
    for idx, r in enumerate(rows):
        cells = [c.get_text(" ", strip=True) for c in r.find_all(["th", "td"])]
        joined = " ".join(cells).upper()
        if "CUSIP" in joined and "TICKER" in joined:
            header_cols = cells
            data_start_idx = idx + 1
            break

    if not header_cols:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Missing required header columns (CUSIP, Ticker, Securities) in {file_path.name}",
        )

    records: List[Dict[str, Any]] = []
    for r in rows[data_start_idx:]:
        if "footer-text" in (r.get("class") or []):
            continue
        tds = r.find_all("td")
        if len(tds) < 6:
            continue
        cols = [c.get_text(" ", strip=True) for c in tds]

        cusip = cols[0].strip()
        ticker = cols[1].strip()
        company_name = cols[2].strip()
        shares_raw = cols[3].strip()
        value_raw = cols[4].strip()
        percent_raw = cols[5].strip()

        if len(cols) >= 11:
            gics_sector = cols[9].strip()
            country = cols[10].strip()
        elif len(cols) >= 8:
            gics_sector = cols[6].strip()
            country = cols[7].strip()
        else:
            gics_sector = ""
            country = ""

        if not cusip and not ticker and not company_name:
            continue
        if len(cusip) > 25 or "global industry classification" in cusip.lower():
            continue

        records.append(
            {
                "holding_date": holding_date,
                "ticker": ticker if ticker else cusip,
                "cusip": cusip,
                "company_name": company_name,
                "shares": parse_float(shares_raw),
                "shares_raw": shares_raw,
                "value": parse_float(value_raw),
                "value_raw": value_raw,
                "percent_net_assets": parse_float(percent_raw),
                "percent_raw": percent_raw,
                "gics_sector": gics_sector,
                "country": country,
            }
        )

    if not records:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Zero valid holding records extracted from {file_path.name}",
        )

    return records
