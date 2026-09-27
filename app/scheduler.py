import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.config import SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE
from app.database import get_settings, update_setting
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
                sched_str = settings.get("schedule_time_pt", f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
                try:
                    target_hour, target_min = [int(x) for x in sched_str.split(":")]
                except Exception:
                    target_hour, target_min = SCHEDULE_HOUR, SCHEDULE_MINUTE

                now_pt = datetime.now(tz)
                today_str = now_pt.strftime("%Y-%m-%d")

                if (
                    state == "ACTIVE"
                    and now_pt.hour == target_hour
                    and now_pt.minute == target_min
                    and self._last_scheduled_run_date != today_str
                    and not self._is_job_running
                ):
                    with self._lock:
                        self._is_job_running = True
                        self._last_scheduled_run_date = today_str
                    try:
                        run_daily_scrape(trigger_type="SCHEDULED_7PM_PT")
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
        sched_str = settings.get("schedule_time_pt", "19:00")
        try:
            target_hour, target_min = [int(x) for x in sched_str.split(":")]
        except Exception:
            target_hour, target_min = 19, 0

        next_run = now_pt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        if now_pt >= next_run:
            next_run += timedelta(days=1)

        return {
            "scraper_state": state,
            "is_job_running": self._is_job_running,
            "schedule_time_pt": f"{target_hour:02d}:{target_min:02d} PT",
            "timezone": SCHEDULE_TIMEZONE,
            "current_time_pt": now_pt.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            "next_scheduled_run_pt": (
                next_run.strftime("%Y-%m-%d %I:%M:%S %p %Z") if state == "ACTIVE" else f"None ({state})"
            ),
            "alert_email": settings.get("alert_email", "gvarun@gmail.com"),
        }

    def set_admin_state(self, new_state: str) -> Dict[str, Any]:
        valid = {"ACTIVE", "PAUSED", "STOPPED"}
        normalized = new_state.strip().upper()
        if normalized not in valid:
            raise ValueError(f"Invalid state {new_state}; must be one of {valid}")
        update_setting("scraper_state", normalized)
        return self.get_status()

    def run_now(self, trigger_type: str = "MANUAL_ADMIN", simulate_error: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if self._is_job_running:
                return {"status": "BUSY", "message": "A scrape job is already in progress."}
            self._is_job_running = True
        try:
            return run_daily_scrape(trigger_type=trigger_type, simulate_error=simulate_error)
        finally:
            with self._lock:
                self._is_job_running = False


scheduler = DailyScraperScheduler()
