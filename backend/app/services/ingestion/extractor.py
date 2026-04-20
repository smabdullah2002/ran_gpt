from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .detector import discover_api_candidates


logger = logging.getLogger(__name__)


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
        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            candidates = await self._discover_api_candidates_with_external_scripts(
                page_url=page_url,
                raw_html=raw_html,
                session=session,
            )

            if not candidates:
                raise RuntimeError("No API endpoint candidates found")

            ordered_candidates = sorted(
                candidates,
                key=lambda candidate: _rank_api_candidate(page_url, candidate),
                reverse=True,
            )

            for candidate in ordered_candidates[:16]:
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

    async def _discover_api_candidates_with_external_scripts(
        self,
        page_url: str,
        raw_html: str,
        session: aiohttp.ClientSession,
    ) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add(candidate: str) -> None:
            if not candidate:
                return
            if candidate in seen:
                return
            seen.add(candidate)
            candidates.append(candidate)

        for candidate in discover_api_candidates(page_url, raw_html):
            add(candidate)

        soup = BeautifulSoup(raw_html, "html.parser")
        script_urls: list[str] = []
        for script in soup.find_all("script", src=True):
            src = str(script.get("src", "")).strip()
            if not src:
                continue
            script_urls.append(urljoin(page_url, src))

        backend_bases: list[str] = []

        for script_url in script_urls[:4]:
            try:
                async with session.get(script_url, allow_redirects=True) as response:
                    if response.status >= 400:
                        continue
                    javascript = await response.text(errors="ignore")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue

            for base in re.findall(r"https?://[^\"'`\s,;)]*/api", javascript, flags=re.IGNORECASE):
                normalized_base = base.rstrip("/")
                if normalized_base not in backend_bases:
                    backend_bases.append(normalized_base)

            for pattern in [
                r"\.get\(\s*[`\"']([^`\"']+)[`\"']",
                r"\.post\(\s*[`\"']([^`\"']+)[`\"']",
                r"\.put\(\s*[`\"']([^`\"']+)[`\"']",
                r"\.patch\(\s*[`\"']([^`\"']+)[`\"']",
                r"\.delete\(\s*[`\"']([^`\"']+)[`\"']",
            ]:
                for endpoint_path in re.findall(pattern, javascript):
                    lowered = endpoint_path.lower()
                    if not lowered.startswith("/"):
                        continue
                    if any(token in lowered for token in ["/auth/", "/admin", "/login", "/register"]):
                        continue
                    if "${" in endpoint_path:
                        continue
                    for base in backend_bases:
                        add(f"{base}{endpoint_path}")

        for base in backend_bases:
            for guessed in _guess_spa_api_paths(page_url):
                add(f"{base}{guessed}")

        return candidates

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
            hint_text = _extract_semantic_hints(soup)
            if hint_text:
                text = hint_text

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
        if not _is_playwright_loop_supported():
            raise RuntimeError(
                "Playwright browser rendering is disabled on Windows SelectorEventLoop."
            )

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


def _extract_semantic_hints(soup: BeautifulSoup) -> str:
    hint_values: list[str] = []

    for attr in ["description", "og:description", "twitter:description", "keywords", "author"]:
        tag = soup.find("meta", attrs={"name": attr}) or soup.find("meta", attrs={"property": attr})
        if tag and tag.get("content"):
            hint_values.append(str(tag.get("content", "")).strip())

    title_tag = soup.find("title")
    if title_tag:
        hint_values.append(title_tag.get_text(strip=True))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in hint_values:
        compact = " ".join(value.split()).strip()
        if not compact:
            continue
        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(compact)

    return "\n".join(deduped)


def _guess_spa_api_paths(page_url: str) -> list[str]:
    lowered = page_url.lower()

    if "/about" in lowered or lowered.rstrip("/").endswith("mmsblog.com"):
        return ["/author", "/posts/recent?limit=5"]
    if "/blog" in lowered or "/articles" in lowered:
        return ["/posts?page=1&limit=30", "/posts?limit=10", "/posts/recent?limit=5"]
    if "/gallery" in lowered:
        return ["/gallery"]
    if "/interview" in lowered:
        return ["/interviews"]

    return ["/posts/recent?limit=5", "/posts?limit=10"]


def _rank_api_candidate(page_url: str, candidate: str) -> int:
    lowered_url = page_url.lower()
    lowered_candidate = candidate.lower()

    score = 0

    if any(token in lowered_candidate for token in ["/auth/", "/admin", "/login", "/register"]):
        return -100

    if "/about" in lowered_url or lowered_url.rstrip("/").endswith("mmsblog.com"):
        if "/author" in lowered_candidate:
            score += 120
        if "/posts/recent" in lowered_candidate:
            score += 60
        if "/posts" in lowered_candidate:
            score += 20

    if "/blog" in lowered_url or "/articles" in lowered_url:
        if "/posts" in lowered_candidate:
            score += 120
        if "/interviews" in lowered_candidate:
            score += 40

    if "/gallery" in lowered_url and "/gallery" in lowered_candidate:
        score += 120

    if "/interview" in lowered_url and "/interviews" in lowered_candidate:
        score += 120

    if "/posts" in lowered_candidate:
        score += 10
    if "/author" in lowered_candidate:
        score += 10
    if "/gallery" in lowered_candidate:
        score += 10
    if "/interviews" in lowered_candidate:
        score += 10

    return score


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_playwright_loop_supported() -> bool:
    if sys.platform != "win32":
        return True

    loop = asyncio.get_running_loop()
    if isinstance(loop, asyncio.SelectorEventLoop):
        logger.debug(
            "Detected Windows SelectorEventLoop; skipping Playwright browser rendering."
        )
        return False

    return True
