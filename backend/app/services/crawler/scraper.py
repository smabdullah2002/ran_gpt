import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fake_useragent import UserAgent
from playwright.async_api import async_playwright

from app.services.processor.cleaner import extract_text


async def scrape_url(
    url: str,
    browser,
    semaphore: asyncio.Semaphore,
    user_agent: str,
) -> dict:
    async with semaphore:
        context = await browser.new_context(
            user_agent=user_agent,
        )
        page = await context.new_page()

        try:
            print(f"  Scraping: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            title = await page.title()
            html = await page.content()
            cleaned_text = extract_text(html) or ""
            word_count = len(cleaned_text.split())

            return {
                "url": url,
                "title": title,
                "cleaned_text": cleaned_text,
                "word_count": word_count,
                "scrape_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            print(f"  ERROR on {url}: {e}")
            return {
                "url": url,
                "title": "",
                "cleaned_text": "",
                "word_count": 0,
                "scrape_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            await context.close()


async def scrape_csv(
    input_path: str | Path,
    output_path: str | Path = "scraped_output.csv",
    max_concurrency: int = 5,
) -> list[dict]:
    input_path = Path(input_path)
    output_path = Path(output_path)

    df = pd.read_csv(input_path)
    urls = df["url"].tolist()
    print(
        f"\nScraping {len(urls)} pages from {input_path} (concurrency={max_concurrency})...\n"
    )

    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[dict] = []
    ua = UserAgent()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        tasks = [scrape_url(url, browser, semaphore, ua.random) for url in urls]
        for task in asyncio.as_completed(tasks):
            result = await task
            results.append(result)
            print(
                f"  [{len(results)}/{len(urls)}] {result['url']} — {result['word_count']} words"
            )

        await browser.close()

    output_df = pd.DataFrame(results)
    output_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\nDone. {len(results)} pages scraped to {output_path}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Playwright Scraper → CSV (reads crawler output)"
    )
    parser.add_argument(
        "--input",
        default="crawl_output.csv",
        help="Input CSV file path from crawler (default: crawl_output.csv)",        
    )
    parser.add_argument(
        "--output",
        default="scraped_output.csv",
        help="Output CSV file path (default: scraped_output.csv)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent pages (default: 5)",
    )

    args = parser.parse_args()  

    asyncio.run(
        scrape_csv(
            input_path=args.input,
            output_path=args.output,
            max_concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
