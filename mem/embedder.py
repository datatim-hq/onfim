"""Local embeddings (P9). fastembed/ONNX without torch; 'fake' is deterministic, for tests."""
from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np

from mem.errors import EmbedderUnavailable

log = logging.getLogger("mem.embedder")

# Candidate models (01): how to load one into fastembed if it is not in the built-in list.
CUSTOM_MODELS = {
    "intfloat/multilingual-e5-small": {"dim": 384, "hf": "intfloat/multilingual-e5-small", "file": "onnx/model.onnx"},
    "intfloat/multilingual-e5-base": {"dim": 768, "hf": "intfloat/multilingual-e5-base", "file": "onnx/model.onnx"},
}


class Embedder:
    model_id: str
    dim: int

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError


class FakeEmbedder(Embedder):
    """Hashes word stems (first 5 letters) and character trigrams. Similar texts end up close."""

    def __init__(self, dim: int):
        self.model_id = "fake"
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        words = re.findall(r"\w+", text.lower().replace("ё", "е"))
        feats = [w[:5] for w in words]
        for w in words:
            feats += [w[i:i + 3] for i in range(max(1, len(w) - 2))]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:4], "little")
            v[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n > 0 else v + (1.0 / np.sqrt(self.dim))

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FastEmbedder(Embedder):
    def __init__(self, model_id: str, dim: int):
        from fastembed import TextEmbedding

        self.model_id = model_id
        self.dim = dim
        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model_id not in supported and model_id in CUSTOM_MODELS:
            from fastembed.common.model_description import ModelSource, PoolingType

            spec = CUSTOM_MODELS[model_id]
            TextEmbedding.add_custom_model(
                model=model_id, pooling=PoolingType.MEAN, normalization=True,
                sources=ModelSource(hf=spec["hf"]), dim=spec["dim"], model_file=spec["file"],
            )
        self._model = TextEmbedding(model_name=model_id)
        self._e5 = "e5" in model_id.lower()
        self._lock = threading.Lock()
        probe = self.embed_query("проверка")
        if probe.shape[0] != dim:
            raise RuntimeError(f"model {model_id} gives dimension {probe.shape[0]}, but the configuration says {dim}")

    def _run(self, texts: list[str]) -> list[np.ndarray]:
        with self._lock:
            out = [np.asarray(v, dtype=np.float32) for v in self._model.embed(texts)]
        return [v / (np.linalg.norm(v) or 1.0) for v in out]

    def embed_passages(self, texts):
        return self._run([f"passage: {t}" if self._e5 else t for t in texts])

    def embed_query(self, text):
        return self._run([f"query: {text}" if self._e5 else text])[0]


def build(model_id: str, dim: int) -> Embedder:
    if model_id == "fake":
        return FakeEmbedder(dim)
    return FastEmbedder(model_id, dim)


class SafeEmbedder:
    """Wrapper: any model error -> 503 for the client, the record is not created (F-11)."""

    def __init__(self, inner: Embedder):
        self.inner = inner
        self.model_id = inner.model_id
        self.dim = inner.dim

    def passage(self, text: str) -> np.ndarray:
        try:
            return self.inner.embed_passages([text])[0]
        except Exception as e:  # noqa: BLE001
            log.exception("embedder failed")
            raise EmbedderUnavailable("embedding model unavailable, the record was not created") from e

    def query(self, text: str) -> np.ndarray:
        try:
            return self.inner.embed_query(text)
        except Exception as e:  # noqa: BLE001
            log.exception("embedder failed")
            raise EmbedderUnavailable("embedding model unavailable") from e
