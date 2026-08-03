"""Dependency-free BM25 and dense hash retrieval fused with reciprocal rank fusion."""

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

from grounded_cv.models import EvidenceChunk

_TOKEN = re.compile(r"[a-z0-9]+")
_SYNONYMS = {
    "ai": ("agent", "retrieval", "grounded"),
    "api": ("backend", "rest", "service"),
    "based": ("location", "dublin"),
    "ci": ("continuous", "integration", "actions"),
    "database": ("sql", "sqlite", "data"),
    "errors": ("failures", "exceptions", "reliability"),
    "jobs": ("ats", "recruiting", "roles"),
    "rag": ("retrieval", "citations", "evidence"),
    "web": ("research", "robots", "sources"),
}


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN.findall(text.casefold())
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(_SYNONYMS.get(token, ()))
    return expanded


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk: EvidenceChunk
    score: float
    bm25_rank: int | None
    dense_rank: int | None


class HybridRetriever:
    """Atomic chunks indexed independently by BM25 and 384-D feature hashing."""

    def __init__(self, chunks: list[EvidenceChunk], *, dimensions: int = 384) -> None:
        self.chunks = chunks
        self.dimensions = dimensions
        self._tokens = [tokenize(_search_text(chunk)) for chunk in chunks]
        self._term_frequencies = [Counter(tokens) for tokens in self._tokens]
        self._document_frequency = Counter(
            token for tokens in self._tokens for token in set(tokens)
        )
        self._average_length = sum(map(len, self._tokens)) / max(len(chunks), 1)
        self._dense_vectors = [self._embed(_search_text(chunk)) for chunk in chunks]

    def search(self, query: str, *, limit: int = 5) -> list[RankedChunk]:
        if not query.strip():
            return []
        bm25_order = self._bm25_ranking(query)
        dense_order = self._dense_ranking(query)
        bm25_ranks = {index: rank for rank, index in enumerate(bm25_order, start=1)}
        dense_ranks = {index: rank for rank, index in enumerate(dense_order, start=1)}
        fused: list[tuple[int, float]] = []
        for index in range(len(self.chunks)):
            score = 1 / (60 + bm25_ranks[index]) + 1 / (60 + dense_ranks[index])
            fused.append((index, score))
        fused.sort(key=lambda item: (-item[1], self.chunks[item[0]].id))
        return [
            RankedChunk(
                chunk=self.chunks[index],
                score=score,
                bm25_rank=bm25_ranks[index],
                dense_rank=dense_ranks[index],
            )
            for index, score in fused[:limit]
        ]

    def _bm25_ranking(self, query: str) -> list[int]:
        query_tokens = tokenize(query)
        count = len(self.chunks)
        scores: list[tuple[int, float]] = []
        for index, frequencies in enumerate(self._term_frequencies):
            length = len(self._tokens[index])
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                document_frequency = self._document_frequency[token]
                inverse_frequency = math.log(
                    1 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * length / max(self._average_length, 1)
                )
                score += inverse_frequency * frequency * 2.5 / denominator
            scores.append((index, score))
        scores.sort(key=lambda item: (-item[1], self.chunks[item[0]].id))
        return [index for index, _ in scores]

    def _dense_ranking(self, query: str) -> list[int]:
        query_vector = self._embed(query)
        scores = [
            (
                index,
                sum(
                    left * right for left, right in zip(query_vector, vector, strict=True)
                ),
            )
            for index, vector in enumerate(self._dense_vectors)
        ]
        scores.sort(key=lambda item: (-item[1], self.chunks[item[0]].id))
        return [index for index, _ in scores]

    def _embed(self, text: str) -> list[float]:
        """Create a deterministic dense vector from word and character-ngram features."""

        vector = [0.0] * self.dimensions
        tokens = tokenize(text)
        features = tokens + [
            f"#{token[index : index + 3]}"
            for token in tokens
            if len(token) >= 3
            for index in range(len(token) - 2)
        ]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def _search_text(chunk: EvidenceChunk) -> str:
    return f"{chunk.section} {chunk.title} {chunk.text}"
