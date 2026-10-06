"""Offline-only BGE encoder. Missing optional dependencies never block routing."""

from __future__ import annotations

import threading
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / "research_state/intent-vector/bge-small-zh-v1.5"


class LocalIntentEncoder:
    model_id = "Xenova/bge-small-zh-v1.5:onnx-fp32-cls"

    def __init__(self, model_dir: Path = MODEL_DIR):
        self.model_dir = model_dir
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with self._lock:
            if self._session is None:
                tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
                tokenizer.enable_truncation(max_length=256)
                tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(
                    str(self.model_dir / "onnx/model.onnx"),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._tokenizer, self._session = tokenizer, session
            tokens = self._tokenizer.encode_batch(texts)
            tensors = {
                "input_ids": np.asarray([item.ids for item in tokens], dtype=np.int64),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in tokens], dtype=np.int64
                ),
                "token_type_ids": np.asarray([item.type_ids for item in tokens], dtype=np.int64),
            }
            inputs = {item.name: tensors[item.name] for item in self._session.get_inputs()}
            outputs = self._session.run(None, inputs)
            # BGE uses CLS pooling, NOT a mean over all token embeddings.
            embeddings = outputs[0][:, 0, :]
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            if not np.isfinite(embeddings).all() or (norms == 0).any():
                raise ValueError("Invalid semantic embeddings")
            return (embeddings / norms).tolist()


# Share the loaded model across independently constructed request engines.
LOCAL_INTENT_ENCODER = LocalIntentEncoder()
