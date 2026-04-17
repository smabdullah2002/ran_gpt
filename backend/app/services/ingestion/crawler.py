from __future__ import annotations

import asyncio
import heapq
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urldefrag, urlparse
import xml.etree.ElementTree as ET

import aiohttp
from bs4 import BeautifulSoup


@dataclass
class CrawlConfig:
    max_depth: int = 2
    max_pages: int = 50
    rate_limit_seconds: float = 0.2
    request_timeout_seconds: int = 12
    max_concurrency: int = 8
    per_domain_concurrency: int = 3
    retries: int = 2


@dataclass
class CrawlResult:
    urls: list[str]
    diagnostics: dict[str, object]


def normalize_url(url: str) -> str:
    clean, _ = urldefrag(url)
    parsed = urlparse(clean)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_junk_query_key(key)
    ]
    query = urlencode(query_pairs)
    return parsed._replace(path=path, query=query, fragment="").geturl()


def _normalized_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_internal_link(root_url: str, candidate_url: str) -> bool:
    return _normalized_host(root_url) == _normalized_host(candidate_url)


def _is_junk_query_key(key: str) -> bool:
    lowered = key.lower()
    if lowered.startswith("utm_"):
        return True
    return lowered in {
        "fbclid",
        "gclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "source",
        "igshid",
    }


def _is_disallowed_path(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    blocked_tokens = ("/login", "/signup", "/cart", "/admin")
    return any(token in path for token in blocked_tokens)


class URLCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
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
        self.global_semaphore = asyncio.Semaphore(config.max_concurrency)
        self.domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self.domain_next_allowed: dict[str, float] = {}
        self.domain_penalty: dict[str, float] = {}
        self._domain_lock = asyncio.Lock()
        self._playwright = None
        self._browser = None

    async def crawl(self, root_url: str) -> CrawlResult:
        root_url = normalize_url(root_url)
        crawled_urls: list[str] = []
        visited: set[str] = set()
        enqueued: set[str] = set()
        discovered_internal_links = 0
        link_counts: dict[str, int] = {}
        sitemap_attempted = True
        sitemap_failed = False
        homepage_status_code: int | None = None
        homepage_html_link_count = 0
        homepage_browser_link_count = 0
        forced_browser_homepage_crawl = False
        browser_render_count = 0
        heuristic_seed_count = 0

        sitemap_urls, sitemap_non_200_urls = await self._discover_sitemap_urls(root_url)
        if not sitemap_urls:
            sitemap_failed = True
        sitemap_exists = len(sitemap_urls) > 0
        sitemap_set = set(sitemap_urls)

        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_seconds)

        initial_discovery_urls: list[str] = []
        discovery_connector = aiohttp.TCPConnector(limit=max(self.config.max_concurrency * 2, 20))
        async with aiohttp.ClientSession(timeout=timeout, connector=discovery_connector, headers=self.headers) as session:
            if sitemap_exists:
                initial_discovery_urls.extend(sitemap_urls)

            if not sitemap_exists:
                homepage_status_code, homepage_html = await self._fetch_text(session, root_url)
                if _is_usable_response(homepage_status_code, homepage_html):
                    html_links = self._extract_links(homepage_html, root_url)
                    homepage_html_link_count = len(html_links)
                    initial_discovery_urls.extend(html_links)

                    if (
                        len(homepage_html) < 2000
                        or homepage_html_link_count == 0
                        or _looks_js_rendered(homepage_html)
                    ):
                        browser_links = await self._extract_links_with_browser(root_url)
                        if browser_links:
                            browser_render_count += 1
                        homepage_browser_link_count = len(browser_links)
                        initial_discovery_urls.extend(browser_links)

                # Critical zero-page prevention rule.
                if not initial_discovery_urls:
                    forced_browser_homepage_crawl = True
                    browser_links = await self._extract_links_with_browser(root_url)
                    if browser_links:
                        browser_render_count += 1
                    homepage_browser_link_count = max(homepage_browser_link_count, len(browser_links))
                    initial_discovery_urls.extend(browser_links)

                if not initial_discovery_urls:
                    heuristic_urls = self._heuristic_seed_urls(root_url)
                    heuristic_seed_count = len(heuristic_urls)
                    initial_discovery_urls.extend(heuristic_urls)

            # Final fallback to guarantee at least one target if reachable.
            if root_url not in initial_discovery_urls:
                initial_discovery_urls.insert(0, root_url)

        queue: list[tuple[int, int, str, int, bool]] = []
        order = 0
        state_lock = asyncio.Lock()

        def enqueue(url: str, priority: int, depth: int, from_sitemap: bool) -> None:
            nonlocal order
            normalized = normalize_url(url)
            if normalized in enqueued:
                return
            if not _is_internal_link(root_url, normalized):
                return
            if _is_disallowed_path(normalized):
                return
            enqueued.add(normalized)
            heapq.heappush(queue, (priority, order, normalized, depth, from_sitemap))
            order += 1

        if sitemap_exists:
            for sitemap_url in sitemap_urls:
                enqueue(sitemap_url, priority=0, depth=0, from_sitemap=True)

        for discovered_url in initial_discovery_urls:
            enqueue(discovered_url, priority=1, depth=0, from_sitemap=False)

        try:
            crawl_connector = aiohttp.TCPConnector(limit=max(self.config.max_concurrency * 2, 20))
            async with aiohttp.ClientSession(timeout=timeout, connector=crawl_connector, headers=self.headers) as session:
                while queue and len(crawled_urls) < self.config.max_pages:
                    _, _, current_url, depth, from_sitemap = heapq.heappop(queue)

                    async with state_lock:
                        if current_url in visited:
                            continue
                        visited.add(current_url)

                    status_code, body = await self._fetch_text(session, current_url)
                    if not _is_usable_response(status_code, body):
                        if from_sitemap and status_code not in (None, 200):
                            sitemap_non_200_urls.append(current_url)
                        continue

                    async with state_lock:
                        if len(crawled_urls) >= self.config.max_pages:
                            continue
                        crawled_urls.append(current_url)

                    if depth >= self.config.max_depth:
                        continue

                    links = self._extract_links(body, current_url)
                    if (len(body) < 2000 or not links or _looks_js_rendered(body)) and len(crawled_urls) < self.config.max_pages:
                        browser_links = await self._extract_links_with_browser(current_url)
                        if browser_links:
                            browser_render_count += 1
                            links.extend(browser_links)

                    unique_links = list(dict.fromkeys(links))
                    link_counts[current_url] = len(unique_links)

                    for link in unique_links:
                        normalized = normalize_url(link)
                        if normalized in visited:
                            continue
                        if sitemap_exists and normalized in sitemap_set:
                            continue
                        enqueue(
                            normalized,
                            priority=1,
                            depth=depth + 1,
                            from_sitemap=False,
                        )
                        discovered_internal_links += 1
        finally:
            await self._close_browser()

        diagnostics = {
            "sitemap_attempted": sitemap_attempted,
            "sitemap_enabled": sitemap_exists,
            "sitemap_failed": sitemap_failed,
            "sitemap_url_count": len(sitemap_urls),
            "sitemap_seed_count": len([url for url in sitemap_urls if url in enqueued]),
            "sitemap_urls": sitemap_urls,
            "non_200_sitemap_urls": sitemap_non_200_urls,
            "homepage_status_code": homepage_status_code,
            "homepage_html_link_count": homepage_html_link_count,
            "homepage_browser_link_count": homepage_browser_link_count,
            "forced_browser_homepage_crawl": forced_browser_homepage_crawl,
            "heuristic_seed_count": heuristic_seed_count,
            "browser_render_count": browser_render_count,
            "per_page_link_counts": link_counts,
            "discovered_internal_links": discovered_internal_links,
            "max_pages_limit": self.config.max_pages,
            "max_depth_guard": self.config.max_depth,
            "max_concurrency": self.config.max_concurrency,
            "per_domain_concurrency": self.config.per_domain_concurrency,
        }

        return CrawlResult(urls=crawled_urls, diagnostics=diagnostics)

    async def _discover_sitemap_urls(self, root_url: str) -> tuple[list[str], list[str]]:
        parsed = urlparse(root_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        sitemap_candidates = [
            urljoin(base + "/", "sitemap.xml"),
            urljoin(base + "/", "sitemap_index.xml"),
        ]

        seen: set[str] = set()
        collected: list[str] = []
        non_200_sitemap_urls: list[str] = []

        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_seconds)
        connector = aiohttp.TCPConnector(limit=max(self.config.max_concurrency, 10))

        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=self.headers) as session:
            robots_sitemaps = await self._discover_sitemaps_from_robots(session, base)
            for robots_sitemap in robots_sitemaps:
                if robots_sitemap not in sitemap_candidates:
                    sitemap_candidates.append(robots_sitemap)

            for sitemap_url in sitemap_candidates:
                found_urls = await self._parse_sitemap_recursive(
                    session=session,
                    sitemap_url=sitemap_url,
                    root_url=root_url,
                    seen_sitemaps=seen,
                    non_200_sitemap_urls=non_200_sitemap_urls,
                )
                for url in found_urls:
                    normalized = normalize_url(url)
                    if normalized in seen:
                        continue
                    if not _is_internal_link(root_url, normalized):
                        continue
                    seen.add(normalized)
                    collected.append(normalized)

        return collected, non_200_sitemap_urls

    async def _parse_sitemap_recursive(
        self,
        session: aiohttp.ClientSession,
        sitemap_url: str,
        root_url: str,
        seen_sitemaps: set[str],
        non_200_sitemap_urls: list[str] | None = None,
    ) -> list[str]:
        sitemap_url = normalize_url(sitemap_url)
        if sitemap_url in seen_sitemaps:
            return []
        seen_sitemaps.add(sitemap_url)

        status_code, body, content_type = await self._fetch_text(session, sitemap_url, include_content_type=True)
        if status_code is None or not body:
            return []

        if status_code != 200:
            if non_200_sitemap_urls is not None:
                non_200_sitemap_urls.append(sitemap_url)
            return []

        if "xml" not in content_type and "<urlset" not in body and "<sitemapindex" not in body:
            return []

        urls: list[str] = []

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []

        tag = root.tag.lower()
        if tag.endswith("sitemapindex"):
            for child in root:
                if not child.tag.lower().endswith("sitemap"):
                    continue
                loc = _find_child_text_by_suffix(child, "loc")
                if not loc:
                    continue
                nested = await self._parse_sitemap_recursive(
                    session=session,
                    sitemap_url=loc,
                    root_url=root_url,
                    seen_sitemaps=seen_sitemaps,
                    non_200_sitemap_urls=non_200_sitemap_urls,
                )
                urls.extend(nested)

        if tag.endswith("urlset"):
            for child in root:
                if not child.tag.lower().endswith("url"):
                    continue
                loc = _find_child_text_by_suffix(child, "loc")
                if not loc:
                    continue
                if _is_internal_link(root_url, loc):
                    urls.append(loc)

        return urls

    async def _fetch_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        include_content_type: bool = False,
    ) -> tuple[int | None, str | None, str] | tuple[int | None, str | None]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        async with self._domain_lock:
            if domain not in self.domain_semaphores:
                self.domain_semaphores[domain] = asyncio.Semaphore(self.config.per_domain_concurrency)

        domain_sem = self.domain_semaphores[domain]
        content_type = ""

        for attempt in range(self.config.retries + 1):
            await self._respect_domain_delay(domain)
            try:
                async with self.global_semaphore, domain_sem:
                    async with session.get(url, allow_redirects=True) as response:
                        text = await response.text(errors="ignore")
                        content_type = response.headers.get("Content-Type", "").lower()

                        if response.status in {429, 500, 502, 503, 504} and attempt < self.config.retries:
                            await self._register_failure(domain)
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue

                        await self._register_success(domain)
                        if include_content_type:
                            return response.status, text, content_type
                        return response.status, text
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await self._register_failure(domain)
                if attempt < self.config.retries:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
                if include_content_type:
                    return None, None, ""
                return None, None

        if include_content_type:
            return None, None, ""
        return None, None

    async def _discover_sitemaps_from_robots(self, session: aiohttp.ClientSession, base: str) -> list[str]:
        robots_url = urljoin(base + "/", "robots.txt")
        status_code, body = await self._fetch_text(session, robots_url)
        if status_code != 200 or not body:
            return []

        sitemaps: list[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped.lower().startswith("sitemap:"):
                continue
            candidate = stripped.split(":", 1)[1].strip()
            if not candidate:
                continue
            resolved = normalize_url(urljoin(base + "/", candidate))
            sitemaps.append(resolved)
        return sitemaps

    async def _respect_domain_delay(self, domain: str) -> None:
        async with self._domain_lock:
            next_allowed = self.domain_next_allowed.get(domain, 0.0)
            now = asyncio.get_running_loop().time()
            if next_allowed > now:
                sleep_for = next_allowed - now
            else:
                sleep_for = 0.0

        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    async def _register_success(self, domain: str) -> None:
        async with self._domain_lock:
            penalty = max(0.0, self.domain_penalty.get(domain, 0.0) - 0.1)
            self.domain_penalty[domain] = penalty
            self.domain_next_allowed[domain] = (
                asyncio.get_running_loop().time()
                + self.config.rate_limit_seconds * (1.0 + penalty)
            )

    async def _register_failure(self, domain: str) -> None:
        async with self._domain_lock:
            penalty = min(2.0, self.domain_penalty.get(domain, 0.0) + 0.25)
            self.domain_penalty[domain] = penalty
            self.domain_next_allowed[domain] = (
                asyncio.get_running_loop().time()
                + self.config.rate_limit_seconds * (1.0 + penalty)
            )

    @staticmethod
    def _extract_links(html: str, base_url: str) -> list[str]:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        links: set[str] = set()

        def add_candidate(raw_url: str) -> None:
            href = (raw_url or "").strip()
            if not href:
                return
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                return

            resolved = urljoin(base_url, href)
            parsed = urlparse(resolved)
            if parsed.scheme not in {"http", "https"}:
                return

            normalized = normalize_url(resolved)
            if not _is_internal_link(base_url, normalized):
                return
            if _is_disallowed_path(normalized):
                return
            if _has_static_asset_extension(normalized):
                return

            links.add(normalized)

        # 1) Primary page links.
        for tag in soup.find_all(["a", "area"], href=True):
            add_candidate(tag.get("href", ""))

        # 2) Modern routing hints from link tags.
        for tag in soup.find_all("link", href=True):
            rel_values = {value.lower() for value in (tag.get("rel") or []) if isinstance(value, str)}
            if rel_values.intersection({"canonical", "alternate", "next", "prev"}):
                add_candidate(tag.get("href", ""))

        # 3) Framework-emitted URL attributes.
        for attr_name in ["data-href", "data-url", "data-link", "data-path", "routerlink", "xlink:href"]:
            for tag in soup.find_all(attrs={attr_name: True}):
                add_candidate(str(tag.get(attr_name, "")))

        # 4) Lightweight route extraction from inline scripts.
        route_pattern = re.compile(r"['\"](/(?!/)[A-Za-z0-9_\-./]{2,120})['\"]")
        for script in soup.find_all("script"):
            if script.get("src"):
                continue
            inline = script.string or script.get_text(" ", strip=True)
            if not inline:
                continue
            for match in route_pattern.findall(inline):
                route = match[0]
                if _looks_like_discoverable_route(route):
                    add_candidate(route)

        return list(links)

    @staticmethod
    def _heuristic_seed_urls(root_url: str) -> list[str]:
        parsed = urlparse(root_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        candidates = [
            "/blog",
            "/blogs",
            "/posts",
            "/articles",
            "/news",
            "/docs",
            "/documentation",
            "/help",
            "/support",
            "/about",
            "/contact",
            "/team",
            "/resources",
            "/guides",
            "/tutorials",
            "/faq",
            "/knowledge-base",
            "/feed",
            "/rss",
        ]
        return [normalize_url(urljoin(base + "/", path.lstrip("/"))) for path in candidates]

    async def _extract_links_with_browser(self, url: str) -> list[str]:
        rendered_html = await self._render_html_with_browser(url)
        if not rendered_html:
            return []
        return self._extract_links(rendered_html, url)

    async def _render_html_with_browser(self, url: str) -> str | None:
        try:
            browser = await self._ensure_browser()
            if browser is None:
                return None

            page = await browser.new_page(user_agent=self.headers["User-Agent"])
            try:
                await page.goto(url, wait_until="networkidle", timeout=25000)
                return await page.content()
            finally:
                await page.close()
        except Exception:
            return None

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser
        except Exception:
            self._playwright = None
            self._browser = None
            return None

    async def _close_browser(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


def _find_child_text_by_suffix(node: ET.Element, suffix: str) -> str | None:
    suffix = suffix.lower()
    for child in node:
        if child.tag.lower().endswith(suffix) and child.text:
            return child.text.strip()
    return None


def _looks_js_rendered(html: str) -> bool:
    lowered = html.lower()
    if "<noscript" in lowered and "enable javascript" in lowered:
        return True

    app_shell_markers = ["id=\"root\"", "id=\"app\"", "id='root'", "id='app'"]
    marker_present = any(marker in lowered for marker in app_shell_markers)
    text_like_tokens = sum(1 for token in ["<p", "<article", "<section", "<h1", "<h2"] if token in lowered)
    script_count = lowered.count("<script")
    return marker_present and script_count >= 3 and text_like_tokens <= 1


def _is_usable_response(status_code: int | None, body: str | None) -> bool:
    if not body:
        return False
    if status_code == 200:
        return True
    return status_code in {401, 403} and len(body) >= 500


def _has_static_asset_extension(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    static_exts = (
        ".css",
        ".js",
        ".mjs",
        ".json",
        ".map",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".pdf",
        ".zip",
        ".gz",
        ".mp4",
        ".mp3",
        ".xml",
    )
    return path.endswith(static_exts)


def _looks_like_discoverable_route(route: str) -> bool:
    lowered = route.lower()
    if lowered in {"/", "/#", "/api", "/graphql"}:
        return False
    if "/api/" in lowered:
        return False
    if _has_static_asset_extension(route):
        return False
    return True
