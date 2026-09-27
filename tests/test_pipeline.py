import unittest
from app.config import RAW_FILES_DIR
from app.database import (
    compare_holdings,
    get_available_dates,
    get_recent_alerts,
    init_db,
)
from app.notifier import KnownErrorType
from app.scheduler import scheduler
from app.scraper import run_daily_scrape, seed_historical_mfs_dates


class TestETFPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_01_live_daily_scrape_and_xls_archive(self) -> None:
        res = run_daily_scrape(trigger_type="UNIT_TEST")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreater(res["records_inserted"], 50)
        raw_path = RAW_FILES_DIR / res["raw_file"]
        self.assertTrue(raw_path.exists())
        canonical_path = RAW_FILES_DIR / "Active_Value_ETF-Daily_Holdings_09-25-26.xls"
        self.assertTrue(canonical_path.exists())

    def test_02_historical_seed_and_date_comparison(self) -> None:
        seed_res = seed_historical_mfs_dates(["2026-08-31", "2026-06-30", "2026-03-31"])
        for item in seed_res:
            self.assertEqual(item["status"], "SUCCESS")

        dates = [d["holding_date"] for d in get_available_dates()]
        self.assertIn("2026-09-25", dates)
        self.assertIn("2026-06-30", dates)

        comp = compare_holdings(date_a="2026-06-30", date_b="2026-09-25", exclude_cash=True)
        added_tickers = [r["ticker"] for r in comp["newly_added"]]
        removed_tickers = [r["ticker"] for r in comp["completely_removed"]]
        self.assertIn("TJX US", added_tickers)
        self.assertIn("EBAY US", added_tickers)
        self.assertIn("CAT US", removed_tickers)
        self.assertIn("C US", removed_tickers)

    def test_03_admin_controls(self) -> None:
        st = scheduler.set_admin_state("PAUSED")
        self.assertEqual(st["scraper_state"], "PAUSED")
        st = scheduler.set_admin_state("STOPPED")
        self.assertEqual(st["scraper_state"], "STOPPED")
        st = scheduler.set_admin_state("ACTIVE")
        self.assertEqual(st["scraper_state"], "ACTIVE")

    def test_04_all_four_known_error_notifications(self) -> None:
        error_types = [
            KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
            KnownErrorType.POPUPS_FAILED_TO_CLEAR,
            KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
            KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
        ]
        for et in error_types:
            res = run_daily_scrape(trigger_type="ERROR_TEST", simulate_error=et)
            self.assertEqual(res["status"], "FAILED")
            self.assertEqual(res["error_type"], et)
            self.assertEqual(res["alert_sent"]["recipient"], "gvarun@gmail.com")

        alerts = get_recent_alerts(10)
        self.assertGreaterEqual(len(alerts), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
