from __future__ import annotations

import json
import re
from typing import Literal

from bs4 import BeautifulSoup

ContentType = Literal["api", "static", "dynamic"]

API_HINT_PATTERNS = [
    re.compile(r"/api/", re.IGNORECASE),
    re.compile(r"\.json([/?#]|$)", re.IGNORECASE),
    re.compile(r"graphql", re.IGNORECASE),
    re.compile(r"\bfetch\(", re.IGNORECASE),
    re.compile(r"XMLHttpRequest", re.IGNORECASE),
]

FRAMEWORK_HINT_PATTERNS = [
    re.compile(r"data-reactroot", re.IGNORECASE),
    re.compile(r"__NEXT_DATA__", re.IGNORECASE),
    re.compile(r"window\.__NUXT__", re.IGNORECASE),
    re.compile(r"id=\"root\"", re.IGNORECASE),
    re.compile(r"id=\"app\"", re.IGNORECASE),
]


def detect_content_type(url: str, html: str) -> ContentType:
    soup = BeautifulSoup(html, "html.parser")

    api_score = _score_api_indicators(soup, html)
    static_score = _score_static_indicators(soup)
    dynamic_score = _score_dynamic_indicators(soup, html)

    if api_score >= 2:
        return "api"
    if static_score >= dynamic_score:
        return "static"
    return "dynamic"


def discover_api_candidates(page_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: set[str] = set()

    for script in soup.find_all("script"):
        script_text = script.get_text(" ", strip=True)
        if not script_text:
            continue

        for pattern in [r"https?://[^\"'\s]+", r"/api/[^\"'\s]+", r"/graphql[^\"'\s]*"]:
            for match in re.findall(pattern, script_text, flags=re.IGNORECASE):
                candidates.add(match)

        json_blob = _extract_json_from_script(script_text)
        if isinstance(json_blob, dict):
            _collect_json_endpoints(json_blob, candidates)

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if any(token in href.lower() for token in ["/api/", "graphql", ".json"]):
            candidates.add(href)

    return sorted(candidates)


def _score_api_indicators(soup: BeautifulSoup, html: str) -> int:
    score = 0
    compact = html[:150000]

    if any(pattern.search(compact) for pattern in API_HINT_PATTERNS):
        score += 1

    script_tags = soup.find_all("script")
    if any(script.get("type", "").lower() in {"application/json", "application/ld+json"} for script in script_tags):
        score += 1

    if any(_extract_json_from_script(script.get_text(" ", strip=True)) for script in script_tags):
        score += 1

    return score


def _score_static_indicators(soup: BeautifulSoup) -> int:
    text_blocks = [
        tag.get_text(" ", strip=True)
        for tag in soup.find_all(["article", "main", "section", "p", "li"])
    ]
    total_text_len = sum(len(block) for block in text_blocks)
    script_len = sum(len(tag.get_text(" ", strip=True)) for tag in soup.find_all("script"))

    score = 0
    if total_text_len > 1200:
        score += 2
    elif total_text_len > 500:
        score += 1

    if total_text_len > 0 and script_len / max(total_text_len, 1) < 1.5:
        score += 1

    return score


def _score_dynamic_indicators(soup: BeautifulSoup, html: str) -> int:
    score = 0
    compact = html[:150000]

    root = soup.find(id="root") or soup.find(id="app")
    if root and len(root.get_text(" ", strip=True)) < 80:
        score += 2

    if any(pattern.search(compact) for pattern in FRAMEWORK_HINT_PATTERNS):
        score += 1

    scripts = soup.find_all("script", src=True)
    if len(scripts) >= 8:
        score += 1

    body_text_len = len(soup.get_text(" ", strip=True))
    if len(scripts) >= 4 and body_text_len < 500:
        score += 1

    return score


def _extract_json_from_script(script_text: str) -> dict | None:
    script_text = script_text.strip()
    if not script_text:
        return None

    if script_text.startswith("{") and script_text.endswith("}"):
        try:
            data = json.loads(script_text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _collect_json_endpoints(data: dict, endpoints: set[str]) -> None:
    for value in data.values():
        if isinstance(value, dict):
            _collect_json_endpoints(value, endpoints)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _collect_json_endpoints(item, endpoints)
        elif isinstance(value, str):
            lower = value.lower()
            if any(token in lower for token in ["/api/", "graphql", ".json"]):
                endpoints.add(value)
