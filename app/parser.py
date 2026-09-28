import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from app.notifier import KnownErrorType, ScraperBotError


def normalize_date(raw_date: str) -> str:
    """Converts MM-DD-YY, MM/DD/YYYY, Mon DD, YYYY, or YYYY-MM-DD into standard YYYY-MM-DD format."""
    raw = (raw_date or "").strip()
    if not raw:
        raise ValueError("Empty date string")
    raw_underscore = raw.replace("_", "/")
    for candidate in (raw, raw_underscore):
        for fmt in (
            "%Y-%m-%d",
            "%m-%d-%y",
            "%m-%d-%Y",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%b %d, %Y",
            "%B %d, %Y",
        ):
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    raise ValueError(f"Unrecognized date format: {raw}")


def parse_float(val: str) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[,$%\s]", "", str(val).strip())
    if cleaned in ("", "-", "--", "N/A", "nan"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_ishares_xml_xls(content: str, file_name: str, fallback_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses iShares SpreadsheetML XML (.xls) workbooks (e.g., iShares-Core-SP-500-ETF_fund.xls).
    Extracts the 'Holdings' worksheet rows.
    """
    start = content.find('<ss:Worksheet ss:Name="Holdings">')
    if start == -1:
        start = content.find("<ss:Worksheet")
    if start == -1:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Worksheet 'Holdings' missing from iShares XML workbook {file_name}",
        )
    end = content.find("</ss:Worksheet>", start)
    sheet_xml = content[start:end] if end != -1 else content[start:]

    row_blocks = re.findall(r"<ss:Row[^>]*>(.*?)</ss:Row>", sheet_xml, re.S)
    parsed_rows: List[List[str]] = []
    for rb in row_blocks:
        cells = [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<ss:Data[^>]*>(.*?)</ss:Data>", rb, re.S)
        ]
        if any(cells):
            parsed_rows.append(cells)

    raw_date = fallback_date
    header_idx = -1
    for idx, r in enumerate(parsed_rows[:20]):
        if not raw_date and len(r) == 1:
            try:
                raw_date = normalize_date(r[0])
            except ValueError:
                pass
        if not raw_date and len(r) >= 2 and "holdings as of" in r[0].lower():
            try:
                raw_date = normalize_date(r[1])
            except ValueError:
                pass
        joined_upper = " ".join(r).upper()
        if "TICKER" in joined_upper and ("MARKET VALUE" in joined_upper or "WEIGHT" in joined_upper):
            header_idx = idx
            break

    if header_idx == -1:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Could not find Holdings header row inside {file_name}",
        )

    holding_date = normalize_date(raw_date) if raw_date else datetime.now().strftime("%Y-%m-%d")
    headers = [h.strip().upper() for h in parsed_rows[header_idx]]

    def col_idx(keys: List[str], default: int = -1) -> int:
        for i, h in enumerate(headers):
            if any(k == h or k in h for k in keys):
                return i
        return default

    i_ticker = col_idx(["TICKER", "SYMBOL"], 0)
    i_name = col_idx(["NAME", "SECURITY", "COMPANY"], 1)
    i_sector = col_idx(["SECTOR"], 2)
    i_val = col_idx(["MARKET VALUE", "VALUE"], 4)
    i_pct = col_idx(["WEIGHT", "PERCENT", "%"], 5)
    i_shares = col_idx(["QUANTITY", "SHARES"], 7)
    i_country = col_idx(["LOCATION", "COUNTRY"], 9)
    i_cusip = col_idx(["CUSIP", "ISIN", "SEDOL"], -1)

    records: List[Dict[str, Any]] = []
    for r in parsed_rows[header_idx + 1 :]:
        if len(r) < 4:
            continue
        ticker = r[i_ticker].strip() if 0 <= i_ticker < len(r) else ""
        company_name = r[i_name].strip() if 0 <= i_name < len(r) else ""
        gics_sector = r[i_sector].strip() if 0 <= i_sector < len(r) else ""
        value_raw = r[i_val].strip() if 0 <= i_val < len(r) else ""
        percent_raw = r[i_pct].strip() if 0 <= i_pct < len(r) else ""
        shares_raw = r[i_shares].strip() if 0 <= i_shares < len(r) else ""
        country = r[i_country].strip() if 0 <= i_country < len(r) else ""
        cusip = r[i_cusip].strip() if 0 <= i_cusip < len(r) else ""

        if not ticker and not company_name:
            continue

        records.append(
            {
                "holding_date": holding_date,
                "ticker": ticker or company_name[:12].upper(),
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
            f"Zero valid holding records extracted from iShares workbook {file_name}",
        )
    return records


def parse_csv_holdings(content: str, file_name: str, fallback_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses a CSV or TSV holdings file (supports LSVD, VFLO, MFS, and custom CSV schemas).
    """
    raw_date = fallback_date
    if not raw_date:
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{2}[_-]\d{2}[_-]\d{2,4})", file_name)
        if m:
            try:
                raw_date = normalize_date(m.group(1))
            except ValueError:
                raw_date = None
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
        if "TICKER" in joined or "CUSIP" in joined or "STOCK SYMBOL" in joined or "ISIN" in joined:
            header_idx = idx
            break

    headers = [h.strip().upper() for h in rows[header_idx]]

    def find_col(keywords: List[str], exact_first: bool = True) -> int:
        if exact_first:
            for i, h in enumerate(headers):
                if h in keywords:
                    return i
        for i, h in enumerate(headers):
            if any(k in h for k in keywords):
                return i
        return -1

    idx_cusip = find_col(["CUSIP", "ISIN", "SEDOL"])
    idx_ticker = find_col(["TICKER", "STOCK SYMBOL", "SYMBOL"])
    idx_name = find_col(["HOLDING", "SECURITIES", "COMPANY NAME", "NAME", "COMPANY", "DESCRIPTION"])
    idx_shares = find_col(["NUMBER OF SHARES", "SHARES", "PAR AMOUNT", "QUANTITY"])
    idx_value = find_col(["MARKET VALUE", "VALUE"])
    idx_pct = find_col(["% OF NAV", "PORTFOLIO %", "PERCENT OF NET ASSETS", "PERCENT", "WEIGHT", "%"])
    idx_sector = find_col(["GICS SECTOR", "GICS", "SECTOR", "SECURITY TYPE"])
    idx_country = find_col(["COUNTRY", "LOCATION"])
    idx_date = find_col(["DATE", "AS OF DATE"])

    records: List[Dict[str, Any]] = []
    for r in rows[header_idx + 1 :]:
        if len(r) < 3:
            continue
        cusip = r[idx_cusip].strip() if 0 <= idx_cusip < len(r) else ""
        ticker = r[idx_ticker].strip() if 0 <= idx_ticker < len(r) else ""
        company_name = r[idx_name].strip() if 0 <= idx_name < len(r) else ""
        shares_raw = r[idx_shares].strip() if 0 <= idx_shares < len(r) else ""
        value_raw = r[idx_value].strip() if 0 <= idx_value < len(r) else ""
        percent_raw = r[idx_pct].strip() if 0 <= idx_pct < len(r) else ""
        gics_sector = r[idx_sector].strip() if 0 <= idx_sector < len(r) else ""
        country = r[idx_country].strip() if 0 <= idx_country < len(r) else ""

        row_date = holding_date
        if 0 <= idx_date < len(r) and r[idx_date].strip():
            try:
                row_date = normalize_date(r[idx_date].strip())
            except ValueError:
                pass

        if not ticker and not cusip and not company_name:
            continue

        resolved_ticker = ticker or cusip or ("CASH" if "CASH" in company_name.upper() else company_name[:12].upper())

        records.append(
            {
                "holding_date": row_date,
                "ticker": resolved_ticker,
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
    Parses downloaded ETF holding files across all 4 supported formats:
      1. MFS HTML Table (.xls)
      2. iShares SpreadsheetML XML (.xls)
      3. LSV Asset Management (.csv)
      4. VictoryShares Free Cash Flow ETF (.csv)
    """
    if not file_path.exists():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded file does not exist at {file_path}",
        )

    size = file_path.stat().st_size
    if size < 40:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Downloaded file is empty or truncated ({size} bytes): {file_path.name}",
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"Failed to read downloaded file {file_path.name}: {e}",
        ) from e

    # 1. iShares SpreadsheetML XML (.xls)
    if "<ss:workbook" in content.lower() or "<ss:worksheet" in content.lower():
        return parse_ishares_xml_xls(content, file_path.name, fallback_date=fallback_date)

    # 2. CSV format (LSVD, VFLO, or manual CSV uploads)
    if file_path.suffix.lower() == ".csv" or ("<table" not in content.lower() and "," in content):
        return parse_csv_holdings(content, file_path.name, fallback_date=fallback_date)

    # 3. MFS HTML-based .xls
    if "<table" not in content.lower() and "<tr" not in content.lower():
        raise ScraperBotError(
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
            f"File {file_path.name} does not contain valid holdings table structure",
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
