import re
from xml.etree import ElementTree as ET

import aiohttp

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap/",
]


async def fetch_sitemap(sitemap_url: str) -> list[str] | None:
    """Fetch an exact sitemap URL. No auto-discovery, no fallback."""
    print(f"Fetching sitemap: {sitemap_url}")
    return await _fetch_and_parse_sitemap(sitemap_url.rstrip("/"))


async def try_sitemap(base_url: str) -> list[str] | None:
    base_url = base_url.rstrip("/")

    urls = await _try_robots_txt(base_url)
    if urls:
        print(f"Found sitemap in robots.txt — {len(urls)} URLs")
        return urls

    for path in SITEMAP_PATHS:
        sm_url = base_url + path
        urls = await _fetch_and_parse_sitemap(sm_url)
        if urls:
            print(f"Found sitemap at {path} — {len(urls)} URLs")
            return urls

    return None


async def _try_robots_txt(base_url: str) -> list[str] | None:
    robots_url = base_url + "/robots.txt"
    text = await _fetch_with_aiohttp(robots_url)
    if text is None:
        return None

    sitemap_urls = re.findall(
        r"^Sitemap:\s*(\S+)", text, re.IGNORECASE | re.MULTILINE
    )
    if not sitemap_urls:
        return None

    all_urls: list[str] = []
    for sm_url in sitemap_urls:
        urls = await _fetch_and_parse_sitemap(sm_url.strip())
        if urls:
            all_urls.extend(urls)

    return all_urls if all_urls else None


async def _fetch_with_aiohttp(url: str) -> str | None:
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
    except Exception:
        return None


async def _fetch_with_playwright(url: str) -> str | None:
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=15000
            )
            if response and response.status == 200:
                body = await response.body()
                await browser.close()
                return body.decode("utf-8")
            await browser.close()
            return None
    except Exception:
        return None


async def _fetch_and_parse_sitemap(sitemap_url: str) -> list[str] | None:
    xml = await _fetch_with_aiohttp(sitemap_url)
    if xml is None:
        xml = await _fetch_with_playwright(sitemap_url)

    if xml is None:
        return None

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    ns = _extract_ns(root.tag)
    local_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    loc_tag = f"{{{ns}}}loc" if ns else "loc"

    if local_tag == "sitemapindex":
        all_urls: list[str] = []
        for loc in root.iter(loc_tag):
            if loc.text:
                child_urls = await _fetch_and_parse_sitemap(loc.text.strip())
                if child_urls:
                    all_urls.extend(child_urls)
        return all_urls if all_urls else None

    urls = [loc.text.strip() for loc in root.iter(loc_tag) if loc.text]
    return urls if urls else None


def parse_sitemap_xml(xml: str) -> list[str] | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    ns = _extract_ns(root.tag)
    tag = f"{{{ns}}}loc" if ns else "loc"

    urls = [loc.text.strip() for loc in root.iter(tag) if loc.text]
    return urls if urls else None


def _extract_ns(tag: str) -> str:
    m = re.match(r"\{([^}]+)\}", tag)
    return m.group(1) if m else ""
