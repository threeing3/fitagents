import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar


T = TypeVar("T")


_ASCII_WORD_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_DOMAIN_SYNONYMS = {
    "睡不好": ["睡眠", "睡眠差", "睡眠不足", "recovery", "sleep"],
    "睡多久": ["睡眠", "小时", "7", "9", "sleep"],
    "外卖": ["外食", "takeout", "nutrition"],
    "膝盖痛": ["膝痛", "膝盖疼", "knee", "pain"],
    "肩伤": ["肩痛", "肩膀", "shoulder", "injury"],
    "甲亢": ["甲状腺", "hyperthyroid", "thyroid"],
    "蛋白粉": ["蛋白质", "protein", "supplement"],
}


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize mixed Chinese/English fitness text for small-corpus BM25."""
    lowered = text.lower()
    tokens: list[str] = []
    for phrase, synonyms in _DOMAIN_SYNONYMS.items():
        if phrase in lowered:
            tokens.extend(synonyms)
    tokens.extend(match.group(0) for match in _ASCII_WORD_RE.finditer(lowered))
    for match in _CJK_RE.finditer(lowered):
        segment = match.group(0)
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) >= 3:
            tokens.extend(segment[index : index + 3] for index in range(len(segment) - 2))
    return tokens


@dataclass(frozen=True)
class BM25Match:
    item: T
    score: float
    normalized_score: float


class BM25Ranker:
    def __init__(self, documents: Sequence[str], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.document_tokens = [tokenize_for_bm25(document) for document in documents]
        self.document_lengths = [len(tokens) for tokens in self.document_tokens]
        self.average_length = (
            sum(self.document_lengths) / len(self.document_lengths)
            if self.document_lengths
            else 0.0
        )
        document_frequency: Counter[str] = Counter()
        for tokens in self.document_tokens:
            document_frequency.update(set(tokens))
        total_documents = len(self.document_tokens)
        self.idf = {
            token: math.log(1 + (total_documents - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }

    def score(self, query: str, document_index: int) -> float:
        if not self.document_tokens or self.average_length <= 0:
            return 0.0
        query_tokens = tokenize_for_bm25(query)
        if not query_tokens:
            return 0.0
        frequencies = Counter(self.document_tokens[document_index])
        document_length = self.document_lengths[document_index] or 1
        score = 0.0
        for token in query_tokens:
            term_frequency = frequencies.get(token, 0)
            if term_frequency <= 0:
                continue
            denominator = term_frequency + self.k1 * (
                1 - self.b + self.b * document_length / self.average_length
            )
            score += self.idf.get(token, 0.0) * (
                term_frequency * (self.k1 + 1) / denominator
            )
        return score


def rank_by_bm25(
    items: Sequence[T],
    query: str,
    document_builder: Callable[[T], str],
) -> list[BM25Match]:
    documents = [document_builder(item) for item in items]
    ranker = BM25Ranker(documents)
    raw_matches = [
        BM25Match(item=item, score=ranker.score(query, index), normalized_score=0.0)
        for index, item in enumerate(items)
    ]
    max_score = max((match.score for match in raw_matches), default=0.0)
    if max_score <= 0:
        return raw_matches
    return [
        BM25Match(
            item=match.item,
            score=match.score,
            normalized_score=match.score / max_score,
        )
        for match in raw_matches
    ]


def build_weighted_document(fields: Iterable[tuple[str | None, int]]) -> str:
    parts: list[str] = []
    for text, weight in fields:
        if not text:
            continue
        parts.extend([text] * max(weight, 1))
    return "\n".join(parts)
