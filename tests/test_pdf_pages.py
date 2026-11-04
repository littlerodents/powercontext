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

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.runtime import MemorySearchPage
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _blank_pdf(page_count: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_parse_covers_every_physical_page_and_projects_citation_metadata() -> None:
    content = _blank_pdf()
    pages = parse_pdf_document(PDFDocumentInput(document_id="control", content=content))

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "")]
    digest = hashlib.sha256(content).hexdigest()
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    for page in pages:
        assert page.document_sha256 == digest
        source = asyncio.run(registry.resolve(page))
        assert len(source.name) <= 256
        assert asyncio.run(registry.read(source)) == page
        observation = project_source_for_transport(registry, source)
        text = observation.projection(TEXT_EVIDENCE_PROJECTION_KEY)
        assert text == {
            "source_type": "pdf-page",
            "source_id": source.name,
            "content": f"{page.document_id}\n{page.text}",
            "metadata": {
                "document_id": "control",
                "document_sha256": digest,
                "page_number": page.page_number,
            },
        }


def test_pdf_revision_changes_source_identity() -> None:
    original = parse_pdf_document(PDFDocumentInput(document_id="control", content=_blank_pdf(1)))
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_metadata({"/Title": "New revision"})
    buffer = io.BytesIO()
    writer.write(buffer)
    updated = parse_pdf_document(PDFDocumentInput(document_id="control", content=buffer.getvalue()))

    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    names = [asyncio.run(registry.resolve(page)).name for page in (original[0], updated[0])]
    assert names[0] != names[1]


def test_empty_password_permission_marker_is_readable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")  # noqa: S106  # test fixture only
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=buffer.getvalue()))
    assert len(pages) == 1 and pages[0].page_number == 1


@pytest.mark.parametrize("raw", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_corrupt_documents_fail_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


def test_blank_document_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="document_id"):
        PDFDocumentInput(document_id="   ", content=_blank_pdf(1))


class _StubMemory:
    """Return one fixed pool so reranking behavior is observable without storage."""

    def __init__(self, hits: tuple[MemoryHit, ...]) -> None:
        self._hits = hits

    async def search(self, request, /) -> MemorySearchPage:
        assert request.mode == "fts"
        if not self._hits:
            return MemorySearchPage(memory_ref=None, mode="fts", hits=())
        return MemorySearchPage(memory_ref=self._hits[0].memory_ref, mode="fts", hits=self._hits)


def _hit(entry_id: str, text: str, score: float) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-v1",
        text=text,
        score=score,
        matched_by=("fts",),
    )


def test_search_returns_distinct_lexically_ranked_citations() -> None:
    hits = (
        _hit("noise", "GENERIC\n2022 2022 2022", 0.9),
        _hit("revenue", "DOC\nNet revenue for fiscal 2022 was 86,392 million.", 0.1),
        _hit("costs", "DOC\nCost of sales in 2022.", 0.2),
    )
    citations = asyncio.run(search_pdf_pages(_StubMemory(hits), "What was net revenue in fiscal 2022?", limit=3))

    assert [citation.entry_id for citation in citations] == ["noise", "revenue", "costs"]
    assert len({citation.entry_id for citation in citations}) == 3
    assert all(citation.entry_version_id == f"{citation.entry_id}-v1" for citation in citations)
    assert all(citation.memory_ref == hits[0].memory_ref for citation in citations)

    trimmed = asyncio.run(search_pdf_pages(_StubMemory(hits), "What was net revenue in fiscal 2022?", limit=2))
    assert len(trimmed) == 2 and len({citation.entry_id for citation in trimmed}) == 2


def test_search_returns_empty_tuple_without_hits() -> None:
    assert asyncio.run(search_pdf_pages(_StubMemory(()), "What was net revenue in fiscal 2022?")) == ()


def test_search_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(search_pdf_pages(_StubMemory((_hit("a", "text", 0.1),)), "question", limit=0))
    with pytest.raises(ValueError, match="question"):
        asyncio.run(search_pdf_pages(_StubMemory((_hit("a", "text", 0.1),)), "   "))
