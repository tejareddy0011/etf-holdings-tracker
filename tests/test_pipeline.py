import unittest
from pathlib import Path
from app.config import RAW_FILES_DIR
from app.database import (
    add_or_update_etf_target,
    compare_holdings,
    get_available_dates,
    get_etf_targets,
    get_recent_alerts,
    init_db,
)
from app.notifier import KnownErrorType
from app.parser import parse_csv_holdings
from app.scheduler import scheduler
from app.scraper import run_daily_scrape, seed_historical_mfs_dates


class TestETFPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_01_live_daily_scrape_and_xls_archive(self) -> None:
        res = run_daily_scrape(trigger_type="UNIT_TEST", etf_symbol="MFSV")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreater(res["records_inserted"], 50)
        raw_path = RAW_FILES_DIR / res["raw_file"]
        self.assertTrue(raw_path.exists())
        canonical_path = RAW_FILES_DIR / "Active_Value_ETF-Daily_Holdings_09-25-26.xls"
        self.assertTrue(canonical_path.exists())

    def test_02_historical_seed_and_date_comparison(self) -> None:
        seed_res = seed_historical_mfs_dates(["2026-08-31", "2026-06-30", "2026-03-31"], etf_symbol="MFSV")
        for item in seed_res:
            self.assertEqual(item["status"], "SUCCESS")

        dates = [d["holding_date"] for d in get_available_dates("MFSV")]
        self.assertIn("2026-09-25", dates)
        self.assertIn("2026-06-30", dates)

        comp = compare_holdings(date_a="2026-06-30", date_b="2026-09-25", etf_symbol="MFSV", exclude_cash=True)
        added_tickers = [r["ticker"] for r in comp["newly_added"]]
        removed_tickers = [r["ticker"] for r in comp["completely_removed"]]
        self.assertIn("TJX US", added_tickers)
        self.assertIn("EBAY US", added_tickers)
        self.assertIn("CAT US", removed_tickers)
        self.assertIn("C US", removed_tickers)

    def test_03_multi_etf_registry_and_second_etf_scrape(self) -> None:
        targets = get_etf_targets()
        symbols = [t["etf_symbol"] for t in targets]
        self.assertIn("MFSV", symbols)
        self.assertIn("MFSG", symbols)

        # Scrape MFS Active Growth ETF (MFSG)
        res_growth = run_daily_scrape(trigger_type="UNIT_TEST", etf_symbol="MFSG")
        self.assertEqual(res_growth["status"], "SUCCESS")
        self.assertGreater(res_growth["records_inserted"], 20)

    def test_04_manual_csv_upload_parser(self) -> None:
        sample_csv = (
            "CUSIP/SEDOL,Ticker,Securities,Shares or Par Amount,Value,Percent of Net Assets,GICS Sectors,Country\n"
            "594918104,MSFT US,MICROSOFT CORP,1000,$500000.00,5.00%,Information Technology,United States\n"
            "037833100,AAPL US,APPLE INC,2000,$400000.00,4.00%,Information Technology,United States\n"
        )
        records = parse_csv_holdings(sample_csv, "custom_upload_2026-09-01.csv")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["holding_date"], "2026-09-01")
        self.assertEqual(records[0]["ticker"], "MSFT US")

    def test_05_admin_controls(self) -> None:
        st = scheduler.set_admin_state("PAUSED")
        self.assertEqual(st["scraper_state"], "PAUSED")
        st = scheduler.set_admin_state("STOPPED")
        self.assertEqual(st["scraper_state"], "STOPPED")
        st = scheduler.set_admin_state("ACTIVE")
        self.assertEqual(st["scraper_state"], "ACTIVE")

    def test_06_all_four_known_error_notifications(self) -> None:
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
