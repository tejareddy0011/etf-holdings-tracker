import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from app.config import DEFAULT_ETF_SYMBOL, RAW_FILES_DIR
from app.database import (
    compare_holdings,
    get_available_dates,
    get_holdings_by_date,
    get_recent_alerts,
    get_recent_runs,
    init_db,
    update_setting,
)
from app.scheduler import scheduler
from app.scraper import seed_historical_mfs_dates

STATIC_DIR = Path(__file__).resolve().parent / "static"


def list_raw_files() -> list:
    files = []
    for p in sorted(RAW_FILES_DIR.glob("*.xls*"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = p.stat()
        files.append(
            {
                "filename": p.name,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return files


class ETFReportingHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index_file = STATIC_DIR / "index.html"
            content = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/status":
            dates = get_available_dates(DEFAULT_ETF_SYMBOL)
            self._send_json(
                {
                    "scheduler": scheduler.get_status(),
                    "available_dates": dates,
                    "recent_runs": get_recent_runs(15),
                    "recent_alerts": get_recent_alerts(15),
                    "raw_files": list_raw_files(),
                }
            )
            return

        if path == "/api/compare":
            dates = get_available_dates(DEFAULT_ETF_SYMBOL)
            default_b = dates[0]["holding_date"] if len(dates) >= 1 else ""
            default_a = dates[1]["holding_date"] if len(dates) >= 2 else default_b
            date_a = qs.get("date_a", [default_a])[0]
            date_b = qs.get("date_b", [default_b])[0]
            exclude_cash = qs.get("exclude_cash", ["true"])[0].lower() != "false"
            if not date_a or not date_b:
                self._send_json({"error": "Both date_a and date_b are required"}, status=400)
                return
            result = compare_holdings(
                date_a=date_a,
                date_b=date_b,
                etf_symbol=DEFAULT_ETF_SYMBOL,
                exclude_cash=exclude_cash,
            )
            self._send_json(result)
            return

        if path == "/api/holdings":
            dates = get_available_dates(DEFAULT_ETF_SYMBOL)
            default_date = dates[0]["holding_date"] if dates else ""
            target_date = qs.get("date", [default_date])[0]
            exclude_cash = qs.get("exclude_cash", ["false"])[0].lower() == "true"
            rows = get_holdings_by_date(target_date, etf_symbol=DEFAULT_ETF_SYMBOL, exclude_cash=exclude_cash)
            self._send_json(
                {
                    "etf_symbol": DEFAULT_ETF_SYMBOL,
                    "holding_date": target_date,
                    "count": len(rows),
                    "holdings": rows,
                }
            )
            return

        if path.startswith("/api/raw-files/"):
            fname = Path(urllib.parse.unquote(path.replace("/api/raw-files/", ""))).name
            fpath = RAW_FILES_DIR / fname
            if not fpath.exists() or not fpath.is_file():
                self._send_json({"error": "File not found"}, status=404)
                return
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.ms-excel")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/admin/toggle":
            state = body.get("state", "ACTIVE")
            try:
                status = scheduler.set_admin_state(state)
                self._send_json({"ok": True, "scheduler": status})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/settings":
            if "alert_email" in body and body["alert_email"]:
                update_setting("alert_email", str(body["alert_email"]).strip())
            if "schedule_time_pt" in body and body["schedule_time_pt"]:
                update_setting("schedule_time_pt", str(body["schedule_time_pt"]).strip())
            self._send_json({"ok": True, "scheduler": scheduler.get_status()})
            return

        if path == "/api/admin/scrape-now":
            res = scheduler.run_now(trigger_type="MANUAL_UI")
            self._send_json(res)
            return

        if path == "/api/admin/seed-history":
            res = seed_historical_mfs_dates()
            self._send_json({"ok": True, "seeded": res})
            return

        if path == "/api/admin/test-error":
            error_type = body.get("error_type", "Target URL unreachable or layout changed")
            res = scheduler.run_now(trigger_type="DIAGNOSTIC_ERROR_TEST", simulate_error=error_type)
            self._send_json(res)
            return

        self._send_json({"error": "Endpoint not found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy access logs
        pass


def bootstrap_initial_data() -> None:
    init_db()
    dates = get_available_dates(DEFAULT_ETF_SYMBOL)
    if not dates:
        print("Bootstrapping live MFS Active Value ETF (MFSV) daily & historical holdings...")
        scheduler.run_now(trigger_type="INITIAL_BOOTSTRAP")
        seed_historical_mfs_dates(["2026-08-31", "2026-07-31", "2026-06-30", "2026-03-31", "2025-12-31"])


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    bootstrap_initial_data()
    scheduler.start_background_loop()
    server = ThreadingHTTPServer((host, port), ETFReportingHandler)
    print(f"MFS ETF Scraper & Reporting Server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    run_server(host="127.0.0.1", port=port)
