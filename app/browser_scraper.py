"""
Playwright Headless Browser Automation for MFS Daily ETF Holdings.
Executes the exact UI navigation steps specified in Section 2:
  1. Navigate to MFS MFSV Daily Holdings URL
  2. Bypass initial splash screens (click "Individual Investor" and "Save my preferences" pop-ups)
  3. Locate and click the "Download Daily Fund Holdings" link (.js-download-btn)
  4. Save the downloaded Excel (.xls) file stamped with the download date
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import RAW_FILES_DIR, SCHEDULE_TIMEZONE, TARGET_URL
from app.notifier import KnownErrorType, ScraperBotError


def download_via_playwright(url: str = TARGET_URL, proxy_server: Optional[str] = None) -> Path:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
    except ImportError as e:
        raise RuntimeError("Playwright is not installed in the current environment") from e

    with sync_playwright() as p:
        launch_args = {"headless": True}
        if Path("/usr/bin/google-chrome").exists():
            launch_args["executable_path"] = "/usr/bin/google-chrome"
        if proxy_server:
            launch_args["proxy"] = {"server": proxy_server}

        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # Step 1: Open Target URL
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not response or response.status >= 400:
                status = response.status if response else "No Response"
                raise ScraperBotError(
                    KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                    f"Target URL returned HTTP {status}",
                )
        except ScraperBotError:
            browser.close()
            raise
        except Exception as e:
            browser.close()
            raise ScraperBotError(
                KnownErrorType.URL_UNREACHABLE_OR_LAYOUT_CHANGED,
                f"Target URL unreachable: {e}",
            ) from e

        # Step 2: Bypass initial splash screens ("Individual Investor" and "Save my preferences" pop-ups)
        try:
            # OneTrust cookie consent banner if visible
            cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept'), button:has-text('Save my preferences')")
            if cookie_btn.count() > 0 and cookie_btn.first.is_visible():
                cookie_btn.first.click(timeout=5000)

            # Role selection splash modal ("Individual Investor" -> "Save my preferences")
            indiv_investor = page.locator("text='Individual Investor'")
            if indiv_investor.count() > 0 and indiv_investor.first.is_visible():
                indiv_investor.first.click(timeout=5000)
                save_prefs = page.locator("text='Save my preferences', button:has-text('Save'), a:has-text('Save my preferences')")
                if save_prefs.count() > 0 and save_prefs.first.is_visible():
                    save_prefs.first.click(timeout=5000)
        except Exception as e:
            browser.close()
            raise ScraperBotError(
                KnownErrorType.POPUPS_FAILED_TO_CLEAR,
                f'Failed to clear "Individual Investor" / "Save my preferences" pop-ups: {e}',
            ) from e

        # Step 3: Locate and click "Download Daily Fund Holdings" link
        download_link = page.locator("a.js-download-btn, a:has-text('Download Daily Fund Holdings')")
        if download_link.count() == 0:
            browser.close()
            raise ScraperBotError(
                KnownErrorType.DOWNLOAD_LINK_MISSING_OR_ALTERED,
                '"Download Daily Fund Holdings" link is missing or altered on the page.',
            )

        # Step 4: Trigger download and save file stamped with download date
        try:
            with page.expect_download(timeout=15000) as download_info:
                download_link.first.click()
            download = download_info.value
            suggested_name = download.suggested_filename or "Active_Value_ETF-Daily_Holdings.xls"
            stem = Path(suggested_name).stem
            download_stamp = datetime.now(ZoneInfo(SCHEDULE_TIMEZONE)).strftime("%Y-%m-%d")
            stamped_path = RAW_FILES_DIR / f"{stem}_downloaded_{download_stamp}.xls"
            download.save_as(str(stamped_path))
            browser.close()
            return stamped_path
        except PlaywrightTimeoutError as e:
            browser.close()
            raise ScraperBotError(
                KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
                f"Timed out waiting for Excel file download after clicking link: {e}",
            ) from e
        except Exception as e:
            browser.close()
            raise ScraperBotError(
                KnownErrorType.EXCEL_FAILED_OR_CORRUPTED,
                f"Excel file download failed: {e}",
            ) from e
