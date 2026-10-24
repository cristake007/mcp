"""Optional CPU embeddings; no LLM server, API key, or permanent model process."""
import math
from functools import lru_cache
import os

MODEL_ID = "intfloat/multilingual-e5-small"
DIMS = 384


def enabled() -> bool:
    return os.getenv("RAG_EMBEDDINGS_ENABLED", "1").lower() not in {"0", "false", "no"}


class Embedder:
    def __init__(self):
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        if MODEL_ID not in {entry["model"] for entry in TextEmbedding.list_supported_models()}:
            TextEmbedding.add_custom_model(
                model=MODEL_ID,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=MODEL_ID),
                dim=DIMS,
                model_file="onnx/model_O4.onnx",
            )
        self.model = TextEmbedding(
            model_name=MODEL_ID,
            cache_dir=os.getenv("FASTEMBED_CACHE_PATH", "/model-cache"),
            threads=2,
        )

    def embed(self, values: list[str], *, is_query: bool = False) -> list[str]:
        if not values:
            return []
        prefix = "query: " if is_query else "passage: "
        vectors = []
        for vector in self.model.embed([prefix + value for value in values], batch_size=8):
            floats = [float(item) for item in vector]
            if len(floats) != DIMS or any(not math.isfinite(x) for x in floats):
                raise ValueError("Embedding model returned invalid vector")
            vectors.append("[" + ",".join(str(x) for x in floats) + "]")
        if len(vectors) != len(values):
            raise RuntimeError("Embedding count mismatch")
        return vectors


@lru_cache(maxsize=1)
def cached_embedder() -> Embedder:
    """Avoid reloading the ONNX model on every HTTP search request."""
    return Embedder()
