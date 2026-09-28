import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from app.config import (
    ALERT_RECIPIENT_EMAIL,
    DB_PATH,
    DEFAULT_ETF_SYMBOL,
    PRESET_ETF_TARGETS,
)

CASH_TICKERS = {"CASH", "CASH_USD", "CASHUSD", "SWEEP", "-", "85749270"}


def is_cash_or_sweep(ticker: str, cusip: str, company_name: str, gics_sector: str) -> bool:
    t = (ticker or "").strip().upper()
    c = (cusip or "").strip().upper()
    name = (company_name or "").strip().upper()
    sec = (gics_sector or "").strip()
    if not t or t in CASH_TICKERS or c in CASH_TICKERS:
        return True
    if t.isdigit():
        return True
    if "CASH & CASH EQUIVALENTS" in name or "MONEY MARKET" in name:
        return True
    if sec in ("-", "") and "CASH" in name:
        return True
    return False


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS etf_targets (
                etf_symbol TEXT PRIMARY KEY,
                etf_name TEXT NOT NULL,
                daily_url TEXT NOT NULL,
                historical_base_url TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                bot_state TEXT NOT NULL DEFAULT 'ACTIVE',
                file_format TEXT NOT NULL DEFAULT 'XLS',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_symbol TEXT NOT NULL DEFAULT 'MFSV',
                holding_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                cusip TEXT,
                company_name TEXT,
                shares REAL,
                shares_raw TEXT,
                value REAL,
                value_raw TEXT,
                percent_net_assets REAL,
                percent_raw TEXT,
                gics_sector TEXT,
                country TEXT,
                is_cash INTEGER NOT NULL DEFAULT 0,
                source_file TEXT,
                downloaded_at TEXT NOT NULL,
                UNIQUE(etf_symbol, holding_date, ticker, cusip)
            );

            CREATE INDEX IF NOT EXISTS idx_holdings_date ON holdings(etf_symbol, holding_date);
            CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(etf_symbol, ticker);

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_symbol TEXT NOT NULL DEFAULT 'MFSV',
                trigger_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                holding_date TEXT,
                status TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT,
                raw_file_path TEXT,
                records_parsed INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_alert_logs_date ON scrape_runs(started_at);

            CREATE TABLE IF NOT EXISTS alert_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                error_date TEXT NOT NULL,
                error_type TEXT NOT NULL,
                error_details TEXT,
                delivery_status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Ensure columns exist on older DBs
        existing_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(etf_targets)").fetchall()
        }
        if "bot_state" not in existing_cols:
            conn.execute("ALTER TABLE etf_targets ADD COLUMN bot_state TEXT NOT NULL DEFAULT 'ACTIVE'")
        if "file_format" not in existing_cols:
            conn.execute("ALTER TABLE etf_targets ADD COLUMN file_format TEXT NOT NULL DEFAULT 'XLS'")

        now = datetime.now(timezone.utc).isoformat()
        defaults = {
            "scraper_state": "ACTIVE",
            "schedule_time_pt": "19:00",
            "alert_email": ALERT_RECIPIENT_EMAIL,
            "scraper_mode": "AUTO",
            "proxy_enabled": "false",
        }
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO admin_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now),
            )
        conn.execute(
            "UPDATE admin_settings SET value = ?, updated_at = ? WHERE key = 'alert_email'",
            (ALERT_RECIPIENT_EMAIL, now),
        )
        for etf in PRESET_ETF_TARGETS:
            conn.execute(
                """
                INSERT OR IGNORE INTO etf_targets (
                    etf_symbol, etf_name, daily_url, historical_base_url, is_active, bot_state, file_format, created_at
                ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', 'XLS', ?)
                """,
                (
                    etf["etf_symbol"],
                    etf["etf_name"],
                    etf["daily_url"],
                    etf.get("historical_base_url", ""),
                    etf.get("is_active", 1),
                    now,
                ),
            )


def get_etf_targets() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.etf_symbol, t.etf_name, t.daily_url, t.historical_base_url,
                   t.is_active, COALESCE(t.bot_state, 'ACTIVE') AS bot_state,
                   COALESCE(t.file_format, 'XLS') AS file_format,
                   COUNT(DISTINCT h.holding_date) AS snapshot_dates_count
            FROM etf_targets t
            LEFT JOIN holdings h ON h.etf_symbol = t.etf_symbol
            GROUP BY t.etf_symbol
            ORDER BY CASE WHEN t.etf_symbol = 'MFSV' THEN 0 ELSE 1 END, t.etf_symbol ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_etf_target(etf_symbol: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM etf_targets WHERE etf_symbol = ?",
            (etf_symbol.strip().upper(),),
        ).fetchone()
        return dict(row) if row else None


def set_etf_bot_state(etf_symbol: str, bot_state: str) -> Optional[Dict[str, Any]]:
    sym = etf_symbol.strip().upper()
    state = bot_state.strip().upper()
    if state not in {"ACTIVE", "PAUSED", "STOPPED"}:
        raise ValueError(f"Invalid bot_state: {bot_state}")
    is_active = 1 if state == "ACTIVE" else 0
    with get_conn() as conn:
        conn.execute(
            "UPDATE etf_targets SET bot_state = ?, is_active = ? WHERE etf_symbol = ?",
            (state, is_active, sym),
        )
    return get_etf_target(sym)


def add_or_update_etf_target(
    etf_symbol: str,
    etf_name: str,
    daily_url: str,
    historical_base_url: str = "",
    file_format: str = "XLS",
) -> Dict[str, Any]:
    sym = etf_symbol.strip().upper()
    if not sym:
        raise ValueError("ETF symbol is required")
    if not daily_url.strip():
        raise ValueError("Daily Holdings URL is required")
    if not historical_base_url and "/daily-holdings/" in daily_url:
        historical_base_url = daily_url.split("#")[0].replace("/daily-holdings/", "/full-holdings/").replace(".html", "")
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO etf_targets (
                etf_symbol, etf_name, daily_url, historical_base_url, is_active, bot_state, file_format, created_at
            )
            VALUES (?, ?, ?, ?, 1, 'ACTIVE', ?, ?)
            ON CONFLICT(etf_symbol) DO UPDATE SET
                etf_name = excluded.etf_name,
                daily_url = excluded.daily_url,
                historical_base_url = excluded.historical_base_url,
                file_format = excluded.file_format
            """,
            (sym, etf_name.strip() or sym, daily_url.strip(), historical_base_url.strip(), file_format.upper(), now),
        )
    return get_etf_target(sym) or {}


def get_settings() -> Dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM admin_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def update_setting(key: str, value: str) -> Dict[str, str]:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO admin_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
    return get_settings()


def save_holdings(
    records: List[Dict[str, Any]],
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    source_file: str = "",
) -> int:
    if not records:
        return 0
    sym = etf_symbol.strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    holding_date = records[0]["holding_date"]
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM holdings WHERE etf_symbol = ? AND holding_date = ?",
            (sym, holding_date),
        )
        count = 0
        for r in records:
            cash_flag = 1 if is_cash_or_sweep(
                r.get("ticker", ""),
                r.get("cusip", ""),
                r.get("company_name", ""),
                r.get("gics_sector", ""),
            ) else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO holdings (
                    etf_symbol, holding_date, ticker, cusip, company_name,
                    shares, shares_raw, value, value_raw,
                    percent_net_assets, percent_raw, gics_sector, country,
                    is_cash, source_file, downloaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sym,
                    r["holding_date"],
                    r["ticker"],
                    r.get("cusip", ""),
                    r.get("company_name", ""),
                    r.get("shares"),
                    r.get("shares_raw", ""),
                    r.get("value"),
                    r.get("value_raw", ""),
                    r.get("percent_net_assets"),
                    r.get("percent_raw", ""),
                    r.get("gics_sector", ""),
                    r.get("country", ""),
                    cash_flag,
                    source_file,
                    now,
                ),
            )
            count += 1
        return count


def log_scrape_start(trigger_type: str = "SCHEDULED", etf_symbol: str = DEFAULT_ETF_SYMBOL) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO scrape_runs (etf_symbol, trigger_type, started_at, status)
            VALUES (?, ?, ?, 'RUNNING')
            """,
            (etf_symbol.strip().upper(), trigger_type, now),
        )
        return int(cur.lastrowid)


def log_scrape_finish(
    run_id: int,
    status: str,
    holding_date: Optional[str] = None,
    raw_file_path: Optional[str] = None,
    records_parsed: int = 0,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE scrape_runs
            SET completed_at = ?, status = ?, holding_date = ?, raw_file_path = ?,
                records_parsed = ?, error_type = ?, error_message = ?
            WHERE id = ?
            """,
            (
                now,
                status,
                holding_date,
                raw_file_path,
                records_parsed,
                error_type,
                error_message,
                run_id,
            ),
        )


def log_alert(
    recipient_email: str,
    error_date: str,
    error_type: str,
    error_details: str,
    delivery_status: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO alert_logs (created_at, recipient_email, error_date, error_type, error_details, delivery_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, recipient_email, error_date, error_type, error_details, delivery_status),
        )
        return int(cur.lastrowid)


def get_available_dates(etf_symbol: str = DEFAULT_ETF_SYMBOL) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT holding_date,
                   COUNT(*) AS total_rows,
                   SUM(CASE WHEN is_cash = 0 THEN 1 ELSE 0 END) AS stock_positions,
                   SUM(COALESCE(value, 0)) AS total_market_value,
                   MAX(source_file) AS source_file
            FROM holdings
            WHERE etf_symbol = ?
            GROUP BY holding_date
            ORDER BY holding_date DESC
            """,
            (etf_symbol.strip().upper(),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_holdings_by_date(
    holding_date: str,
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    exclude_cash: bool = False,
) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        query = """
            SELECT etf_symbol, holding_date, ticker, cusip, company_name, shares, shares_raw,
                   value, value_raw, percent_net_assets, percent_raw,
                   gics_sector, country, is_cash, source_file
            FROM holdings
            WHERE etf_symbol = ? AND holding_date = ?
        """
        params: List[Any] = [etf_symbol.strip().upper(), holding_date]
        if exclude_cash:
            query += " AND is_cash = 0"
        query += " ORDER BY COALESCE(percent_net_assets, 0) DESC, ticker ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def compare_holdings(
    date_a: str,
    date_b: str,
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    exclude_cash: bool = True,
) -> Dict[str, Any]:
    sym = etf_symbol.strip().upper()
    rows_a = get_holdings_by_date(date_a, etf_symbol=sym, exclude_cash=exclude_cash)
    rows_b = get_holdings_by_date(date_b, etf_symbol=sym, exclude_cash=exclude_cash)

    map_a: Dict[str, Dict[str, Any]] = {}
    for r in rows_a:
        t = r["ticker"].strip().upper()
        if t not in map_a:
            map_a[t] = r

    map_b: Dict[str, Dict[str, Any]] = {}
    for r in rows_b:
        t = r["ticker"].strip().upper()
        if t not in map_b:
            map_b[t] = r

    tickers_a = set(map_a.keys())
    tickers_b = set(map_b.keys())

    added_tickers = sorted(tickers_b - tickers_a)
    removed_tickers = sorted(tickers_a - tickers_b)
    common_tickers = sorted(tickers_a & tickers_b)

    newly_added = [map_b[t] for t in added_tickers]
    newly_added.sort(key=lambda x: x.get("percent_net_assets") or 0.0, reverse=True)

    completely_removed = [map_a[t] for t in removed_tickers]
    completely_removed.sort(key=lambda x: x.get("percent_net_assets") or 0.0, reverse=True)

    retained_changes = []
    for t in common_tickers:
        a = map_a[t]
        b = map_b[t]
        shares_a = a.get("shares") or 0.0
        shares_b = b.get("shares") or 0.0
        pct_a = a.get("percent_net_assets") or 0.0
        pct_b = a_pct_b = b.get("percent_net_assets") or 0.0
        retained_changes.append(
            {
                "ticker": t,
                "cusip": b.get("cusip") or a.get("cusip"),
                "company_name": b.get("company_name") or a.get("company_name"),
                "gics_sector": b.get("gics_sector") or a.get("gics_sector"),
                "country": b.get("country") or a.get("country"),
                "shares_date_a": shares_a,
                "shares_date_b": shares_b,
                "shares_diff": round(shares_b - shares_a, 2),
                "value_date_a": a.get("value") or 0.0,
                "value_date_b": b.get("value") or 0.0,
                "percent_date_a": pct_a,
                "percent_date_b": a_pct_b,
                "percent_diff": round(a_pct_b - pct_a, 2),
            }
        )
    retained_changes.sort(key=lambda x: abs(x["percent_diff"]), reverse=True)

    return {
        "etf_symbol": sym,
        "date_a": date_a,
        "date_b": date_b,
        "exclude_cash": exclude_cash,
        "summary": {
            "date_a_count": len(map_a),
            "date_b_count": len(map_b),
            "newly_added_count": len(newly_added),
            "completely_removed_count": len(completely_removed),
            "retained_count": len(retained_changes),
        },
        "newly_added": newly_added,
        "completely_removed": completely_removed,
        "retained_changes": retained_changes,
    }


def get_recent_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
