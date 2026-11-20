"""Local, optional multilingual E5 inference. App startup never downloads any files."""

from functools import lru_cache
from pathlib import Path


class LocalEmbeddings:
    def __init__(self, path: Path) -> None:
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer

        self._np = numpy
        self._tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=256)
        self._tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            str(path / "model_quantized.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {item.name for item in self._session.get_inputs()}

    @lru_cache(maxsize=32)
    def encode(self, texts: tuple[str, ...]):
        tokens = self._tokenizer.encode_batch(list(texts))
        np = self._np
        mask = np.asarray([row.attention_mask for row in tokens], dtype=np.int64)
        candidates = {
            "input_ids": np.asarray([row.ids for row in tokens], dtype=np.int64),
            "attention_mask": mask,
            "token_type_ids": np.asarray([row.type_ids for row in tokens], dtype=np.int64),
        }
        hidden = self._session.run(None, {key: candidates[key] for key in self._inputs})[0]
        weighted = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return weighted / np.maximum(np.linalg.norm(weighted, axis=1, keepdims=True), 1e-12)

    def similarity(self, query: str, passages: tuple[str, ...]) -> list[float]:
        query_vector = self.encode((f"query: {query}",))[0]
        index = self.encode(passages)
        scores = index @ query_vector
        return scores.reshape(-1, 2).max(axis=1).tolist()


@lru_cache(maxsize=2)
def load_embeddings(path: Path) -> LocalEmbeddings | None:
    if not all((path / filename).is_file() for filename in ("tokenizer.json", "model_quantized.onnx")):
        return None
    return LocalEmbeddings(path)
