# Multi-ETF Daily Holdings Scraper & Reporting Platform (Phase 1 & Phase 2)

Automated daily 7:00 PM PT anonymous ETF holdings ingestion pipeline (`.xls` and `.csv`), per-ETF archive folders (`data/raw_files/<ETF>/`), PT download date-and-time stamping (`_downloaded_YYYY-MM-DD_HH-MM-SS_PT`), 5+ year file retention policy, in-browser raw file viewer, SQLite/PostgreSQL database parser, email error alerting system (`Adam.smith.fintech@gmail.com`), per-ETF On/Pause/Off bot controls, and Date A vs Date B stock position comparison web app.

## Configured ETFs (Daily 7:00 PM PT & 8:00 PM PT Automated Download)

1. **`MFSV` (MFS Active Value ETF)** — `.xls` (7:00 PM PT)
   * Target URL: `https://www.mfs.com/en-us/individual-investor/product-strategies/exchange-traded-funds/daily-holdings/MFSV-active-value-etf.html#`
   * Link: `"Download Daily Fund Holdings"`
   * Dedicated Folder: `data/raw_files/MFSV/`
2. **`LSVD` (LSV Disciplined Value ETF)** — `.csv` (7:00 PM PT)
   * Target URL: `https://www.lsvasset.com/disciplined-value-etf/`
   * Link: `"All Fund Holdings CSV Download"`
   * Dedicated Folder: `data/raw_files/LSVD/`
3. **`VFLO` (VictoryShares Free Cash Flow ETF)** — `.csv` (7:00 PM PT)
   * Target URL: `https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-free-cash-flow-etf`
   * Link: `"All Holdings"`
   * Dedicated Folder: `data/raw_files/VFLO/`
4. **`IVV` (iShares Core S&P 500 ETF)** — `.xls` (7:00 PM PT)
   * Target URL: `https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf`
   * Link: `"Data download"`
   * Dedicated Folder: `data/raw_files/IVV/`
5. **`SFLO` (VictoryShares Small Cap Free Cash Flow ETF)** — `.csv` (8:00 PM PT)
   * Target URL: `https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-small-cap-free-cash-flow-etf`
   * Link: `"All Holdings"`
   * Dedicated Folder: `data/raw_files/SFLO/`
6. **`GFLW` (VictoryShares Free Cash Flow Growth ETF)** — `.csv` (8:00 PM PT)
   * Target URL: `https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-free-cash-flow-growth-etf`
   * Link: `"All Holdings"`
   * Dedicated Folder: `data/raw_files/GFLW/`
7. **`IFLO` (VictoryShares International Free Cash Flow ETF)** — `.csv` (8:00 PM PT)
   * Target URL: `https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-international-free-cash-flow-etf`
   * Link: `"All Holdings"`
   * Dedicated Folder: `data/raw_files/IFLO/`
8. **`GRIN` (VictoryShares International Free Cash Flow Growth ETF)** — `.csv` (8:00 PM PT)
   * Target URL: `https://advisor.vcm.com/products/victoryshares-etfs/victoryshares-etfs-list/victoryshares-international-free-cash-flow-growth-etf`
   * Link: `"All Holdings"`
   * Dedicated Folder: `data/raw_files/GRIN/`


## 1. Quick Start & Pulling Latest Updates

```bash
# If you already cloned the repo earlier, pull the latest updates first:
git pull

# Run the reporting web server + autonomous 7:00 PM PT background scheduler
python3 -m app.server

# Run the end to end test suite across all 4 ETFs
python3 -m unittest discover -s tests -v
```

Open `http://127.0.0.1:8080` in your browser (or deploy directly to any cloud host using the included `Dockerfile`, `docker-compose.yml`, `railway.json`, or `render.yaml`).

## 2. Project Structure & Key Features

* [`app/config.py`](app/config.py): Schedule (`19:00` and `20:00` `America/Los_Angeles`), multi-ETF presets (`MFSV`, `LSVD`, `VFLO`, `IVV`, `SFLO`, `GFLW`, `IFLO`, `GRIN`), 5-year file retention policy (`RETENTION_YEARS = 5`), alert email (`Adam.smith.fintech@gmail.com`), SMTP, and standard HTTP client settings.
* [`app/scraper.py`](app/scraper.py): Clears splash popups, validates download links across MFS, LSV Asset Management, VictoryShares, and BlackRock iShares, stamps every file with download date and time in PT (`_downloaded_YYYY-MM-DD_HH-MM-SS_PT`), saves each ETF's daily `.xls` or `.csv` file inside its own dedicated directory (`data/raw_files/MFSV/`, `data/raw_files/LSVD/`, `data/raw_files/VFLO/`, `data/raw_files/IVV/`, etc.), and loads parsed records into the database.
* [`app/browser_scraper.py`](app/browser_scraper.py): Playwright headless Chrome click flow for full browser automation.
* [`app/parser.py`](app/parser.py): Multi-format parser supporting MFS HTML `.xls`, BlackRock iShares SpreadsheetML XML `.xls`, LSV `.csv`, and VictoryShares `.csv` holdings files across different ETF schemas.
* [`app/notifier.py`](app/notifier.py): Catches all 4 required error types and immediately sends an email notification to `Adam.smith.fintech@gmail.com` with the current date and specific error type.
* [`app/scheduler.py`](app/scheduler.py): Autonomous daily 7:00 PM PT and 8:00 PM PT scheduler with both master and per-ETF `ACTIVE` (On), `PAUSED` (Pause), and `STOPPED` (Off) bot controls.
* [`app/server.py`](app/server.py) & [`app/static/index.html`](app/static/index.html): Web UI for viewing or downloading original PT-stamped `.xls` and `.csv` files directly in the browser, toggling each ETF's bot On/Pause/Off independently, and comparing Date A vs Date B newly added and completely removed stock positions.

## 3. Architecture & Operations Overview

### 1. Network Reliability & Session Handling
* Uses realistic browser headers, session cookies, and configurable outbound request routing so daily scheduled jobs complete cleanly across financial CDNs.

### 2. Infrastructure Costs (Cloud Hosting & Database)
* **Compute & Scheduler**: Railway Hobby Plan ($5/month) or Google Cloud Run + Cloud Scheduler.
* **Database & File Storage**:
  * Local/Single Node: SQLite + persistent volume for raw `.xls` and `.csv` archival.
  * Managed Cloud SQL / Supabase / Neon PostgreSQL: Free tier (up to 500 MB, enough for 10+ years of daily ETF holdings).
* **Email Notifications**: Gmail SMTP with an App Password ($0/month).

### 3. Architecture & Technology Stack
* **Scraper & Automation**: Python 3 + Playwright (for sites requiring JS click interactions) paired with `requests` + `BeautifulSoup4` (for direct cookie + DOM extraction, which runs 10x faster and uses minimal memory).
* **Database**: SQLite for zero config local/single container deployment, with clean SQL schema (`holdings`, `scrape_runs`, `alert_logs`, `admin_settings`) ready to swap to PostgreSQL as more ETFs are added.
* **Reporting Website**: Lightweight Python HTTP/FastAPI backend serving a responsive HTML/JS reporting interface with instant Date A vs Date B ticker diffing and CSV export.

### 4. Source Control & Handoff (Multi ETF Scaling)
* **Git Repository**: Hosted on GitHub with a `main` production branch, `.env.example` for environment variables, and automated unit tests (`tests/test_pipeline.py`).
* **Scaling to Additional ETFs**: The database schema already includes an `etf_symbol` column (`MFSV` by default). Adding new ETFs (e.g., iShares, Vanguard, SPDR, or other MFS ETFs) only requires registering a provider adapter in `app/scraper.py` without changing the database or reporting UI.
* **Contact & Code Issues**: For any repository or code questions, reach out at `tejareddi0011@gmail.com`.

