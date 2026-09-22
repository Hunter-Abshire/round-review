"""Ranking reference passages for a question, in pure Python.

BM25 over a few hundred short passages needs no embedding model, no vector store and no
network, which keeps the whole tool offline and keeps a question's cost to the model call it
was always going to make. The corpus is small enough that scoring it exhaustively is faster
than any index would be.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from round_review.reference.corpus import Passage

WORD_RE = re.compile(r"[a-z0-9]+")
# Common enough to carry no signal in a question about a game.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "without",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "she",
        "it",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "should",
        "would",
        "could",
        "can",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "not",
        "no",
        "nor",
        "so",
        "too",
        "very",
        "just",
        "about",
        "here",
        "there",
        "any",
        "some",
        "all",
        "more",
        "most",
        "other",
        "same",
        "own",
        "only",
        "s",
        "t",
        "don",
        "now",
    }
)
# BM25's usual defaults; the corpus is short passages, so they need no tuning.
K1 = 1.5
B = 0.75
# How much a passage about the agent or map in play is favoured. Multiplicative, so it
# reorders among relevant passages but cannot promote an irrelevant one.
TAG_BOOST = 0.6
DEFAULT_MAX_CHARS = 4000


@dataclass(frozen=True, slots=True)
class Hit:
    passage: Passage
    score: float


@dataclass(frozen=True, slots=True)
class Index:
    passages: tuple[Passage, ...]
    term_frequencies: tuple[dict[str, int], ...]
    lengths: tuple[int, ...]
    document_frequencies: dict[str, int]
    average_length: float = field(default=0.0)


def tokenize(text: str) -> list[str]:
    return [
        word for word in WORD_RE.findall(text.lower()) if len(word) > 1 and word not in STOPWORDS
    ]


def build_index(passages: Sequence[Passage]) -> Index:
    frequencies: list[dict[str, int]] = []
    lengths: list[int] = []
    document_frequencies: dict[str, int] = {}
    for passage in passages:
        counts: dict[str, int] = {}
        for term in tokenize(f"{passage.title} {passage.text}"):
            counts[term] = counts.get(term, 0) + 1
        frequencies.append(counts)
        lengths.append(sum(counts.values()))
        for term in counts:
            document_frequencies[term] = document_frequencies.get(term, 0) + 1
    average = sum(lengths) / len(lengths) if lengths else 0.0
    return Index(tuple(passages), tuple(frequencies), tuple(lengths), document_frequencies, average)


def _idf(index: Index, term: str) -> float:
    total = len(index.passages)
    seen = index.document_frequencies.get(term, 0)
    if seen == 0:
        return 0.0
    return math.log(1 + (total - seen + 0.5) / (seen + 0.5))


def search(
    index: Index,
    question: str,
    limit: int,
    tags: Sequence[str] = (),
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Hit]:
    """The best `limit` passages for this question, within a character budget.

    `tags` are the agent and map in play. They favour passages about the right agent and map
    without being able to surface a passage the question does not match at all.
    """
    terms = tokenize(question)
    if limit <= 0 or not terms or not index.passages:
        return []
    wanted = {tag.lower() for tag in tags}

    scored: list[Hit] = []
    for i, passage in enumerate(index.passages):
        counts = index.term_frequencies[i]
        length = index.lengths[i]
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            denominator = frequency + K1 * (
                1 - B + B * (length / index.average_length if index.average_length else 1)
            )
            score += _idf(index, term) * (frequency * (K1 + 1)) / denominator
        if score <= 0:
            continue
        if wanted & {tag.lower() for tag in passage.tags}:
            score *= 1 + TAG_BOOST
        scored.append(Hit(passage, score))

    # Passage id last so an exact score tie always produces the same references.
    scored.sort(key=lambda hit: (-hit.score, hit.passage.id))

    kept: list[Hit] = []
    used = 0
    for hit in scored:
        if len(kept) >= limit:
            break
        # Always return the best passage, even if it alone exceeds the budget.
        if kept and used + len(hit.passage.text) > max_chars:
            continue
        kept.append(hit)
        used += len(hit.passage.text)
    return kept
