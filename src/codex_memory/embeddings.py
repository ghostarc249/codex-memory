from __future__ import annotations

import hashlib
import math
import re


DEFAULT_EMBEDDING_PROVIDER = "local-hash-v1"
DEFAULT_EMBEDDING_DIMENSIONS = 256
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")


def embed_text(text: str, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS) -> list[float]:
    """Create a deterministic local embedding without external dependencies.

    This is intentionally simple: it gives semantic-ish lexical recall and a
    stable storage format while keeping the default install local and offline.
    A future provider can write vectors into the same table.
    """
    vector = [0.0] * dimensions
    tokens = TOKEN_PATTERN.findall(text.lower())
    for token in tokens:
        add_token(vector, token, weight=1.0)
        for part in re.split(r"[./_-]+", token):
            if part and part != token:
                add_token(vector, part, weight=0.35)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def add_token(vector: list[float], token: str, weight: float) -> None:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    bucket = int.from_bytes(digest[:4], "big") % len(vector)
    sign = 1.0 if digest[4] & 1 else -1.0
    vector[bucket] += sign * weight
