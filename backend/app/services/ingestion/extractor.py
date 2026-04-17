from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .detector import discover_api_candidates


@dataclass
class ExtractionResult:
    text: str
    title: str
    method: str
    content_hash: str


class ContentExtractor:
    def __init__(self, request_timeout_seconds: int = 15, retries: int = 2) -> None:
        self.request_timeout_seconds = request_timeout_seconds
        self.retries = retries
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    async def fetch_raw_html(self, url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            for attempt in range(self.retries + 1):
                try:
                    async with session.get(url, allow_redirects=True) as response:
                        if response.status >= 400:
                            if attempt < self.retries:
                                await asyncio.sleep(0.3 * (attempt + 1))
                                continue
                            raise RuntimeError(f"HTTP {response.status} for {url}")
                        text = await response.text(errors="ignore")
                        if _is_homepage(url):
                            print(response.status)
                            print(text[:1000])
                        return text
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    if attempt < self.retries:
                        await asyncio.sleep(0.3 * (attempt + 1))
                        continue
                    raise RuntimeError(f"Failed to fetch HTML for {url}: {exc}") from exc

        raise RuntimeError(f"Failed to fetch HTML for {url}")

    async def extract_with_browser(self, url: str) -> ExtractionResult:
        raw_html = ""
        try:
            rendered_html = await self._render_with_browser(url)
            return self._extract_from_static_html(url, rendered_html, forced_method="browser")
        except Exception:
            # Browser rendering may be unavailable on some Windows loop policies.
            try:
                raw_html = await self.fetch_raw_html(url)
                return self._extract_from_static_html(url, raw_html, forced_method="html")
            except Exception:
                return self._extract_lenient_fallback(url, raw_html, forced_method="html_fallback")

    async def extract(self, url: str, raw_html: str, content_type: str) -> ExtractionResult:
        if content_type == "api":
            try:
                return await self._extract_from_api(url, raw_html)
            except Exception:
                pass

        if content_type in {"api", "static"}:
            try:
                return self._extract_from_static_html(url, raw_html)
            except Exception:
                if content_type == "static":
                    raise

        try:
            rendered_html = await self._render_with_browser(url)
            return self._extract_from_static_html(url, rendered_html, forced_method="browser")
        except Exception:
            # If Playwright cannot launch, keep ingestion alive with static extraction.
            try:
                return self._extract_from_static_html(url, raw_html, forced_method="html")
            except Exception:
                return self._extract_lenient_fallback(url, raw_html, forced_method="html_fallback")

    async def _extract_from_api(self, page_url: str, raw_html: str) -> ExtractionResult:
        candidates = discover_api_candidates(page_url, raw_html)
        if not candidates:
            raise RuntimeError("No API endpoint candidates found")

        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            for candidate in candidates[:8]:
                endpoint = candidate if candidate.startswith("http") else urljoin(page_url, candidate)
                try:
                    async with session.get(endpoint, allow_redirects=True) as response:
                        if response.status >= 400:
                            continue
                        response_content_type = response.headers.get("Content-Type", "").lower()
                        if "json" not in response_content_type:
                            continue
                        payload = await response.json(content_type=None)
                        text = _json_to_text(payload)
                        if len(text.split()) < 30:
                            continue
                        title = _extract_title_from_html(raw_html) or endpoint
                        content_hash = _sha256(text)
                        return ExtractionResult(text=text, title=title, method="api", content_hash=content_hash)
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                    continue

        raise RuntimeError("API extraction did not return usable JSON content")

    def _extract_from_static_html(
        self, url: str, html: str, forced_method: str | None = None
    ) -> ExtractionResult:
        soup = BeautifulSoup(html, "html.parser")

        for tag_name in ["script", "style"]:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        main_node = soup.find("article") or soup.find("main") or soup.body
        if main_node is None:
            raise RuntimeError(f"No extractable HTML body for {url}")

        text = main_node.get_text("\n", strip=True)
        raw_body_text = soup.get_text("\n", strip=True)
        if len(text) < 100:
            text = raw_body_text

        if len(text) < 50:
            text = soup.get_text("\n", strip=True)

        if len(text) < 50:
            raise RuntimeError("Static HTML has insufficient content")

        title = _extract_title_from_html(html) or url
        method = forced_method or "html"
        content_hash = _sha256(text)

        return ExtractionResult(text=text, title=title, method=method, content_hash=content_hash)

    def _extract_lenient_fallback(
        self,
        url: str,
        html: str,
        forced_method: str = "html_fallback",
    ) -> ExtractionResult:
        soup = BeautifulSoup(html, "html.parser")

        for tag_name in ["script", "style", "noscript"]:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        text = soup.get_text("\n", strip=True)
        if not text:
            text = _extract_title_from_html(html) or url

        return ExtractionResult(
            text=text,
            title=_extract_title_from_html(html) or url,
            method=forced_method,
            content_hash=_sha256(text),
        )

    async def _render_with_browser(self, url: str) -> str:
        _ensure_subprocess_supported_loop()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for dynamic pages. Install with: pip install playwright"
            ) from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            content = await page.content()
            await browser.close()
            return content


def _json_to_text(payload: Any, indent: int = 0) -> str:
    prefix = "  " * indent

    if isinstance(payload, dict):
        lines: list[str] = []
        for key, value in payload.items():
            nested = _json_to_text(value, indent + 1)
            if "\n" in nested:
                lines.append(f"{prefix}{key}:")
                lines.append(nested)
            else:
                lines.append(f"{prefix}{key}: {nested.strip()}")
        return "\n".join(lines)

    if isinstance(payload, list):
        lines = [f"{prefix}- {_json_to_text(item, indent + 1).strip()}" for item in payload]
        return "\n".join(lines)

    return f"{prefix}{payload}"


def _extract_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else ""


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_homepage(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return path == "/" and not parsed.query


def _ensure_subprocess_supported_loop() -> None:
    if sys.platform != "win32":
        return

    loop = asyncio.get_running_loop()
    if isinstance(loop, asyncio.SelectorEventLoop):
        raise RuntimeError(
            "Playwright browser rendering requires Windows ProactorEventLoop. "
            "Restart server with WindowsProactorEventLoopPolicy enabled."
        )
