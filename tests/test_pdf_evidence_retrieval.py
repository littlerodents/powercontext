# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""Regression coverage for questions requiring financial-statement evidence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFPageInput,
    _question_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class _EvidencePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def test_report_identification_retains_ambiguous_comparisons() -> None:
    rankings = {
        "original": [
            SimpleNamespace(text="ALDER_2024_10K\nBalance sheets"),
            SimpleNamespace(text="LUMINATOOLS_2024_10K\nBalance sheets"),
            SimpleNamespace(text="LUMINATOOLS_2023_10K\nBalance sheets"),
        ]
    }
    assert _question_document("Compare Alder and Lumina Tools for FY24", rankings) is None
    assert _question_document("What did Lumina Tools report in FY24?", rankings) == "LUMINATOOLS_2024_10K"
    assert _question_document("What changed in FY24?", rankings) is None


def test_financial_statement_can_be_found_without_literal_metric_name(tmp_path) -> None:
    """A ratio question must reach statement data even when the ratio is never written on the page."""

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'evidence.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        texts = [
            "Consolidated Balance Sheets. Current assets: cash and cash equivalents 50; "
            "accounts receivable 25. Total current liabilities 40. Property and equipment 200.",
            "Consolidated Statements of Income. Revenues 150. Total expenses 100.",
            "Consolidated Statements of Cash Flows. Purchases of property, plant and equipment 30.",
            "Business segment sales change: organic local currency sales excluding acquisitions and divestitures.",
            *(f"General discussion of quick ratio, operating margin and a capital-intensive business, "
              f"example {i}." for i in range(40)),
        ]
        pages = [
            PDFPageInput(document_id="synthetic-report", document_sha256="a" * 64, page_number=i, text=text)
            for i, text in enumerate(texts, 1)
        ]
        sources = [await registry.resolve(page) for page in pages]
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="Test", summary="PDF pages", idempotency_key="test"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for source in sources:
                observation = project_source_for_transport(registry, source)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
        async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for question, expected in (
                ("Does the report show a healthy quick ratio?", {sources[0].name}),
                ("What is the operating margin?", {sources[1].name}),
                ("Is this a capital-intensive business?", {source.name for source in sources[:3]}),
                ("Which segment's growth excludes mergers and acquisitions?", {sources[3].name}),
            ):
                citations = await search_pdf_pages(memory, question, limit=20)
                found = set()
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    found.update(source.source_id for source in entry.sources)
                assert expected <= found
                assert len(citations) <= 20

    asyncio.run(scenario())


def test_financial_statements_respect_company_in_a_shared_scope(tmp_path) -> None:
    """Similar statements from different reports must not crowd out the requested company."""

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'mixed.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        sources = []
        target_names = set()
        target_document_names = set()
        for company in ("ALDER", "BAYVIEW", "CEDAR", "DUNES", "LUMINATOOLS"):
            texts = [
                "Consolidated Balance Sheets. Total assets 500 and property equipment 100.",
                "Consolidated Statements of Income. Net sales 200 and operating income 20.",
                "Consolidated Statements of Cash Flows. Purchases of property and equipment 30.",
                *(f"Discussion of capital-intensive businesses and general market factors, section {i}." for i in range(12)),
            ]
            for number, text in enumerate(texts, 1):
                page = PDFPageInput(
                    document_id=f"{company}_2024_10K", document_sha256=company[0].lower() * 64,
                    page_number=number, text=text,
                )
                source = await registry.resolve(page)
                sources.append(source)
                if company == "LUMINATOOLS":
                    target_document_names.add(source.name)
                if company == "LUMINATOOLS" and number <= 3:
                    target_names.add(source.name)
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="Mixed", summary="PDF reports", idempotency_key="mixed"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for source in sources:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=project_source_for_transport(registry, source))
                )
        async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "Is Lumina Tools a capital-intensive business?", limit=3)
            found = set()
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                found.update(source.source_id for source in entry.sources)
            assert len(target_names & found) >= 2
            assert found <= target_document_names
            citations = await search_pdf_pages(memory, "Is Lumina Tools a capital-intensive business?", limit=20)
            found = set()
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                found.update(source.source_id for source in entry.sources)
            assert target_names <= found

    asyncio.run(scenario())
