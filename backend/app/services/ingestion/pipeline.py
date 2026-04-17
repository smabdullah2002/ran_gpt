from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .chunker import TextChunker
from .cleaner import ContentCleaner
from .crawler import CrawlConfig, URLCrawler
from .detector import detect_content_type
from .embedder import Embedder
from .extractor import ContentExtractor
from .storage import SupabaseIngestionStorage


@dataclass
class IngestionConfig:
    max_depth: int = 2
    max_pages: int = 50
    rate_limit_seconds: float = 0.2
    request_timeout_seconds: int = 15
    retries: int = 2
    max_concurrency: int = 8
    per_domain_concurrency: int = 3
    processing_concurrency: int = 4
    min_words_after_cleaning: int = 40
    min_words_after_cleaning_fallback: int = 8
    min_words_after_cleaning_browser: int = 10
    allow_tiny_fallback_content: bool = True
    chunk_size_words: int = 500
    chunk_overlap_words: int = 50
    supabase_connection_string: str = ""
    supabase_table_name: str = "document_chunks"
    storage_cache_path: str = "backend/data/ingestion_cache.json"


class IngestionPipeline:
    def __init__(self, config: IngestionConfig) -> None:
        self.config = config
        self.crawler = URLCrawler(
            CrawlConfig(
                max_depth=config.max_depth,
                max_pages=config.max_pages,
                rate_limit_seconds=config.rate_limit_seconds,
                request_timeout_seconds=config.request_timeout_seconds,
                max_concurrency=config.max_concurrency,
                per_domain_concurrency=config.per_domain_concurrency,
                retries=config.retries,
            )
        )
        self.extractor = ContentExtractor(
            request_timeout_seconds=config.request_timeout_seconds,
            retries=config.retries,
        )
        self.cleaner = ContentCleaner()
        self.chunker = TextChunker(
            chunk_size_words=config.chunk_size_words,
            overlap_words=config.chunk_overlap_words,
        )
        self.embedder = Embedder()
        self.storage = SupabaseIngestionStorage(
            connection_string=config.supabase_connection_string,
            cache_path=config.storage_cache_path,
            table_name=config.supabase_table_name,
        )

    async def ingest(self, root_url: str) -> dict[str, Any]:
        crawl_result = await self.crawler.crawl(root_url)
        urls = crawl_result.urls

        processed_pages = 0
        total_chunks = 0
        method_counter: Counter[str] = Counter()
        failed_urls: list[dict[str, str]] = []
        skipped_urls: list[dict[str, str]] = []
        lock = asyncio.Lock()
        processing_semaphore = asyncio.Semaphore(self.config.processing_concurrency)

        async def process_url(url: str) -> None:
            nonlocal processed_pages, total_chunks
            try:
                async with processing_semaphore:
                    try:
                        raw_html = await self.extractor.fetch_raw_html(url)
                        content_type = detect_content_type(url, raw_html)
                        extraction = await self.extractor.extract(url, raw_html, content_type)
                    except Exception:
                        extraction = await self.extractor.extract_with_browser(url)

                    unchanged = await asyncio.to_thread(
                        self.storage.is_unchanged,
                        url,
                        extraction.content_hash,
                    )
                    if unchanged:
                        async with lock:
                            skipped_urls.append({"url": url, "reason": "unchanged_content"})
                        return

                    clean_text = self.cleaner.clean(extraction.text)
                    raw_text = " ".join(extraction.text.split())
                    raw_text_length = len(raw_text)
                    cleaned_text_length = len(clean_text.strip())

                    min_required_length = 50
                    if extraction.method == "html_fallback" and self.config.allow_tiny_fallback_content:
                        # Fallback content can be very short on protected/challenge pages; ingest if non-empty.
                        clean_text = raw_text
                        cleaned_text_length = len(clean_text)
                        min_required_length = 1

                    # If strict cleaning is too aggressive, recover with normalized raw text.
                    if min_required_length > 1 and cleaned_text_length < 100 and raw_text_length >= 50:
                        clean_text = raw_text
                        cleaned_text_length = len(clean_text)

                    if cleaned_text_length < min_required_length:
                        async with lock:
                            skipped_urls.append(
                                {
                                    "url": url,
                                    "reason": "too_short_after_cleaning",
                                    "method": extraction.method,
                                    "cleaned_text_length": cleaned_text_length,
                                    "raw_text_length": raw_text_length,
                                    "min_required_length": min_required_length,
                                }
                            )
                        return

                    chunks = self.chunker.chunk(clean_text, source_url=url)
                    if not chunks:
                        async with lock:
                            skipped_urls.append({"url": url, "reason": "no_chunks_generated"})
                        return

                    embeddings = await asyncio.to_thread(
                        self.embedder.embed_texts,
                        (chunk["content"] for chunk in chunks),
                    )
                    stored_count = await asyncio.to_thread(
                        self.storage.store_chunks,
                        chunks,
                        embeddings,
                        url,
                        extraction.title,
                        extraction.content_hash,
                        extraction.method,
                    )

                    await asyncio.to_thread(self.storage.update_cache, url, extraction.content_hash)

                    async with lock:
                        processed_pages += 1
                        total_chunks += stored_count
                        method_counter[extraction.method] += 1
            except Exception as exc:
                async with lock:
                    failed_urls.append({"url": url, "error": str(exc)})

        await asyncio.gather(*(process_url(url) for url in urls))

        return {
            "total_urls_crawled": len(urls),
            "total_pages_processed": processed_pages,
            "total_chunks_created": total_chunks,
            "extraction_method_distribution": dict(method_counter),
            "crawler_diagnostics": crawl_result.diagnostics,
            "skipped_urls": skipped_urls,
            "failed_urls": failed_urls,
        }

    async def ingest_raw_text(
        self,
        text: str,
        title: str = "Raw Text",
        source_id: str = "raw://default",
    ) -> dict[str, Any]:
        return await self._ingest_document(
            source_id=source_id,
            title=title,
            text=text,
            extraction_method="raw_text",
        )

    async def ingest_file_text(
        self,
        text: str,
        filename: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        effective_source_id = source_id or f"file://{filename}"
        return await self.ingest_file_sections(
            filename=filename,
            sections=[{"text": text, "metadata": {"section_type": "plain_text"}}],
            source_id=effective_source_id,
        )

    async def ingest_file_sections(
        self,
        filename: str,
        sections: list[dict[str, Any]],
        source_id: str | None = None,
    ) -> dict[str, Any]:
        effective_source_id = source_id or f"file://{filename}"
        return await self._ingest_file_sections_document(
            source_id=effective_source_id,
            title=filename,
            sections=sections,
            extraction_method="file_text",
        )

    async def _ingest_document(
        self,
        source_id: str,
        title: str,
        text: str,
        extraction_method: str,
    ) -> dict[str, Any]:
        normalized_text = " ".join(text.split())
        if not normalized_text:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
                "skipped_urls": [{"url": source_id, "reason": "empty_input"}],
                "failed_urls": [],
            }

        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        unchanged = await asyncio.to_thread(self.storage.is_unchanged, source_id, content_hash)
        if unchanged:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
                "skipped_urls": [{"url": source_id, "reason": "unchanged_content"}],
                "failed_urls": [],
            }

        clean_text = self.cleaner.clean(normalized_text)
        cleaned_text_length = len(clean_text.strip())
        raw_text_length = len(normalized_text)

        # For direct user-provided text/file content, keep a permissive floor to avoid over-skipping.
        min_required_length = 1
        if cleaned_text_length < 100 and raw_text_length >= 50:
            clean_text = normalized_text
            cleaned_text_length = len(clean_text)

        if cleaned_text_length < min_required_length:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
                "skipped_urls": [
                    {
                        "url": source_id,
                        "reason": "too_short_after_cleaning",
                        "method": extraction_method,
                        "cleaned_text_length": cleaned_text_length,
                        "raw_text_length": raw_text_length,
                        "min_required_length": min_required_length,
                    }
                ],
                "failed_urls": [],
            }

        chunks = self.chunker.chunk(clean_text, source_url=source_id)
        if not chunks:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
                "skipped_urls": [{"url": source_id, "reason": "no_chunks_generated"}],
                "failed_urls": [],
            }

        embeddings = await asyncio.to_thread(self.embedder.embed_texts, (chunk["content"] for chunk in chunks))
        stored_count = await asyncio.to_thread(
            self.storage.store_chunks,
            chunks,
            embeddings,
            source_id,
            title,
            content_hash,
            extraction_method,
        )
        await asyncio.to_thread(self.storage.update_cache, source_id, content_hash)

        return {
            "total_urls_crawled": 1,
            "total_pages_processed": 1,
            "total_chunks_created": stored_count,
            "extraction_method_distribution": {extraction_method: 1},
            "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
            "skipped_urls": [],
            "failed_urls": [],
        }

    async def _ingest_file_sections_document(
        self,
        source_id: str,
        title: str,
        sections: list[dict[str, Any]],
        extraction_method: str,
    ) -> dict[str, Any]:
        normalized_sections: list[dict[str, Any]] = []
        hash_parts: list[str] = []

        for section in sections:
            section_text = " ".join(str(section.get("text", "")).split())
            if not section_text:
                continue
            section_meta = section.get("metadata") if isinstance(section.get("metadata"), dict) else {}
            normalized_sections.append({"text": section_text, "metadata": section_meta})
            hash_parts.append(section_text)

        if not normalized_sections:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {"source_type": extraction_method, "source_id": source_id},
                "skipped_urls": [{"url": source_id, "reason": "empty_input"}],
                "failed_urls": [],
            }

        content_hash = hashlib.sha256("\n".join(hash_parts).encode("utf-8")).hexdigest()
        unchanged = await asyncio.to_thread(self.storage.is_unchanged, source_id, content_hash)
        if unchanged:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {
                    "source_type": extraction_method,
                    "source_id": source_id,
                    "section_count": len(normalized_sections),
                },
                "skipped_urls": [{"url": source_id, "reason": "unchanged_content"}],
                "failed_urls": [],
            }

        all_chunks: list[dict[str, Any]] = []
        global_chunk_index = 0

        for section_index, section in enumerate(normalized_sections):
            raw_text = section["text"]
            clean_text = self.cleaner.clean(raw_text)
            if len(clean_text.strip()) < 100 and len(raw_text) >= 50:
                clean_text = raw_text

            if not clean_text.strip():
                continue

            section_chunks = self.chunker.chunk(clean_text, source_url=source_id)
            for chunk in section_chunks:
                chunk_meta = dict(chunk.get("metadata", {}))
                chunk_meta.update(
                    {
                        "chunk_index": global_chunk_index,
                        "section_index": section_index,
                        **section["metadata"],
                    }
                )
                chunk["metadata"] = chunk_meta
                all_chunks.append(chunk)
                global_chunk_index += 1

        if not all_chunks:
            return {
                "total_urls_crawled": 1,
                "total_pages_processed": 0,
                "total_chunks_created": 0,
                "extraction_method_distribution": {},
                "crawler_diagnostics": {
                    "source_type": extraction_method,
                    "source_id": source_id,
                    "section_count": len(normalized_sections),
                },
                "skipped_urls": [{"url": source_id, "reason": "no_chunks_generated"}],
                "failed_urls": [],
            }

        embeddings = await asyncio.to_thread(self.embedder.embed_texts, (chunk["content"] for chunk in all_chunks))
        stored_count = await asyncio.to_thread(
            self.storage.store_chunks,
            all_chunks,
            embeddings,
            source_id,
            title,
            content_hash,
            extraction_method,
        )
        await asyncio.to_thread(self.storage.update_cache, source_id, content_hash)

        return {
            "total_urls_crawled": 1,
            "total_pages_processed": 1,
            "total_chunks_created": stored_count,
            "extraction_method_distribution": {extraction_method: 1},
            "crawler_diagnostics": {
                "source_type": extraction_method,
                "source_id": source_id,
                "section_count": len(normalized_sections),
                "chunk_count": len(all_chunks),
            },
            "skipped_urls": [],
            "failed_urls": [],
        }
