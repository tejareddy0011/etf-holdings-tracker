import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.config import DEFAULT_ETF_SYMBOL, SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE
from app.database import get_etf_targets, get_settings, update_setting
from app.scraper import run_daily_scrape


class DailyScraperScheduler:
    """
    Autonomous background scheduler that executes the MFS ETF scraper daily at 7:00 PM PT
    (America/Los_Angeles) while honoring the administrative toggle:
      - ACTIVE  (Running autonomously on daily 7:00 PM PT schedule)
      - PAUSED  (Temporarily paused; skips daily trigger until resumed)
      - STOPPED (Halted until manually started)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_scheduled_run_date: Optional[str] = None
        self._last_etf_run_date: Dict[str, str] = {}
        self._is_job_running: bool = False

    def start_background_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ETFScraperScheduler")
        self._thread.start()

    def _loop(self) -> None:
        tz = ZoneInfo(SCHEDULE_TIMEZONE)
        while not self._stop_event.is_set():
            try:
                settings = get_settings()
                state = settings.get("scraper_state", "ACTIVE")
                default_sched = settings.get("schedule_time_pt", f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

                now_pt = datetime.now(tz)
                today_str = now_pt.strftime("%Y-%m-%d")

                if state == "ACTIVE" and not self._is_job_running:
                    due_targets = []
                    for target in get_etf_targets():
                        if not (target.get("is_active", 1) and target.get("bot_state", "ACTIVE") == "ACTIVE"):
                            continue
                        sym = target["etf_symbol"]
                        etf_sched = target.get("schedule_time_pt") or default_sched
                        try:
                            t_hour, t_min = [int(x) for x in etf_sched.split(":")]
                        except Exception:
                            t_hour, t_min = SCHEDULE_HOUR, SCHEDULE_MINUTE
                        if (
                            now_pt.hour == t_hour
                            and now_pt.minute == t_min
                            and self._last_etf_run_date.get(sym) != today_str
                        ):
                            due_targets.append((sym, t_hour, t_min))

                    if due_targets:
                        with self._lock:
                            self._is_job_running = True
                            self._last_scheduled_run_date = today_str
                            for sym, _, _ in due_targets:
                                self._last_etf_run_date[sym] = today_str
                        try:
                            for sym, t_hour, t_min in due_targets:
                                if t_hour == 19 and t_min == 0:
                                    trig = "SCHEDULED_7PM_PT"
                                elif t_hour == 20 and t_min == 0:
                                    trig = "SCHEDULED_8PM_PT"
                                else:
                                    trig = f"SCHEDULED_{t_hour:02d}{t_min:02d}_PT"
                                run_daily_scrape(
                                    trigger_type=trig,
                                    etf_symbol=sym,
                                )
                        finally:
                            with self._lock:
                                self._is_job_running = False
            except Exception as e:
                print(f"Scheduler loop error: {e}")

            time.sleep(15)

    def get_status(self) -> Dict[str, Any]:
        tz = ZoneInfo(SCHEDULE_TIMEZONE)
        now_pt = datetime.now(tz)
        settings = get_settings()
        state = settings.get("scraper_state", "ACTIVE")
        default_sched = settings.get("schedule_time_pt", "19:00")

        active_times = sorted(
            {
                (t.get("schedule_time_pt") or default_sched)
                for t in get_etf_targets()
                if t.get("is_active", 1) and t.get("bot_state", "ACTIVE") == "ACTIVE"
            }
        )
        if not active_times:
            active_times = [default_sched]

        upcoming_candidates = []
        for s in active_times:
            try:
                th, tm = [int(x) for x in s.split(":")]
            except Exception:
                th, tm = 19, 0
            cand = now_pt.replace(hour=th, minute=tm, second=0, microsecond=0)
            if now_pt >= cand:
                cand += timedelta(days=1)
            upcoming_candidates.append(cand)

        next_run = min(upcoming_candidates)
        sched_display = " & ".join(active_times) + " PT"

        return {
            "scraper_state": state,
            "is_job_running": self._is_job_running,
            "schedule_time_pt": sched_display,
            "timezone": SCHEDULE_TIMEZONE,
            "current_time_pt": now_pt.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            "next_scheduled_run_pt": (
                next_run.strftime("%Y-%m-%d %I:%M:%S %p %Z") if state == "ACTIVE" else f"None ({state})"
            ),
            "alert_email": settings.get("alert_email", "gvarun@gmail.com"),
            "proxy_urls": settings.get("proxy_urls", ""),
        }

    def set_admin_state(self, new_state: str) -> Dict[str, Any]:
        valid = {"ACTIVE", "PAUSED", "STOPPED"}
        normalized = new_state.strip().upper()
        if normalized not in valid:
            raise ValueError(f"Invalid state {new_state}; must be one of {valid}")
        update_setting("scraper_state", normalized)
        return self.get_status()

    def run_now(
        self,
        trigger_type: str = "MANUAL_ADMIN",
        etf_symbol: str = DEFAULT_ETF_SYMBOL,
        simulate_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._is_job_running:
                return {"status": "BUSY", "message": "A scrape job is already in progress."}
            self._is_job_running = True
        try:
            return run_daily_scrape(
                trigger_type=trigger_type,
                etf_symbol=etf_symbol,
                simulate_error=simulate_error,
            )
        finally:
            with self._lock:
                self._is_job_running = False


scheduler = DailyScraperScheduler()
