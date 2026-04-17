from __future__ import annotations

import hashlib
import threading
from typing import Iterable


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None
        self._model_load_error: str | None = None
        self._model_lock = threading.Lock()

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        text_list = list(texts)
        if not text_list:
            return []

        model = self._get_model()
        if model is None:
            return [self._fallback_embedding(text) for text in text_list]

        vectors = model.encode(text_list, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def _get_model(self):
        if self._model is not None:
            return self._model

        if self._model_load_error is not None:
            return None

        with self._model_lock:
            if self._model is not None:
                return self._model

            if self._model_load_error is not None:
                return None

            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
                return self._model
            except Exception as exc:
                self._model_load_error = str(exc)
                return None

    @staticmethod
    def _fallback_embedding(text: str, dimensions: int = 384) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < dimensions:
            for byte in digest:
                if len(values) >= dimensions:
                    break
                values.append((byte / 255.0) * 2 - 1)
            digest = hashlib.sha256(digest).digest()
        return values
