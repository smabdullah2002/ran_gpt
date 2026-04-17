from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, HttpUrl

from config import get_settings
from services.ingestion.pipeline import IngestionConfig, IngestionPipeline

router = APIRouter(prefix="/api/v1", tags=["ingestion"])
logger = logging.getLogger(__name__)


class IngestRequest(BaseModel):
    root_url: HttpUrl = Field(..., description="Root URL to crawl and ingest")
    max_depth: int | None = Field(default=None, ge=0, le=5)
    max_pages: int | None = Field(default=None, ge=1, le=500)


class IngestTextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Raw text content to ingest")
    title: str = Field(default="Raw Text", min_length=1, max_length=256)
    source_id: str | None = Field(default=None, description="Optional source identifier for dedupe")


@router.post("/ingest")
async def ingest_site(payload: IngestRequest) -> dict:
    config = _build_ingestion_config(payload.max_depth, payload.max_pages)
    pipeline = IngestionPipeline(config)
    return await pipeline.ingest(str(payload.root_url))


@router.post("/ingest/text")
async def ingest_text(payload: IngestTextRequest) -> dict:
    config = _build_ingestion_config()
    pipeline = IngestionPipeline(config)
    source_id = payload.source_id or "raw://default"
    return await pipeline.ingest_raw_text(text=payload.text, title=payload.title, source_id=source_id)


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...), source_id: str | None = None) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    filename = file.filename or "uploaded_file"
    sections = _extract_sections_from_upload(filename=filename, content_type=file.content_type, raw=raw)
    if not sections:
        logger.warning("ingest_file_extract_failed filename=%s content_type=%s", filename, file.content_type)
        raise HTTPException(status_code=400, detail="Unable to extract readable text from uploaded file")

    logger.info(
        "ingest_file_extracted filename=%s section_count=%s has_page_metadata=%s",
        filename,
        len(sections),
        any("page_number" in section.get("metadata", {}) for section in sections),
    )

    config = _build_ingestion_config()
    pipeline = IngestionPipeline(config)
    return await pipeline.ingest_file_sections(filename=filename, sections=sections, source_id=source_id)


def _build_ingestion_config(max_depth: int | None = None, max_pages: int | None = None) -> IngestionConfig:
    settings = get_settings()
    return IngestionConfig(
        max_depth=max_depth if max_depth is not None else settings.INGEST_MAX_DEPTH,
        max_pages=max_pages if max_pages is not None else settings.INGEST_MAX_PAGES,
        rate_limit_seconds=settings.INGEST_RATE_LIMIT_SECONDS,
        request_timeout_seconds=settings.INGEST_REQUEST_TIMEOUT_SECONDS,
        retries=settings.INGEST_REQUEST_RETRIES,
        max_concurrency=settings.INGEST_MAX_CONCURRENCY,
        per_domain_concurrency=settings.INGEST_PER_DOMAIN_CONCURRENCY,
        processing_concurrency=settings.INGEST_PROCESSING_CONCURRENCY,
        min_words_after_cleaning=settings.INGEST_MIN_WORDS_AFTER_CLEANING,
        min_words_after_cleaning_fallback=settings.INGEST_MIN_WORDS_AFTER_CLEANING_FALLBACK,
        min_words_after_cleaning_browser=settings.INGEST_MIN_WORDS_AFTER_CLEANING_BROWSER,
        allow_tiny_fallback_content=settings.INGEST_ALLOW_TINY_FALLBACK_CONTENT,
        chunk_size_words=settings.INGEST_CHUNK_SIZE_WORDS,
        chunk_overlap_words=settings.INGEST_CHUNK_OVERLAP_WORDS,
        supabase_connection_string=settings.DIRECT_CONNECTION_KEY or "",
        supabase_table_name=settings.SUPABASE_TABLE_NAME,
        storage_cache_path=settings.INGEST_CACHE_PATH,
    )


def _extract_text_from_upload(filename: str, content_type: str | None, raw: bytes) -> str:
    sections = _extract_sections_from_upload(filename=filename, content_type=content_type, raw=raw)
    return "\n\n".join(section["text"] for section in sections if section.get("text"))


def _extract_sections_from_upload(filename: str, content_type: str | None, raw: bytes) -> list[dict[str, object]]:
    suffix = Path(filename).suffix.lower()
    is_pdf = suffix == ".pdf" or (content_type and "pdf" in content_type.lower())
    is_docx = suffix == ".docx" or (content_type and "wordprocessingml" in content_type.lower())

    if is_pdf:
        sections = _extract_pdf_sections(filename=filename, raw=raw)
        if sections:
            return sections

        # Fallback for environments where unstructured PDF dependencies are missing.
        pypdf_text = _sanitize_extracted_text(_extract_pdf_with_pypdf(raw))
        if _is_readable_text(pypdf_text):
            return [{"text": pypdf_text, "metadata": {"filename": filename, "section_type": "pdf_pypdf"}}]

        # Do not decode raw PDF bytes as text; that produces binary garbage.
        return []

    if is_docx:
        sections = _extract_docx_sections(filename=filename, raw=raw)
        if sections:
            return sections
        return []

    # Primary parser: unstructured handles many document formats.
    parsed_text = _sanitize_extracted_text(_extract_with_unstructured(filename=filename, raw=raw))
    if _is_readable_text(parsed_text):
        return [{"text": parsed_text, "metadata": {"filename": filename, "section_type": "auto"}}]

    # Fallback parser for text-like content and environments missing format extras.
    decoded = raw.decode("utf-8", errors="ignore")

    if suffix in {".txt", ".md", ".markdown", ".log", ".csv", ".xml", ".yml", ".yaml"}:
        text = _sanitize_extracted_text(decoded)
        return [{"text": text, "metadata": {"filename": filename, "section_type": "text"}}] if text else []

    if suffix in {".html", ".htm"} or (content_type and "html" in content_type.lower()):
        soup = BeautifulSoup(decoded, "html.parser")
        text = _sanitize_extracted_text(soup.get_text("\n", strip=True))
        return [{"text": text, "metadata": {"filename": filename, "section_type": "html"}}] if text else []

    if suffix == ".json" or (content_type and "json" in content_type.lower()):
        try:
            payload = json.loads(decoded)
            text = _sanitize_extracted_text(json.dumps(payload, indent=2, ensure_ascii=False))
            return [{"text": text, "metadata": {"filename": filename, "section_type": "json"}}] if text else []
        except json.JSONDecodeError:
            text = _sanitize_extracted_text(decoded)
            return [{"text": text, "metadata": {"filename": filename, "section_type": "json_text"}}] if text else []

    # Fallback for text-like unknown extensions.
    fallback = _sanitize_extracted_text(decoded)
    if _is_readable_text(fallback):
        return [{"text": fallback, "metadata": {"filename": filename, "section_type": "fallback_text"}}]
    return []


def _extract_with_unstructured(filename: str, raw: bytes) -> str:
    try:
        from unstructured.partition.auto import partition
    except Exception:
        return ""

    try:
        elements = partition(file=io.BytesIO(raw), file_filename=filename)
    except Exception:
        return ""

    parts: list[str] = []
    for element in elements:
        text = str(getattr(element, "text", "") or "").strip()
        if text:
            parts.append(text)

    return "\n".join(parts).strip()


def _extract_pdf_with_pypdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception:
        return ""

    parts: list[str] = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            parts.append(page_text)

    return "\n".join(parts).strip()


def _extract_pdf_sections(filename: str, raw: bytes) -> list[dict[str, object]]:
    try:
        from unstructured.partition.pdf import partition_pdf
    except Exception:
        return []

    try:
        elements = partition_pdf(file=io.BytesIO(raw))
    except Exception:
        return []

    return _elements_to_sections(elements=elements, filename=filename, default_section_type="pdf")


def _extract_docx_sections(filename: str, raw: bytes) -> list[dict[str, object]]:
    try:
        from unstructured.partition.docx import partition_docx
    except Exception:
        return []

    try:
        elements = partition_docx(file=io.BytesIO(raw))
    except Exception:
        return []

    return _elements_to_sections(elements=elements, filename=filename, default_section_type="docx")


def _elements_to_sections(elements: list[object], filename: str, default_section_type: str) -> list[dict[str, object]]:
    sections_by_key: dict[tuple[int | None, str], list[str]] = {}
    metadata_by_key: dict[tuple[int | None, str], dict[str, object]] = {}

    for element in elements:
        text = _sanitize_extracted_text(str(getattr(element, "text", "") or ""))
        # Keep short element fragments; readability is validated after section merge.
        if len(text) < 3:
            continue

        category = str(getattr(element, "category", "") or default_section_type)
        meta_obj = getattr(element, "metadata", None)
        page_number = getattr(meta_obj, "page_number", None) if meta_obj is not None else None
        page_number = int(page_number) if isinstance(page_number, int) else None
        key = (page_number, category)

        sections_by_key.setdefault(key, []).append(text)
        if key not in metadata_by_key:
            metadata_by_key[key] = {
                "filename": filename,
                "section_type": category.lower(),
            }
            if page_number is not None:
                metadata_by_key[key]["page_number"] = page_number

    output: list[dict[str, object]] = []
    for key, parts in sections_by_key.items():
        merged = _sanitize_extracted_text("\n".join(parts))
        if not _is_readable_text(merged):
            continue
        output.append({"text": merged, "metadata": metadata_by_key[key]})

    output.sort(key=lambda item: (item["metadata"].get("page_number", 0), str(item["metadata"].get("section_type", ""))))
    return output


def _sanitize_extracted_text(text: str) -> str:
    if not text:
        return ""

    # Remove NUL and most control bytes while preserving basic whitespace.
    text = text.replace("\x00", "")
    text = "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_readable_text(text: str) -> bool:
    if not text:
        return False

    sample = text[:5000]
    lowered = sample.lower()

    bad_signatures = [
        "pk!",
        "word/document.xml",
        "[content_types].xml",
        "obj\n",
        "endobj",
        "xref",
        "startxref",
    ]
    if any(sig in lowered for sig in bad_signatures):
        return False

    printable_count = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
    printable_ratio = printable_count / max(1, len(sample))
    alpha_count = sum(1 for ch in sample if ch.isalpha())
    token_count = len(sample.split())

    if printable_ratio < 0.80:
        return False

    # Accept shorter but still meaningful extracted text while rejecting binary-like noise.
    if alpha_count >= 12 or token_count >= 5:
        return True

    compact_len = len(sample.replace("\n", " ").strip())
    return compact_len >= 24 and alpha_count >= 8
