"""Choose query-relevant literal passages while measuring the complete wire context."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedEvidenceFragment,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_STOP = frozenset([
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "of",
    "to",
    "for",
    "in",
    "on",
    "at",
    "by",
    "as",
    "and",
    "or",
    "with",
    "from",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "their",
    "there",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "can",
    "may",
    "will",
    "would",
    "should",
    "which",
    "who",
    "what",
    "how",
    "show",
    "shows",
])


def _key(address: MemoryEntryAddress) -> tuple[str, str, int, str, str]:
    return (
        address.memory.scope_id,
        address.memory.artifact.artifact_id,
        address.memory.artifact.revision,
        address.entry_id,
        address.entry_version_id,
    )


def _terms(text: str) -> Counter[str]:
    words: list[str] = []
    for word in re.findall(r"[^\W_]+", text.casefold()):
        if word in _STOP:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return Counter(words)


@dataclass(frozen=True)
class _Sentence:
    fragment: PreparedEvidenceFragment
    terms: Counter[str]


def _sentences(groups: Sequence[PreparedMemoryCandidates]) -> list[_Sentence]:
    result: list[_Sentence] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for group in groups:
        for hit in group.hits:
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            if _key(address) in seen:
                continue
            seen.add(_key(address))
            start = 0
            for match in re.finditer(r"[.!?。！？](?=\s|$)|\n+|$", hit.text):  # noqa: RUF001 - multilingual sentence boundaries
                end = match.end()
                raw = hit.text[start:end]
                left = start + len(raw) - len(raw.lstrip())
                right = end - (len(raw) - len(raw.rstrip()))
                if left < right:
                    fragment = PreparedEvidenceFragment(address, left, right, hit.text[left:right])
                    result.append(_Sentence(fragment, _terms(fragment.text)))
                start = end
    return result


def _render(fragments: Sequence[PreparedEvidenceFragment]) -> str:
    items = [
        {
            "citation": {
                "scope_id": item.address.memory.scope_id,
                "memory_ref": item.address.memory.artifact.model_dump(mode="json"),
                "entry_id": item.address.entry_id,
                "entry_version_id": item.address.entry_version_id,
            },
            "content": item.text,
            "start": item.start,
            "end": item.end,
        }
        for item in fragments
    ]
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return (
        TRUST_POLICY
        + "\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        + encoded
        + "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
    )


def _merged(
    selected: frozenset[int], sentences: Sequence[_Sentence], texts: dict[tuple[str, str, int, str, str], str]
) -> tuple[PreparedEvidenceFragment, ...]:
    fragments: list[PreparedEvidenceFragment] = []
    for index in sorted(selected):
        fragment = sentences[index].fragment
        if fragments:
            previous = fragments[-1]
            gap = (
                texts[_key(fragment.address)][previous.end : fragment.start]
                if previous.address == fragment.address
                else "x"
            )
            if previous.address == fragment.address and not gap.strip():
                fragments[-1] = PreparedEvidenceFragment(
                    fragment.address,
                    previous.start,
                    fragment.end,
                    texts[_key(fragment.address)][previous.start : fragment.end],
                )
                continue
        fragments.append(fragment)
    return tuple(fragments)


def _sentence_scores(sentences: Sequence[_Sentence], question: str) -> list[float]:
    query = _terms(question)
    frequency: Counter[str] = Counter()
    for sentence in sentences:
        frequency.update(sentence.terms.keys())
    weights = {term: math.log1p((len(sentences) + 1) / (frequency[term] + 0.5)) for term in query}
    scores: list[float] = []
    average_length = sum(sum(sentence.terms.values()) for sentence in sentences) / len(sentences) or 1
    for sentence in sentences:
        length = sum(sentence.terms.values())
        score = sum(
            weight
            * (2.2 * sentence.terms[term])
            / (sentence.terms[term] + 1.2 * (0.25 + 0.75 * length / average_length))
            for term, weight in weights.items()
            if sentence.terms[term]
        )
        scores.append(score)
    return scores


def _candidate_windows(sentences: Sequence[_Sentence], scores: Sequence[float]) -> list[frozenset[int]]:
    windows: list[frozenset[int]] = []
    for start in range(len(sentences)):
        for end in range(start + 1, min(start + 4, len(sentences)) + 1):
            if sentences[end - 1].fragment.address != sentences[start].fragment.address:
                break
            if any(scores[index] > 0 for index in range(start, end)):
                windows.append(frozenset(range(start, end)))
    windows.sort(key=lambda window: (-sum(scores[index] for index in window), tuple(sorted(window))))
    return windows


def build_evidence_context(
    request: PrepareContextRequest, groups: Sequence[PreparedMemoryCandidates]
) -> PreparedContextBuild:
    """Rank sentence windows and pack them with scoped citations and exact character offsets."""
    sentences = _sentences(groups)
    if not sentences:
        return PreparedContextBuild(PreparedContext(status="empty", content=None, content_bytes=0), ())
    scores = _sentence_scores(sentences, request.query)
    texts = {
        _key(
            MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        ): hit.text
        for group in groups
        for hit in group.hits
    }
    windows = _candidate_windows(sentences, scores)
    selected: frozenset[int] = frozenset()
    content = ""
    # Extending adjacent sentences shares a citation, so every trial measures a merged rendering.
    for _ in range(8):
        best: tuple[float, frozenset[int], str] | None = None
        for window in windows:
            added = window - selected
            if not added:
                continue
            proposed = selected | window
            rendered = _render(_merged(proposed, sentences, texts))
            cost = len(rendered.encode("utf-8"))
            if cost > request.max_bytes:
                continue
            gain = sum(scores[index] for index in added) / (len(added) ** 0.15)
            if best is None or gain > best[0]:
                best = gain, proposed, rendered
        if best is None or best[0] <= 0:
            break
        _, selected, content = best
    if not selected:
        return PreparedContextBuild(PreparedContext(status="empty", content=None, content_bytes=0), ())
    fragments = _merged(selected, sentences, texts)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
    )
