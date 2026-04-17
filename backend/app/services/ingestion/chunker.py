from __future__ import annotations

import re
from typing import Any


class TextChunker:
    def __init__(self, chunk_size_words: int = 500, overlap_words: int = 50) -> None:
        if chunk_size_words < 100:
            raise ValueError("chunk_size_words must be >= 100")
        if overlap_words < 0:
            raise ValueError("overlap_words must be >= 0")
        self.chunk_size_words = chunk_size_words
        self.overlap_words = overlap_words

    def chunk(self, text: str, source_url: str) -> list[dict[str, Any]]:
        sentences = self._split_sentences(text)
        chunks: list[dict[str, Any]] = []

        current_words: list[str] = []
        chunk_index = 0

        for sentence in sentences:
            sentence_words = sentence.split()
            if not sentence_words:
                continue

            if len(current_words) + len(sentence_words) > self.chunk_size_words and current_words:
                chunk_text = " ".join(current_words).strip()
                chunks.append(
                    {
                        "content": chunk_text,
                        "metadata": {
                            "source_url": source_url,
                            "chunk_index": chunk_index,
                        },
                    }
                )
                chunk_index += 1

                if self.overlap_words > 0:
                    current_words = current_words[-self.overlap_words :]
                else:
                    current_words = []

            current_words.extend(sentence_words)

        if current_words:
            chunks.append(
                {
                    "content": " ".join(current_words).strip(),
                    "metadata": {
                        "source_url": source_url,
                        "chunk_index": chunk_index,
                    },
                }
            )

        return chunks

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []

        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalized)
        return [s.strip() for s in sentences if s.strip()]
