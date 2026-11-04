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

"""Row-level CSV Source capture with exact identity and text-evidence projection."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document plus its durable identity inputs."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One exact CSV record: header-ordered columns and raw string values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row whose identity is ``csv:<document>:<primary key value>``."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def _validate_document_id(document_id: str, /) -> str:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Build the stable percent-encoded row identity and enforce its length bound."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty, non-blank string")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("csv row source_id must not exceed 256 characters")  # noqa: TRY003
    return identity


def _validate_header(record: list[str], /) -> tuple[str, ...]:
    if not record:
        raise ValueError("csv document must have a non-empty header")  # noqa: TRY003
    for field in record:
        if not field.strip():
            raise ValueError("csv header fields must be non-empty and not pure whitespace")  # noqa: TRY003
    if len(set(record)) != len(record):
        raise ValueError("csv header fields must be unique")  # noqa: TRY003
    return tuple(record)


def _decode_document(content: bytes, /) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("csv document must decode as strict UTF-8") from error  # noqa: TRY003
    if text.startswith(chr(0xFEFF)):
        text = text[1:]
    return text


def _read_header(reader: Iterator[list[str]], /) -> list[str]:
    try:
        return next(reader)
    except StopIteration:
        raise ValueError("csv document is empty") from None  # noqa: TRY003
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error  # noqa: TRY003


def _resolve_primary_key(columns: tuple[str, ...], primary_key: str, /) -> int:
    try:
        return columns.index(primary_key)
    except ValueError:
        raise ValueError("primary_key must exactly match one header field") from None  # noqa: TRY003


def _parse_data_rows(
    reader: Iterator[list[str]],
    /,
    *,
    columns: tuple[str, ...],
    key_index: int,
    document_id: str,
    primary_key: str,
) -> tuple[CSVRowInput, ...]:
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    try:
        for record in reader:
            if not record:
                continue  # a completely empty physical record carries no data
            if len(record) != len(columns):
                raise ValueError("csv data record width must equal the header width")  # noqa: TRY003
            primary_key_value = record[key_index]
            if not primary_key_value.strip():
                raise ValueError("csv primary key values must be non-empty and not pure whitespace")  # noqa: TRY003
            if primary_key_value in seen_keys:
                raise ValueError("csv primary key values must be unique within one document")  # noqa: TRY003
            seen_keys.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=document_id,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error  # noqa: TRY003
    return tuple(rows)


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and validate the whole document, then return every row atomically."""

    text = _decode_document(value.content)
    _validate_document_id(value.document_id)
    if not isinstance(value.primary_key, str) or not value.primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    columns = _validate_header(_read_header(reader))
    key_index = _resolve_primary_key(columns, value.primary_key)
    return _parse_data_rows(
        reader,
        columns=columns,
        key_index=key_index,
        document_id=value.document_id,
        primary_key=value.primary_key,
    )


class _CSVRowTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` projection for one CSV row."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowAdapter:
    """Resolve one exact ``CSVRowInput`` into its captured ``CSVRowSource``."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    _CSVRowAdapter(),  # type: ignore[arg-type]
    version="1",
    projections=(_CSVRowTextEvidenceProjection(),),  # type: ignore[list-item]
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
