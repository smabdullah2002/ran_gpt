import asyncio
import random
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROXY_FILE = Path(__file__).parent / "app" / "webshare_proxy_list.txt"


def load_proxies() -> list[dict]:
    if not PROXY_FILE.exists():
        print(f"[WARN] Proxy file not found at {PROXY_FILE}")
        return []

    proxies: list[dict] = []
    with open(PROXY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) == 4:
                host, port, username, password = parts
                proxies.append(
                    {
                        "host": host,
                        "port": int(port),
                        "username": username,
                        "password": password,
                    }
                )
    return proxies


def get_random_proxy() -> dict | None:
    proxies = load_proxies()
    return random.choice(proxies) if proxies else None


async def test_no_proxy(url: str):
    print(f"\n--- No proxy ---")
    print(f"URL: {url}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        response = await page.goto(url, wait_until="domcontentloaded")

        status = response.status if response else "N/A"
        title = await page.title()
        print(f"Status: {status}")
        print(f"Title: {title}")

        if status == 403:
            print(">>> BLOCKED (403 Forbidden)")

        print("\nBrowser will stay open for 15s so you can inspect the page...")
        await page.wait_for_timeout(15000)
        await browser.close()


async def test_with_proxy(url: str):
    proxy = get_random_proxy()
    if not proxy:
        print("[WARN] No proxy available, falling back to no proxy")
        return await test_no_proxy(url)

    print(f"\n--- With proxy ({proxy['host']}:{proxy['port']}) ---")
    print(f"URL: {url}\n")

    proxy_config = {
        "server": f"http://{proxy['host']}:{proxy['port']}",
        "username": proxy["username"],
        "password": proxy["password"],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(proxy=proxy_config)
        response = await page.goto(url, wait_until="domcontentloaded")

        status = response.status if response else "N/A"
        title = await page.title()
        print(f"Status: {status}")
        print(f"Title: {title}")

        if status == 403:
            print(">>> BLOCKED (403 Forbidden)")

        print("\nBrowser will stay open for 15s so you can inspect the page...")
        await page.wait_for_timeout(15000)
        await browser.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.studyfetch.com/"

    choice = input("Test with proxy? (y/n): ").strip().lower()
    if choice == "y":
        asyncio.run(test_with_proxy(url))
    else:
        asyncio.run(test_no_proxy(url))
