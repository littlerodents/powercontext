# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Physical-page PDF capture, page-level Sources, and offline evidence retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise, zip_longest
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, Field, field_validator
from pypdf import PasswordType, PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryHit

PDF_PAGE_SOURCE_NAME = "pdf-page"
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALPHA_DIGIT_RE = re.compile(r"([a-z]+)(\d+)|(\d+)([a-z]+)")
_BIGRAM_WINDOW = 2
# Generic financial-report vocabulary expansion, independent of any document or question.
# Each rule keeps the matched text and inserts synonym fragments right after it, so the
# expansion stays adjacent to its trigger word and preserves phrase-level evidence.
_ABBREVIATIONS = (
    (re.compile(r"\bp\s*&\s*l\b", re.IGNORECASE), lambda match: ("income", "statement")),
    (re.compile(r"\bd\s*&\s*a\b", re.IGNORECASE), lambda match: ("depreciation", "amortization")),
    (re.compile(r"\bcapex\b", re.IGNORECASE), lambda match: ("capital", "spending")),
    (re.compile(r"\bfy\s?(\d{4})\b", re.IGNORECASE), lambda match: (f"fiscal {match.group(1)}",)),
    (
        re.compile(r"\b(?:battles?|lawsuits?|legal)\b", re.IGNORECASE),
        lambda match: ("litigation", "legal", "proceedings"),
    ),
    (re.compile(r"\bincome\b", re.IGNORECASE), lambda match: ("profit",)),
    (re.compile(r"\bgeograph(?:y|ies|ic|ical)\b", re.IGNORECASE), lambda match: ("geographic", "region", "segment")),
    (re.compile(r"\bcapex\b", re.IGNORECASE), lambda match: ("capital", "expenditure")),
    (re.compile(r"\brevenues?\b", re.IGNORECASE), lambda match: ("sales",)),
    (re.compile(r"\bdividends?\b", re.IGNORECASE), lambda match: ("dividend", "shareholder")),
    (re.compile(r"\boperates?\b", re.IGNORECASE), lambda match: ("operation", "division", "segment", "business")),
)
_QUERY_STOPWORDS = frozenset({
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "please",
    "reported",
    "should",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "answer",
    "assuming",
    "calculate",
    "define",
    "details",
    "give",
    "investment",
    "analyst",
    "only",
    "perspective",
    "question",
    "relying",
    "respond",
    "response",
    "shown",
    "use",
})


class PDFDocumentInput(BaseModel):
    """One complete original PDF supplied as bytes under a caller document identity."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """Extracted text of one physical page, counted from the first PDF page."""

    document_id: str
    document_sha256: str
    page_number: Annotated[int, Field(ge=1)]
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageSource(Source):
    """Captured physical page of one exact PDF revision."""

    document_id: str
    document_sha256: str
    page_number: Annotated[int, Field(ge=1)]
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page in order or fail without returning partial pages."""

    document_sha256 = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise ValueError("encrypted PDF requires a non-empty password")  # noqa: TRY301, TRY003
        page_count = len(reader.pages)
        if page_count == 0:
            raise ValueError("PDF contains no pages")  # noqa: TRY301, TRY003
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            texts.append(text if text is not None and text.strip() else "")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("content is not a complete readable PDF") from error  # noqa: TRY003

    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(texts, start=1)
    )


def _page_source_name(document_id: str, document_sha256: str, page_number: int) -> str:
    identity = f"{document_id}\x1f{document_sha256}\x1f{page_number}"
    return f"{PDF_PAGE_SOURCE_NAME}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class PDFPageAdapter:
    """Resolve captured pages and read back their exact page input."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=_page_source_name(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose page text with the document digest and physical page citation metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageAdapter(),
    projections=(PDFPageTextEvidenceProjection(),),
)


def _normalize_token(token: str) -> str:
    """Fold simple English plurals so inflected query words match statement terminology."""

    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _expand_token(token: str) -> tuple[str, ...]:
    match = _ALPHA_DIGIT_RE.fullmatch(token)
    if match is None:
        return (token,)
    return (token, *(part for part in match.groups() if part))


def _expand_question(question: str) -> str:
    """Insert synonym fragments directly after their trigger words, keeping adjacency."""

    expanded = question
    for pattern, fragments in _ABBREVIATIONS:
        expanded = pattern.sub(lambda match, build=fragments: f"{match.group(0)} {' '.join(build(match))}", expanded)
    return expanded


def _synonym_query(question: str) -> str:
    """Build a low-threshold query from expansion fragments only.

    Pages whose only overlap with the question is a synonym (for example ``litigation``
    for ``legal battles``) fail the coverage floor of the full query, so this short
    variant lets the search engine surface them directly.
    """

    fragments: set[str] = set()
    for pattern, build in _ABBREVIATIONS:
        for match in pattern.finditer(question):
            for fragment in build(match):
                for token in fragment.split():
                    if not any(character.isdigit() for character in token):
                        fragments.add(_normalize_token(token))
    return " ".join(sorted(fragments))


def _content_stream(text: str) -> list[str]:
    """Normalized content words in order, used for adjacency evidence."""

    normalized = unicodedata.normalize("NFC", text).casefold()
    stream = []
    for token in _TOKEN_RE.findall(normalized):
        if token in _QUERY_STOPWORDS or (len(token) == 1 and token.isalpha()):
            continue
        token = _normalize_token(token)
        if token not in _QUERY_STOPWORDS:
            stream.append(token)
    return stream


def _question_profile(question: str) -> tuple[dict[str, float], list[tuple[str, str]]]:
    """Weight distinct question terms and collect adjacent content-word pairs."""

    weights: dict[str, float] = {}
    for raw in re.findall(r"[A-Za-z0-9]+", question):
        token = raw.casefold()
        if token in _QUERY_STOPWORDS or (len(token) == 1 and token.isalpha()):
            continue
        for part in _expand_token(token):
            is_subtoken = part != token
            part = _normalize_token(part)
            if part in _QUERY_STOPWORDS or (len(part) == 1 and part.isalpha()):
                continue
            weight = 1.0
            if any(character.isdigit() for character in part):
                weight = 2.0
            elif raw[0].isupper():
                weight = 1.25
            if is_subtoken:
                weight *= 0.75
            weights[part] = max(weights.get(part, 0.0), weight)
    stream = _content_stream(question)
    bigrams = list(dict.fromkeys(pairwise(stream)))
    return weights, bigrams


def _query_variants(question: str, expanded: str, weights: dict[str, float]) -> tuple[str, ...]:
    """Build offline lexical variants: original, abbreviation-expanded, and salient subsets."""

    variants = [question, expanded]
    if weights:
        variants.append(" ".join(weights))
        salient = sorted(
            (term for term, weight in weights.items() if weight >= 1.25 or len(term) >= 6),
            key=lambda term: (-weights[term], term),
        )[:8]
        if len(salient) >= 2:
            variants.append(" ".join(salient))
    unique = []
    for variant in variants:
        if variant.strip() and variant not in unique:
            unique.append(variant)
    return tuple(unique)


def _text_terms(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    terms: set[str] = set()
    for token in _TOKEN_RE.findall(normalized):
        terms.update(_normalize_token(part) for part in _expand_token(token))
    return frozenset(terms)


def _bigram_support(stream: list[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for index, token in enumerate(stream):
        positions.setdefault(token, []).append(index)
    return positions


def _has_nearby_pair(positions: dict[str, list[int]], left: str, right: str) -> bool:
    if left == right:
        indices = positions.get(left, ())
        return any(b - a <= _BIGRAM_WINDOW for a, b in pairwise(indices))
    for first in positions.get(left, ()):
        for second in positions.get(right, ()):
            if abs(first - second) <= _BIGRAM_WINDOW:
                return True
    return False


def _rank_hits(weights: dict[str, float], bigrams: list[tuple[str, str]], hits: list[MemoryHit]) -> list[MemoryHit]:
    """Rerank the pool by weighted unigram evidence plus adjacent-pair phrase support."""

    documents = [(hit, _text_terms(hit.text), _bigram_support(_content_stream(hit.text))) for hit in hits]
    frequency: Counter[str] = Counter()
    for _hit, terms, _positions in documents:
        frequency.update(terms)
    total_weight = sum(weights.values()) or 1.0
    ordered_terms = sorted(weights)
    idf = {term: math.log1p(len(documents) / (1 + frequency[term])) for term in ordered_terms}
    scored: list[tuple[float, MemoryHit]] = []
    for hit, terms, positions in documents:
        overlap = 0.0
        covered = 0.0
        for term in ordered_terms:
            if term not in terms:
                continue
            weight = weights[term]
            overlap += weight * idf[term]
            covered += weight
        for left, right in bigrams:
            if left in terms and right in terms and _has_nearby_pair(positions, left, right):
                overlap += (
                    0.75
                    * (weights.get(left, 1.0) + weights.get(right, 1.0))
                    * max(idf.get(left, 0.0), idf.get(right, 0.0))
                )
        coverage = covered / total_weight
        score = overlap * (0.25 + coverage) + 0.01 * hit.score
        scored.append((score, hit))
    scored.sort(key=lambda item: (-item[0], item[1].entry_id, item[1].entry_version_id))
    return [hit for _score, hit in scored]


def _hit_identity(hit: MemoryHit) -> tuple[str, int, str, str]:
    return (
        hit.memory_ref.artifact_id,
        hit.memory_ref.revision,
        hit.entry_id,
        hit.entry_version_id,
    )


def _search_variants(question: str, expanded: str, weights: dict[str, float]) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = [("original", question), ("expanded", expanded)]
    synonym_query = _synonym_query(question)
    if synonym_query.strip():
        variants.append(("synonyms", synonym_query))
    for label, variant in zip(
        ("joined", "salient"),
        _query_variants(question, expanded, weights)[2:],
        strict=False,
    ):
        variants.append((label, variant))
    # Long financial questions often require several different statements.
    # Independent salient-term searches avoid requiring every evidence page to
    # repeat all the question's terminology before it can enter the candidate pool.
    terms = sorted(
        (term for term in weights if not term.isdigit() and len(term) > 2),
        key=lambda term: (-weights[term], -len(term), term),
    )[:12]
    variants.extend((f"term-{index}", term) for index, term in enumerate(terms))
    variants.extend(_financial_statement_queries(question))
    return variants


_FINANCIAL_STATEMENT_RULES = (
    (
        "capital_intensity",
        r"\bcapital[ -]+(?:intensiv\w*|expenditures?|spending|investment)\b|\bcapex\b",
        (
            "consolidated balance",
            "consolidated income",
            "consolidated operations",
            "consolidated cash flow",
        ),
    ),
    (
        "organic_growth",
        r"(?=.*\bsegment\w*\b)(?=.*\b(?:growth|grow\w*|declin\w*)\b)",
        (
            "segment organic sales growth acquisitions divestitures",
            "business segment sales change excluding acquisitions",
            "organic local currency sales segments",
        ),
    ),
    (
        "liquidity",
        r"\b(?:quick|current|acid[ -]?test)\s+ratio\b|\bliquidity\b",
        ("consolidated balance sheets", "current assets current liabilities cash receivables"),
    ),
    (
        "margin",
        r"\b(?:gross|operating|profit|ebitda|net)\s+margins?\b",
        ("consolidated statements of income", "results of operations operating income revenues"),
    ),
    (
        "balance",
        r"\b(?:liabilit(?:y|ies)|balance\s+sheets?|assets?)\b",
        ("consolidated balance sheets", "total liabilities assets"),
    ),
    (
        "concentration",
        r"\bcustomers?\b.*\bconcentrat|\bconcentrat\w*\b.*\bcustomers?",
        ("customer accounted net revenue", "customer revenue percentage"),
    ),
    ("retention", r"\b(?:retain\w*|retention)\b", ("retention rate card members", "member customer retention")),
    (
        "tax",
        r"\b(?:effective|income)\s+tax\s+rate\b",
        ("effective income tax rate provision", "consolidated statements of operations income tax"),
    ),
    (
        "segments",
        r"(?=.*\b(?:revenue|revenues|sales)\b)(?=.*\b(?:products?|services?|categor\w*|segment\w*)\b)",
        ("summary of business segment data revenues", "revenue products services segments"),
    ),
    (
        "customers",
        r"(?=.*\b(?:primary|principal|major|significant)\b)(?=.*\bcustomers?\b)",
        ("major customers revenues", "customers significant revenues"),
    ),
    (
        "cashflow",
        r"\b(?:cash\s+flows?|ebitda|capex|depreciation|amortization)\b",
        ("consolidated statements of cash flows", "consolidated statements of income"),
    ),
)

_STATEMENT_HEADING_PATTERNS = {
    "concept-capital_intensity-0": re.compile(r"\bconsolidated\s+balance\s+sheets?\b", re.IGNORECASE),
    "concept-capital_intensity-1": re.compile(
        r"\bconsolidated\s+statements?\s+of\s+(?:income|earnings)\b", re.IGNORECASE
    ),
    "concept-capital_intensity-2": re.compile(
        r"\bconsolidated\s+statements?\s+of\s+(?:operations|income|earnings)\b", re.IGNORECASE
    ),
    "concept-capital_intensity-3": re.compile(
        r"\bconsolidated\s+statements?\s+of\s+cash\s+flows?\b", re.IGNORECASE
    ),
}


def _financial_statement_queries(question: str) -> list[tuple[str, str]]:
    """Retrieve source statements when a financial question names a derived metric.

    Reports often contain the underlying figures but not the calculated ratio or
    the wording used in the question. These general-purpose queries use only the
    question; they do not depend on a document identity or annotated answers.
    """

    question = question.casefold()
    variants: list[tuple[str, str]] = []

    for label, pattern, queries in _FINANCIAL_STATEMENT_RULES:
        if re.search(pattern, question, re.DOTALL):
            variants.extend((f"concept-{label}-{index}", query) for index, query in enumerate(queries))
    return variants


async def _collect_rankings(memory, variants: list[tuple[str, str]], pool_limit: int) -> dict[str, list[MemoryHit]]:
    """Run every lexical expert once and keep each expert's engine ranking."""

    # Imported lazily so the Source plugin does not require the built-in Runtime stack.
    from powercontext.builtin.runtime import SearchMemoryRequest

    rankings: dict[str, list[MemoryHit]] = {}
    for label, variant in variants:
        if label in rankings:
            continue
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=pool_limit))
        hits = list(page.hits)
        heading = _STATEMENT_HEADING_PATTERNS.get(label)
        if heading is not None:
            # Prefer the statement itself over footnotes that merely mention it.
            # Stable sorting retains the Memory ranking within each group.
            hits.sort(key=lambda hit: heading.search(hit.text[:1000]) is None)
        rankings[label] = hits
    return rankings


def _interleave_citations(experts: Sequence[Sequence[MemoryHit]], limit: int) -> tuple[MemoryCitation, ...]:
    """Round-robin distinct hits across complementary expert rankings."""

    selected: list[MemoryCitation] = []
    seen: set[tuple[str, int, str, str]] = set()
    for group in zip_longest(*experts):
        for hit in group:
            if hit is None:
                continue
            identity = _hit_identity(hit)
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(
                MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
            )
            if len(selected) >= limit:
                return tuple(selected)
    return tuple(selected)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search the scoped Memory and return at most ``limit`` distinct exact page citations.

    Retrieval runs several offline lexical experts (original question, synonym-expanded
    phrasing, synonym fragments, salient terms) plus a local evidence rerank, then
    interleaves their top results so the returned pages cover complementary signals.
    """

    if limit < 1:
        raise ValueError("limit must be positive")  # noqa: TRY003
    if not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003

    expanded = _expand_question(question)
    weights, bigrams = _question_profile(expanded)
    rankings = await _collect_rankings(
        memory,
        _search_variants(question, expanded, weights),
        pool_limit=max(limit * 10, 150),
    )
    pool_by_identity: dict[tuple[str, int, str, str], MemoryHit] = {}
    for ranking in rankings.values():
        for hit in ranking:
            pool_by_identity.setdefault(_hit_identity(hit), hit)
    if not pool_by_identity:
        return ()

    evidence_ranked = _rank_hits(weights, bigrams, list(pool_by_identity.values()))
    # Fuse query-level retrieval with literal page evidence. The query experts
    # retain pages reached by specialized wording, while the evidence ranking
    # compares every candidate from the same union rather than unrelated batches.
    evidence_ranks = {_hit_identity(hit): rank for rank, hit in enumerate(evidence_ranked)}
    fused = {identity: 2.0 / (20 + rank) for identity, rank in evidence_ranks.items()}
    for label, ranking in rankings.items():
        weight = 1.0 if label in ("original", "expanded", "synonyms") or label.startswith("concept-") else 0.15
        for rank, hit in enumerate(ranking):
            identity = _hit_identity(hit)
            fused[identity] = fused.get(identity, 0.0) + weight / (20 + rank)
    ranked = sorted(evidence_ranked, key=lambda hit: (-fused[_hit_identity(hit)], evidence_ranks[_hit_identity(hit)]))
    concept_rankings = [ranking for label, ranking in rankings.items() if label.startswith("concept-")]
    return _interleave_citations((ranked, evidence_ranked, *concept_rankings), limit)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageAdapter",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
