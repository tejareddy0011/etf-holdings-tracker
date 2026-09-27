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
    cleaned = re.sub(r"[,$%\s]", "", val.strip())
    if cleaned in ("", "-", "N/A", "nan"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_mfs_xls_file(file_path: Path, fallback_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses the downloaded MFS Daily Fund Holdings Excel (.xls) file.
    MFS's 'Download Daily Fund Holdings' button uses jQuery table2excel, which outputs
    an HTML-formatted .xls spreadsheet containing the holdings table.
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
    Raises ScraperBotError(KnownErrorType.EXCEL_FAILED_OR_CORRUPTED) if file is missing or corrupted.
    """
    if not file_path.exists():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded Excel file does not exist at {file_path}",
        )

    size = file_path.stat().st_size
    if size < 100:
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

    # Determine holding date from table attribute, header text "(on MM-DD-YY)", filename, or fallback
    raw_date = table.get("data-daily-holdings-securitiesdate") if hasattr(table, "get") else None
    if not raw_date:
        m = re.search(r"\(on\s+(\d{2}-\d{2}-\d{2,4})\)", content)
        if m:
            raw_date = m.group(1)
    if not raw_date:
        m = re.search(r"(\d{2}-\d{2}-\d{2,4}|\d{4}-\d{2}-\d{2})", file_path.name)
        if m:
            raw_date = m.group(1)
    if not raw_date and fallback_date:
        raw_date = fallback_date

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

    # Identify column indices from header row
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
        if len(tds) < 8:
            continue
        cols = [c.get_text(" ", strip=True) for c in tds]

        # Daily Holdings table has 8 columns:
        #   0: CUSIP/SEDOL, 1: Ticker, 2: Securities, 3: Shares, 4: Value, 5: % Net Assets, 6: GICS, 7: Country
        # Full/Historical Holdings table has 11 columns:
        #   0: CUSIP/SEDOL, 1: Ticker, 2: Securities, 3: Shares, 4: Value, 5: % Net Assets,
        #   6: Equiv Value, 7: Equiv %, 8: Market Cap, 9: GICS, 10: Country
        cusip = cols[0].strip()
        ticker = cols[1].strip()
        company_name = cols[2].strip()
        shares_raw = cols[3].strip()
        value_raw = cols[4].strip()
        percent_raw = cols[5].strip()

        if len(cols) >= 11:
            gics_sector = cols[9].strip()
            country = cols[10].strip()
        else:
            gics_sector = cols[6].strip()
            country = cols[7].strip()

        if not cusip and not ticker and not company_name:
            continue
        # Skip footer disclosure rows if any
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
