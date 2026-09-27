import json
import os
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from app.config import DEFAULT_ETF_SYMBOL, RAW_FILES_DIR, SCHEDULE_TIMEZONE
from app.database import (
    add_or_update_etf_target,
    compare_holdings,
    get_available_dates,
    get_etf_targets,
    get_holdings_by_date,
    get_recent_alerts,
    get_recent_runs,
    init_db,
    log_scrape_finish,
    log_scrape_start,
    save_holdings,
    update_setting,
)
from app.parser import parse_mfs_xls_file
from app.scheduler import scheduler
from app.scraper import seed_historical_mfs_dates

STATIC_DIR = Path(__file__).resolve().parent / "static"


def list_raw_files() -> list:
    files = []
    for p in sorted(
        list(RAW_FILES_DIR.glob("*.xls*")) + list(RAW_FILES_DIR.glob("*.csv")),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
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
            etf = qs.get("etf", [DEFAULT_ETF_SYMBOL])[0].strip().upper()
            dates = get_available_dates(etf)
            self._send_json(
                {
                    "selected_etf": etf,
                    "etf_targets": get_etf_targets(),
                    "scheduler": scheduler.get_status(),
                    "available_dates": dates,
                    "recent_runs": get_recent_runs(15),
                    "recent_alerts": get_recent_alerts(15),
                    "raw_files": list_raw_files(),
                }
            )
            return

        if path == "/api/compare":
            etf = qs.get("etf", [DEFAULT_ETF_SYMBOL])[0].strip().upper()
            dates = get_available_dates(etf)
            default_b = dates[0]["holding_date"] if len(dates) >= 1 else ""
            default_a = dates[1]["holding_date"] if len(dates) >= 2 else default_b
            date_a = qs.get("date_a", [default_a])[0]
            date_b = qs.get("date_b", [default_b])[0]
            exclude_cash = qs.get("exclude_cash", ["true"])[0].lower() != "false"
            if not date_a or not date_b:
                self._send_json(
                    {
                        "etf_symbol": etf,
                        "date_a": date_a,
                        "date_b": date_b,
                        "summary": {
                            "date_a_count": 0,
                            "date_b_count": 0,
                            "newly_added_count": 0,
                            "completely_removed_count": 0,
                            "retained_count": 0,
                        },
                        "newly_added": [],
                        "completely_removed": [],
                        "retained_changes": [],
                    }
                )
                return
            result = compare_holdings(
                date_a=date_a,
                date_b=date_b,
                etf_symbol=etf,
                exclude_cash=exclude_cash,
            )
            self._send_json(result)
            return

        if path == "/api/holdings":
            etf = qs.get("etf", [DEFAULT_ETF_SYMBOL])[0].strip().upper()
            dates = get_available_dates(etf)
            default_date = dates[0]["holding_date"] if dates else ""
            target_date = qs.get("date", [default_date])[0]
            exclude_cash = qs.get("exclude_cash", ["false"])[0].lower() == "true"
            rows = get_holdings_by_date(target_date, etf_symbol=etf, exclude_cash=exclude_cash) if target_date else []
            self._send_json(
                {
                    "etf_symbol": etf,
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

        if path == "/api/admin/add-etf":
            try:
                etf_sym = str(body.get("etf_symbol", "")).strip().upper()
                etf_name = str(body.get("etf_name", "")).strip()
                daily_url = str(body.get("daily_url", "")).strip()
                target = add_or_update_etf_target(etf_sym, etf_name, daily_url)
                self._send_json({"ok": True, "etf": target, "etf_targets": get_etf_targets()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/scrape-now":
            etf = str(body.get("etf_symbol", DEFAULT_ETF_SYMBOL)).strip().upper()
            res = scheduler.run_now(trigger_type="MANUAL_UI", etf_symbol=etf)
            self._send_json(res)
            return

        if path == "/api/admin/seed-history":
            etf = str(body.get("etf_symbol", DEFAULT_ETF_SYMBOL)).strip().upper()
            res = seed_historical_mfs_dates(etf_symbol=etf)
            self._send_json({"ok": True, "etf_symbol": etf, "seeded": res})
            return

        if path == "/api/admin/upload-file":
            etf = str(body.get("etf_symbol", DEFAULT_ETF_SYMBOL)).strip().upper()
            filename = Path(str(body.get("filename", "uploaded_holdings.xls"))).name
            content = str(body.get("content", ""))
            override_date = str(body.get("holding_date", "")).strip() or None

            if not content:
                self._send_json({"ok": False, "error": "File content is empty"}, status=400)
                return

            run_id = log_scrape_start(trigger_type="MANUAL_FILE_UPLOAD", etf_symbol=etf)
            try:
                stamp = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d")
                save_name = f"{Path(filename).stem}_uploaded_{stamp}{Path(filename).suffix or '.xls'}"
                dest = RAW_FILES_DIR / save_name
                dest.write_text(content, encoding="utf-8")

                records = parse_mfs_xls_file(dest, fallback_date=override_date)
                if override_date:
                    for r in records:
                        r["holding_date"] = override_date
                holding_date = records[0]["holding_date"]
                inserted = save_holdings(records=records, etf_symbol=etf, source_file=dest.name)
                log_scrape_finish(
                    run_id=run_id,
                    status="SUCCESS",
                    holding_date=holding_date,
                    raw_file_path=str(dest),
                    records_parsed=inserted,
                )
                self._send_json(
                    {
                        "ok": True,
                        "etf_symbol": etf,
                        "holding_date": holding_date,
                        "records_inserted": inserted,
                        "saved_file": dest.name,
                    }
                )
            except Exception as e:
                log_scrape_finish(
                    run_id=run_id,
                    status="FAILED",
                    error_type="Excel file download failed or file is corrupted",
                    error_message=str(e),
                )
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/test-error":
            error_type = body.get("error_type", "Target URL unreachable or layout changed")
            etf = str(body.get("etf_symbol", DEFAULT_ETF_SYMBOL)).strip().upper()
            res = scheduler.run_now(trigger_type="DIAGNOSTIC_ERROR_TEST", etf_symbol=etf, simulate_error=error_type)
            self._send_json(res)
            return

        self._send_json({"error": "Endpoint not found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def bootstrap_initial_data() -> None:
    init_db()
    dates = get_available_dates(DEFAULT_ETF_SYMBOL)
    if not dates:
        print("Bootstrapping live MFS Active Value ETF (MFSV) daily & historical holdings...")
        scheduler.run_now(trigger_type="INITIAL_BOOTSTRAP", etf_symbol=DEFAULT_ETF_SYMBOL)
        seed_historical_mfs_dates(["2026-08-31", "2026-07-31", "2026-06-30", "2026-03-31", "2025-12-31"])


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    bootstrap_initial_data()
    scheduler.start_background_loop()
    server = ThreadingHTTPServer((host, port), ETFReportingHandler)
    print(f"MFS ETF Scraper & Reporting Server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    run_server(host="0.0.0.0", port=port)
