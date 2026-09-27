# MFS Value ETF (MFSV) Daily Holdings Scraper & Reporting Platform

Automated daily ETF holdings ingestion pipeline, Excel (`.xls`) archive, SQLite/PostgreSQL database parser, email error alerting system, and Date A vs Date B stock position comparison web app.

## 1. Quick Start

```bash
# Run the reporting web server + autonomous 7:00 PM PT background scheduler
python3 -m app.server

# Run the end to end test suite
python3 -m unittest tests/test_pipeline.py
```

Open `http://127.0.0.1:8080` in your browser.

## 2. Project Structure

* [`app/config.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/config.py): Schedule (`19:00` `America/Los_Angeles`), target URL, email (`gvarun@gmail.com`), SMTP, and optional proxy/GCS settings.
* [`app/scraper.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/scraper.py): Navigates to the MFS Active Value ETF (`MFSV`) daily holdings page, clears the `"Individual Investor"` and `"Save my preferences"` splash popups, validates the `"Download Daily Fund Holdings"` link, saves the raw `.xls` file (`Active_Value_ETF-Daily_Holdings_09-25-26.xls`) stamped with the download date in `data/raw_files/`, and loads the parsed records into the database.
* [`app/browser_scraper.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/browser_scraper.py): Playwright headless Chrome click flow for full browser automation.
* [`app/parser.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/parser.py): Extracts `Date`, `Ticker`, `CUSIP`, `Company Name`, `Shares`, `Value`, `Percent of Net Assets`, `GICS sector`, and `Country` from the downloaded `.xls` file.
* [`app/notifier.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/notifier.py): Catches all 4 required error types and immediately sends an email notification to `gvarun@gmail.com` with the current date and specific error type.
* [`app/scheduler.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/scheduler.py): Autonomous daily 7:00 PM PT scheduler with administrative `ACTIVE` (Start), `PAUSED` (Pause), and `STOPPED` (Stop) controls.
* [`app/server.py`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/server.py) & [`app/static/index.html`](file:///usr/local/google/home/velanati/.gemini/jetski/scratch/etf-holdings-tracker/app/static/index.html): Web UI for comparing Date A vs Date B newly added and completely removed stock positions (tracked by Ticker), inspecting parsed database records, downloading raw `.xls` files, and testing error alerts.

## 3. Answers to Section 5: Open Questions for Developer Review

### 1. Anonymity (Proxy Rotation vs VPN)
* **Recommendation**: Use a lightweight **residential/ISP proxy rotation service** (such as Bright Data, Oxylabs, or Smartproxy pay per GB) rather than a standard consumer VPN.
* **Why**: Standard VPNs route traffic through shared datacenter IPs that financial CDNs (Akamai/Cloudflare) frequently flag, and a dropped VPN tunnel can stall a headless server. Because this job runs once daily at 7:00 PM PT (fetching < 1 MB per ETF), a pay as you go residential proxy pool costs less than $2 to $5 per month, rotates cleanly per request via standard HTTP/Playwright proxy parameters (`SCRAPER_PROXIES`), and pairs with realistic browser headers and session cookies.

### 2. Infrastructure Costs (Cloud Hosting, Database, Proxies)
* **Compute & Scheduler**: Google Cloud Run + Cloud Scheduler (or a small $5 to $7/month VM on Render/Railway/GCP e2 micro). On Cloud Run, a daily 15 second scrape plus lightweight internal web UI stays within the free tier or ~$2 to $5/month.
* **Database & File Storage**:
  * Local/Single Node: SQLite + local disk (or Google Cloud Storage bucket at ~$0.02/GB/month for raw `.xls` archival).
  * Managed Cloud SQL / Supabase / Neon PostgreSQL: Free tier (up to 500 MB, enough for 10+ years of daily ETF holdings) or ~$10 to $15/month for a dedicated managed instance.
* **Email Notifications**: Gmail SMTP with an App Password ($0/month) or SendGrid/AWS SES ($0 to $1/month).
* **Total Estimated Cloud Cost**: **$0 to $10/month** on serverless/free tier resources, or **~$15 to $25/month** with dedicated managed DB and residential proxy rotation.

### 3. Architecture & Technology Stack
* **Scraper & Automation**: Python 3 + Playwright (for sites requiring JS click interactions) paired with `requests` + `BeautifulSoup4` (for direct cookie + DOM extraction, which runs 10x faster and uses minimal memory).
* **Database**: SQLite for zero config local/single container deployment, with clean SQL schema (`holdings`, `scrape_runs`, `alert_logs`, `admin_settings`) ready to swap to PostgreSQL as more ETFs are added.
* **Reporting Website**: Lightweight Python HTTP/FastAPI backend serving a responsive HTML/JS reporting interface with instant Date A vs Date B ticker diffing and CSV export.

### 4. Source Control & Handoff (Multi ETF Scaling)
* **Git Repository**: Hosted on GitHub (or GitLab) with a `main` production branch, protected PR workflow, `.env.example` for secrets (`SMTP_PASSWORD`, `SCRAPER_PROXIES`, `GCS_BUCKET_NAME`), and automated unit tests (`tests/test_pipeline.py`).
* **Scaling to Additional ETFs**: The database schema already includes an `etf_symbol` column (`MFSV` by default). Adding new ETFs (e.g., iShares, Vanguard, SPDR, or other MFS ETFs) only requires registering a provider adapter in `app/scraper.py` without changing the database or reporting UI.
