import base64
import hashlib
import hmac
import html as html_lib
import json
import os
import urllib.parse
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from app.config import (
    AUTH_ENABLED,
    AUTH_PASSWORD,
    AUTH_SECRET,
    AUTH_USERNAME,
    DB_PATH,
    DEFAULT_ETF_SYMBOL,
    RAW_FILES_DIR,
    SCHEDULE_TIMEZONE,
)
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
    set_etf_bot_state,
    update_setting,
)
from app.parser import parse_mfs_xls_file
from app.scheduler import scheduler
from app.scraper import get_etf_raw_dir, seed_historical_mfs_dates

STATIC_DIR = Path(__file__).resolve().parent / "static"


def organize_legacy_raw_files() -> None:
    """Ensures any top-level .xls/.csv files are moved cleanly into data/raw_files/<ETF_SYMBOL>/."""
    for p in list(RAW_FILES_DIR.glob("*.xls*")) + list(RAW_FILES_DIR.glob("*.csv")):
        if not p.is_file():
            continue
        if "Growth" in p.name:
            etf_sym = "MFSG"
        elif "Mid_Cap" in p.name:
            etf_sym = "MMID"
        elif "International" in p.name:
            etf_sym = "MFSI"
        elif "Blended_Research" in p.name:
            etf_sym = "BRCE"
        else:
            etf_sym = "MFSV"
        target_dir = get_etf_raw_dir(etf_sym)
        dest = target_dir / p.name
        if not dest.exists():
            dest.write_bytes(p.read_bytes())
        try:
            p.unlink()
        except Exception:
            pass


def list_raw_files(etf_symbol: str = "") -> List[Dict[str, Any]]:
    organize_legacy_raw_files()
    files: List[Dict[str, Any]] = []
    sym_filter = etf_symbol.strip().upper()
    tz = ZoneInfo(SCHEDULE_TIMEZONE)

    priority_order = {"MFSV": 0, "LSVD": 1, "VFLO": 2, "IVV": 3, "MFSG": 4}
    subdirs = [d for d in RAW_FILES_DIR.iterdir() if d.is_dir()]
    for d in sorted(subdirs, key=lambda x: (priority_order.get(x.name.upper(), 99), x.name)):
        if sym_filter and d.name.upper() != sym_filter:
            continue
        for p in sorted(
            list(d.glob("*.xls*")) + list(d.glob("*.csv")),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            stat = p.stat()
            rel = f"{d.name.upper()}/{p.name}"
            dt_pt = datetime.fromtimestamp(stat.st_mtime, tz=tz).strftime("%Y-%m-%d %I:%M:%S %p %Z")
            files.append(
                {
                    "etf_symbol": d.name.upper(),
                    "directory": f"data/raw_files/{d.name.upper()}/",
                    "relative_path": rel,
                    "filename": p.name,
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "downloaded_at_pt": dt_pt,
                    "retention_policy": "5+ Years (1,825+ Days)",
                    "view_url": f"/api/raw-files-view/{urllib.parse.quote(rel)}",
                    "download_url": f"/api/raw-files/{urllib.parse.quote(rel)}",
                }
            )
    if not sym_filter:
        files.sort(key=lambda x: x["modified_at"], reverse=True)
    return files


def resolve_raw_file(rel_path: str) -> Path:
    parts = [Path(part).name for part in rel_path.split("/") if part and part != ".."]
    if len(parts) >= 2:
        candidate = RAW_FILES_DIR / parts[0] / parts[1]
        if candidate.exists() and candidate.is_file():
            return candidate
    if parts:
        candidate = RAW_FILES_DIR / parts[-1]
        if candidate.exists() and candidate.is_file():
            return candidate
        for sub in RAW_FILES_DIR.iterdir():
            if sub.is_dir() and (sub / parts[-1]).exists():
                return sub / parts[-1]
    return RAW_FILES_DIR / "__nonexistent__"


def make_session_token(username: str) -> str:
    msg = f"{username}:{AUTH_PASSWORD}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{username}.{sig}"


def verify_session_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    username, _ = token.split(".", 1)
    if username != AUTH_USERNAME:
        return False
    expected = make_session_token(AUTH_USERNAME)
    return hmac.compare_digest(token, expected)


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

    def _is_authenticated(self) -> bool:
        if not AUTH_ENABLED:
            return True
        cookie_hdr = self.headers.get("Cookie", "")
        if cookie_hdr:
            c = SimpleCookie()
            try:
                c.load(cookie_hdr)
                if "etf_session" in c and verify_session_token(c["etf_session"].value):
                    return True
            except Exception:
                pass
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:].strip()).decode("utf-8", errors="ignore")
                if ":" in decoded:
                    u, p = decoded.split(":", 1)
                    if hmac.compare_digest(u.strip(), AUTH_USERNAME) and hmac.compare_digest(p, AUTH_PASSWORD):
                        return True
            except Exception:
                pass
        return False

    def _serve_html_file(self, filepath: Path) -> None:
        content = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/favicon.ico":
            svg_icon = (
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                b'<rect width="32" height="32" rx="8" fill="#4f46e5"/>'
                b'<path d="M7 23V9m0 14h18M23 12l-6 6-4-4-4 4" stroke="#fff" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
                b'</svg>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg_icon)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(svg_icon)
            return

        if path == "/api/health":
            self._send_json({"ok": True, "status": "healthy"})
            return

        if path == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", "etf_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if path == "/login":
            if self._is_authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._serve_html_file(STATIC_DIR / "login.html")
            return

        if path in ("/", "/index.html"):
            if not self._is_authenticated():
                self._serve_html_file(STATIC_DIR / "login.html")
                return
            self._serve_html_file(STATIC_DIR / "index.html")
            return

        if not self._is_authenticated():
            self._send_json({"ok": False, "error": "Unauthorized. Please log in."}, status=401)
            return

        if path == "/api/status":
            etf = qs.get("etf", [DEFAULT_ETF_SYMBOL])[0].strip().upper()
            dates = get_available_dates(etf)
            all_raw = list_raw_files("")
            raw_bytes = sum(int(f.get("size_bytes", 0)) for f in all_raw)
            db_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
            total_mb = round((raw_bytes + db_bytes) / (1024 * 1024), 2)
            proxy_mb = round((raw_bytes * 1.35) / (1024 * 1024), 2)
            self._send_json(
                {
                    "selected_etf": etf,
                    "etf_targets": get_etf_targets(),
                    "scheduler": scheduler.get_status(),
                    "available_dates": dates,
                    "recent_runs": get_recent_runs(15),
                    "recent_alerts": get_recent_alerts(15),
                    "raw_files": list_raw_files(etf),
                    "all_raw_files": all_raw,
                    "usage_tracker": {
                        "raw_files_count": len(all_raw),
                        "raw_files_mb": round(raw_bytes / (1024 * 1024), 2),
                        "db_mb": round(db_bytes / (1024 * 1024), 2),
                        "total_storage_mb": total_mb,
                        "storage_limit_mb": 5120,
                        "proxy_bandwidth_used_mb": proxy_mb,
                        "proxy_bandwidth_limit_gb": 250,
                        "railway_monthly_cost_usd": 5.00,
                        "webshare_monthly_cost_usd": 5.99,
                        "smtp_monthly_cost_usd": 0.00,
                        "total_monthly_cost_usd": 10.99,
                    },
                }
            )
            return

        if path == "/api/raw-files":
            etf = qs.get("etf", [""])[0].strip().upper()
            files = list_raw_files(etf)
            by_etf: Dict[str, List[Dict[str, Any]]] = {}
            for f in files:
                by_etf.setdefault(f["etf_symbol"], []).append(f)
            self._send_json({"files": files, "by_etf": by_etf})
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

        if path.startswith("/api/raw-files-view/"):
            rel = urllib.parse.unquote(path.replace("/api/raw-files-view/", ""))
            fpath = resolve_raw_file(rel)
            if not fpath.exists() or not fpath.is_file():
                self._send_json({"error": "File not found"}, status=404)
                return
            raw_text = fpath.read_text(encoding="utf-8", errors="ignore")
            preview_css = (
                "body{margin:0;font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;color:#0f172a;padding:28px 36px;}"
                ".topbar{display:flex;justify-content:space-between;align-items:center;background:#ffffff;border:1px solid #e2e8f0;"
                "border-radius:12px;padding:16px 22px;margin-bottom:20px;box-shadow:0 1px 3px rgba(15,23,42,0.05);}"
                ".topbar h2{margin:0;font-size:16px;font-weight:700;color:#0f172a;}"
                ".topbar span{font-size:12.5px;color:#64748b;}"
                ".btn-dl{background:#4f46e5;color:#fff;text-decoration:none;padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;}"
                ".card{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(15,23,42,0.05);overflow-x:auto;}"
                "table{width:100%;border-collapse:collapse;font-size:13px;background:#ffffff;}"
                "th,td{border-bottom:1px solid #e2e8f0;padding:10px 12px;text-align:left;}"
                "th{background:#f8fafc;color:#64748b;font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:0.04em;}"
                "tr:hover td{background:#f8fafc;}"
            )
            dl_href = f"/api/raw-files/{urllib.parse.quote(rel)}"
            top_bar = (
                f"<div class='topbar'><div><h2>Original File Preview: {html_lib.escape(fpath.name)}</h2>"
                f"<span>Directory: data/raw_files/{html_lib.escape(rel)} • Retention: 5+ Years</span></div>"
                f"<a class='btn-dl' href='{dl_href}'>⬇ Download Original File</a></div>"
            )
            if "<table" in raw_text.lower():
                styled_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    f"<title>{html_lib.escape(fpath.name)}</title>"
                    f"<style>{preview_css}</style></head><body>"
                    f"{top_bar}<div class='card'>{raw_text}</div></body></html>"
                )
            elif "<ss:workbook" in raw_text.lower() or "<ss:worksheet" in raw_text.lower():
                import re
                start = raw_text.find('<ss:Worksheet ss:Name="Holdings">')
                if start == -1:
                    start = raw_text.find("<ss:Worksheet")
                end = raw_text.find("</ss:Worksheet>", start)
                sheet_xml = raw_text[start:end] if end != -1 else raw_text
                row_blocks = re.findall(r"<ss:Row[^>]*>(.*?)</ss:Row>", sheet_xml, re.S)
                rows_html = []
                header_rendered = False
                for rb in row_blocks:
                    cells = [
                        re.sub(r"<[^>]+>", "", c).strip()
                        for c in re.findall(r"<ss:Data[^>]*>(.*?)</ss:Data>", rb, re.S)
                    ]
                    if not any(cells):
                        continue
                    is_hdr = not header_rendered and "TICKER" in " ".join(cells).upper()
                    if is_hdr:
                        header_rendered = True
                    tag = "th" if is_hdr else "td"
                    cells_html = "".join(f"<{tag}>{html_lib.escape(c)}</{tag}>" for c in cells)
                    rows_html.append(f"<tr>{cells_html}</tr>")
                styled_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    f"<title>{html_lib.escape(fpath.name)}</title>"
                    f"<style>{preview_css}</style></head><body>"
                    f"{top_bar}<div class='card'><table>{''.join(rows_html)}</table></div></body></html>"
                )
            elif fpath.suffix.lower() == ".csv":
                import csv, io
                reader = csv.reader(io.StringIO(raw_text))
                rows_html = []
                for idx, row in enumerate(reader):
                    tag = "th" if idx == 0 else "td"
                    cells = "".join(f"<{tag}>{html_lib.escape(c)}</{tag}>" for c in row)
                    rows_html.append(f"<tr>{cells}</tr>")
                styled_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    f"<title>{html_lib.escape(fpath.name)}</title>"
                    f"<style>{preview_css}</style></head><body>"
                    f"{top_bar}<div class='card'><table>{''.join(rows_html)}</table></div></body></html>"
                )
            else:
                styled_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    f"<title>{html_lib.escape(fpath.name)}</title>"
                    f"<style>{preview_css} pre{{white-space:pre-wrap;font-family:monospace;font-size:12.5px;}}</style></head><body>"
                    f"{top_bar}<div class='card'><pre>{html_lib.escape(raw_text)}</pre></div></body></html>"
                )
            data = styled_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path.startswith("/api/raw-files/"):
            rel = urllib.parse.unquote(path.replace("/api/raw-files/", ""))
            fpath = resolve_raw_file(rel)
            if not fpath.exists() or not fpath.is_file():
                self._send_json({"error": "File not found"}, status=404)
                return
            data = fpath.read_bytes()
            self.send_response(200)
            ctype = "text/csv" if fpath.suffix.lower() == ".csv" else "application/vnd.ms-excel"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{fpath.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/login":
            u = str(body.get("username", "")).strip()
            p = str(body.get("password", ""))
            if hmac.compare_digest(u, AUTH_USERNAME) and hmac.compare_digest(p, AUTH_PASSWORD):
                token = make_session_token(AUTH_USERNAME)
                resp_bytes = json.dumps({"ok": True, "username": AUTH_USERNAME}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header(
                    "Set-Cookie",
                    f"etf_session={token}; HttpOnly; Path=/; Max-Age=2592000; SameSite=Lax",
                )
                self.end_headers()
                self.wfile.write(resp_bytes)
            else:
                self._send_json({"ok": False, "error": "Invalid username or password."}, status=401)
            return

        if path == "/api/logout":
            resp_bytes = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Set-Cookie", "etf_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if not self._is_authenticated():
            self._send_json({"ok": False, "error": "Unauthorized. Please log in."}, status=401)
            return

        if path == "/api/admin/toggle":
            state = body.get("state", "ACTIVE")
            try:
                status = scheduler.set_admin_state(state)
                self._send_json({"ok": True, "scheduler": status})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/etf-bot-state":
            try:
                etf_sym = str(body.get("etf_symbol", "")).strip().upper()
                bot_state = str(body.get("bot_state", "ACTIVE")).strip().upper()
                updated = set_etf_bot_state(etf_sym, bot_state)
                self._send_json({"ok": True, "etf": updated, "etf_targets": get_etf_targets()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/settings":
            if "alert_email" in body and body["alert_email"]:
                update_setting("alert_email", str(body["alert_email"]).strip())
            if "schedule_time_pt" in body and body["schedule_time_pt"]:
                update_setting("schedule_time_pt", str(body["schedule_time_pt"]).strip())
            if "proxy_urls" in body:
                update_setting("proxy_urls", str(body["proxy_urls"]).strip())
            self._send_json({"ok": True, "scheduler": scheduler.get_status()})
            return

        if path == "/api/admin/add-etf":
            try:
                etf_sym = str(body.get("etf_symbol", "")).strip().upper()
                etf_name = str(body.get("etf_name", "")).strip()
                daily_url = str(body.get("daily_url", "")).strip()
                file_format = str(body.get("file_format", "XLS")).strip().upper()
                target = add_or_update_etf_target(etf_sym, etf_name, daily_url, file_format=file_format)
                self._send_json({"ok": True, "etf": target, "etf_targets": get_etf_targets()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if path == "/api/admin/scrape-now":
            etf = str(body.get("etf_symbol", DEFAULT_ETF_SYMBOL)).strip().upper()
            res = scheduler.run_now(trigger_type="MANUAL_UI", etf_symbol=etf)
            self._send_json(res)
            return

        if path == "/api/admin/scrape-all-active":
            results = []
            for target in get_etf_targets():
                if target.get("is_active", 1) and target.get("bot_state", "ACTIVE") == "ACTIVE":
                    r = scheduler.run_now(trigger_type="MANUAL_ALL_ACTIVE", etf_symbol=target["etf_symbol"])
                    results.append(r)
            self._send_json({"ok": True, "results": results})
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
                stamp = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d_%H-%M-%S_PT")
                save_name = f"{Path(filename).stem}_uploaded_{stamp}{Path(filename).suffix or '.xls'}"
                etf_dir = get_etf_raw_dir(etf)
                dest = etf_dir / save_name
                dest.write_text(content, encoding="utf-8")

                records = parse_mfs_xls_file(dest, fallback_date=override_date)
                if override_date:
                    for r in records:
                        r["holding_date"] = override_date
                holding_date = records[0]["holding_date"]
                inserted = save_holdings(records=records, etf_symbol=etf, source_file=f"{etf}/{dest.name}")
                log_scrape_finish(
                    run_id=run_id,
                    status="SUCCESS",
                    holding_date=holding_date,
                    raw_file_path=f"{etf}/{dest.name}",
                    records_parsed=inserted,
                )
                self._send_json(
                    {
                        "ok": True,
                        "etf_symbol": etf,
                        "holding_date": holding_date,
                        "records_inserted": inserted,
                        "saved_file": f"{etf}/{dest.name}",
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
    organize_legacy_raw_files()

    def _async_seed() -> None:
        try:
            for sym in ["MFSV", "LSVD", "VFLO", "IVV"]:
                if not get_available_dates(sym):
                    print(f"Bootstrapping initial daily holdings for {sym}...")
                    scheduler.run_now(trigger_type="INITIAL_BOOTSTRAP", etf_symbol=sym)
            mfsv_dates = get_available_dates("MFSV")
            if len(mfsv_dates) < 2:
                seed_historical_mfs_dates(
                    ["2026-08-31", "2026-07-31", "2026-06-30", "2026-03-31", "2025-12-31"],
                    etf_symbol="MFSV",
                )
        except Exception as e:
            print(f"Initial bootstrap warning: {e}")

    import threading
    threading.Thread(target=_async_seed, daemon=True).start()


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    bootstrap_initial_data()
    scheduler.start_background_loop()
    server = ThreadingHTTPServer((host, port), ETFReportingHandler)
    print(f"ETF Scraper & Reporting Server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    run_server(host="0.0.0.0", port=port)
