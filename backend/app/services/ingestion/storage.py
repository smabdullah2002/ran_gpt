from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from pgvector.psycopg import register_vector


class SupabaseIngestionStorage:
    def __init__(self, connection_string: str, cache_path: str, table_name: str = "document_chunks") -> None:
        if not connection_string:
            raise ValueError("A Supabase direct connection string is required for ingestion storage")

        self.connection_string = _ensure_sslmode_require(connection_string)
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self._initialize_schema()

    def _get_connection(self, register_vectors: bool = True):
        connection = psycopg.connect(self.connection_string)
        connection.autocommit = True
        if register_vectors:
            register_vector(connection)
        return connection

    def _initialize_schema(self) -> None:
        create_extension_sql = "CREATE EXTENSION IF NOT EXISTS vector;"
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS public.{self.table_name} (
            id uuid PRIMARY KEY,
            source_url text NOT NULL,
            title text,
            content text NOT NULL,
            content_hash text NOT NULL,
            extraction_method text NOT NULL,
            chunk_index integer NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector(384) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """
        create_index_sql = f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {self.table_name}_source_hash_chunk_idx
        ON public.{self.table_name} (source_url, content_hash, chunk_index);
        """

        with self._get_connection(register_vectors=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(create_extension_sql)
                cursor.execute(create_table_sql)
                cursor.execute(create_index_sql)

    def is_unchanged(self, source_url: str, content_hash: str) -> bool:
        cache = self._read_cache()
        if cache.get(source_url) != content_hash:
            return False

        return self._hash_exists_in_supabase(source_url=source_url, content_hash=content_hash)

    def update_cache(self, source_url: str, content_hash: str) -> None:
        cache = self._read_cache()
        cache[source_url] = content_hash
        self.cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    def store_chunks(
        self,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        source_url: str,
        title: str,
        content_hash: str,
        extraction_method: str,
    ) -> int:
        count = 0
        timestamp = datetime.now(timezone.utc).isoformat()
        safe_source_url = _strip_nul_bytes(source_url)
        safe_title = _strip_nul_bytes(title)
        safe_content_hash = _strip_nul_bytes(content_hash)
        safe_extraction_method = _strip_nul_bytes(extraction_method)

        insert_sql = f"""
        INSERT INTO public.{self.table_name} (
            id,
            source_url,
            title,
            content,
            content_hash,
            extraction_method,
            chunk_index,
            metadata,
            embedding
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (source_url, content_hash, chunk_index) DO NOTHING;
        """

        with self._get_connection() as connection:
            with connection.cursor() as cursor:
                for chunk, embedding in zip(chunks, embeddings):
                    safe_content = _strip_nul_bytes(chunk["content"])
                    chunk_metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
                    metadata = {
                        "source_url": safe_source_url,
                        "title": safe_title,
                        "content_hash": safe_content_hash,
                        "extraction_method": safe_extraction_method,
                        "timestamp": timestamp,
                        "chunk_index": chunk["metadata"]["chunk_index"],
                        **chunk_metadata,
                    }
                    safe_metadata = _sanitize_metadata(metadata)
                    cursor.execute(
                        insert_sql,
                        (
                            _stable_uuid(safe_source_url, safe_content_hash, chunk["metadata"]["chunk_index"]),
                            safe_source_url,
                            safe_title,
                            safe_content,
                            safe_content_hash,
                            safe_extraction_method,
                            chunk["metadata"]["chunk_index"],
                            json.dumps(safe_metadata),
                            embedding,
                        ),
                    )
                    if cursor.rowcount and cursor.rowcount > 0:
                        count += 1

        return count

    def _read_cache(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}

        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _hash_exists_in_supabase(self, source_url: str, content_hash: str) -> bool:
        query_sql = f"""
        SELECT 1
        FROM public.{self.table_name}
        WHERE source_url = %s AND content_hash = %s
        LIMIT 1;
        """

        with self._get_connection(register_vectors=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query_sql, (source_url, content_hash))
                return cursor.fetchone() is not None


def _stable_uuid(source_url: str, content_hash: str, chunk_index: int) -> str:
    import uuid

    key = f"{source_url}|{content_hash}|{chunk_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _ensure_sslmode_require(connection_string: str) -> str:
    if "sslmode=" in connection_string:
        return connection_string

    separator = "&" if "?" in connection_string else "?"
    return f"{connection_string}{separator}sslmode=require"


def _strip_nul_bytes(value: str) -> str:
    return value.replace("\x00", "") if value else value


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_nul_bytes(value)
    if isinstance(value, dict):
        return {k: _sanitize_metadata(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    return value
