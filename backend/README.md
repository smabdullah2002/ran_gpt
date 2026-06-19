# RAN GPT — Backend

Ingests website content and saves it for AI training.

## 1. Crawl mode — `python run.py crawl https://example.com`

Uses Crawlee + Playwright to visit pages, click links, and discover URLs on the site.

- Stops after 50 pages by default (use `--max-pages` to change)
- Saves discovered URLs to `crawl_output.csv`
- Good for: sites without sitemaps

```
python run.py crawl https://example.com
python run.py crawl https://example.com --max-pages 100 --output urls.csv
```

## 2. Sitemap mode — `python run.py sitemap https://example.com/sitemap.xml`

Fetches the sitemap XML directly and extracts all URLs.

- Gets ALL URLs from the sitemap (no page limit)
- If Cloudflare blocks the XML, falls back to Playwright to fetch it
- Saves URLs to `crawl_output.csv`
- Good for: getting every page instantly

```
python run.py sitemap https://example.com/sitemap.xml
python run.py sitemap https://studyfetch.com/sitemap.xml --output studyfetch.csv
```

## 3. Scrape mode — `python run.py scrape`

Reads the CSV from step 1 or 2, visits each URL, and extracts clean text.

- Strips ads, nav, footers using Trafilatura
- Saves to `scraped_output.csv`
- Defaults to reading `crawl_output.csv`

```
python run.py scrape
python run.py scrape --input urls.csv --output cleaned.csv --max-concurrency 10
```

## Typical pipeline

```powershell
python run.py crawl https://example.com
python run.py scrape
```

Or with a sitemap:

```powershell
python run.py sitemap https://example.com/sitemap.xml
python run.py scrape
```

## File layout

| File | What it does |
|---|---|
| `run.py` | CLI entry point — crawl, sitemap, or scrape |
| `app/services/crawler/crawler.py` | Crawlee crawler + sitemap fetcher |
| `app/services/crawler/scraper.py` | Visits URLs, extracts clean text |
| `app/services/processor/cleaner.py` | Trafilatura text extraction wrapper |
| `app/services/processor/sitemap.py` | Fetches & parses sitemap XML |
| `playwright_test.py` | Debug tool — test if a URL loads in Playwright |
| `crawl_output.csv` | Discovered URLs from crawl or sitemap |
| `scraped_output.csv` | Cleaned text per URL |

## Run modules directly (no CLI)

Each module can also be run directly with `python -m`:

```powershell
python -m app.services.crawler.crawler   # Crawl the default URL (studyfetch)
python -m app.services.crawler.scraper   # Scrape URLs from crawl_output.csv
```

This is useful for testing during development.

## First time setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```
