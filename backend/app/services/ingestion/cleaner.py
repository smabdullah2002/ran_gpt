from __future__ import annotations

import re


class ContentCleaner:
    def __init__(self, min_line_words: int = 2) -> None:
        self.min_line_words = min_line_words

    def clean(self, text: str) -> str:
        normalized = self._normalize_whitespace(text)
        lines = [line.strip() for line in normalized.split("\n") if line.strip()]

        seen: set[str] = set()
        filtered: list[str] = []

        for line in lines:
            compact = re.sub(r"\s+", " ", line).strip().lower()
            if compact in seen:
                continue
            if len(compact.split()) < self.min_line_words:
                continue
            if compact in {
                "home",
                "menu",
                "search",
                "contact us",
                "privacy policy",
                "read more",
                "share",
                "next",
                "previous",
            }:
                continue
            seen.add(compact)
            filtered.append(line)

        return "\n".join(filtered)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
