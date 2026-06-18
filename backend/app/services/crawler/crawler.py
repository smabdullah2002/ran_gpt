import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from crawlee import ConcurrencySettings
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext


async def crawl_to_csv(
    base_url: str,
    output_path: str | Path = "crawl_output.csv",
    max_pages: int = 50,
    max_concurrency: int = 10,
) -> list[dict]:

    results: list[dict] = []
    output_path = Path(output_path)

    crawler = PlaywrightCrawler(
        headless=True,
        browser_type="chromium",
        max_requests_per_crawl=max_pages,
        concurrency_settings=ConcurrencySettings(
            min_concurrency=1,
            max_concurrency=max_concurrency,
            desired_concurrency=max_concurrency,
        ),
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext):
        url = context.request.url
        print(f"[{len(results) + 1}/{max_pages}] Visiting: {url}")

        try:
            title = await context.page.title()
            results.append(
                {
                    "url": url,
                    "title": title,
                    "crawl_timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(f"  Title: {title}")
        except Exception as e:
            print(f"  ERROR on {url}: {e}")

        await context.enqueue_links(selector="a[href]", strategy="same-domain")

    print(
        f"\nCrawling {base_url} (max {max_pages} pages, concurrency={max_concurrency})...\n"
    )
    await crawler.run([base_url])

    df = pd.DataFrame(results)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\nDone. {len(results)} pages saved to {output_path}")

    return results


if __name__ == "__main__":
    asyncio.run(crawl_to_csv("https://web-scraping.dev/"))
