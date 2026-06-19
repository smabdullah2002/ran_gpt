import argparse
import asyncio

from app.services.crawler.crawler import crawl_to_csv, crawl_from_sitemap
from app.services.crawler.scraper import scrape_csv


async def main():
    parser = argparse.ArgumentParser(
        prog="ran-gpt",
        description="Ingest website content for AI training",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    crawl_p = sub.add_parser("crawl", help="Crawl a website using Crawlee")
    crawl_p.add_argument("url", help="Website URL to crawl")
    crawl_p.add_argument("--max-pages", type=int, default=50, help="Max pages to visit (default: 50)")
    crawl_p.add_argument("--output", default="crawl_output.csv", help="Output CSV file")

    sitemap_p = sub.add_parser("sitemap", help="Fetch URLs from a sitemap")
    sitemap_p.add_argument("url", help="Sitemap URL (e.g., https://example.com/sitemap.xml)")
    sitemap_p.add_argument("--output", default="crawl_output.csv", help="Output CSV file")

    scrape_p = sub.add_parser("scrape", help="Visit URLs and extract clean text")
    scrape_p.add_argument("--input", default="crawl_output.csv", help="CSV with URLs to scrape (default: crawl_output.csv)")
    scrape_p.add_argument("--output", default="scraped_output.csv", help="Output CSV with cleaned text (default: scraped_output.csv)")
    scrape_p.add_argument("--max-concurrency", type=int, default=5, help="Concurrent scrapes (default: 5)")

    args = parser.parse_args()

    if args.mode == "crawl":
        await crawl_to_csv(args.url, args.output, max_pages=args.max_pages)
    elif args.mode == "sitemap":
        await crawl_from_sitemap(args.url, args.output)
    else:
        await scrape_csv(args.input, args.output, max_concurrency=args.max_concurrency)


if __name__ == "__main__":
    asyncio.run(main())
