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

"""Capture immutable visible events from a Pi v3 session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionError(ValueError):
    """The archive or its imported immutable history is invalid."""


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(session_id: str, event_id: str) -> str:
    return "pi:" + hashlib.sha256(_json([session_id, event_id]).encode()).hexdigest()


def _archive(value: PiSessionInput) -> tuple[str, dict[str, dict[str, Any]], str | None]:
    try:
        records = [json.loads(line) for line in value.content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeError, ValueError) as error:
        raise PiSessionError("Pi archive must contain UTF-8 JSONL") from error  # noqa: TRY003 - archive validation contract
    if not records or not isinstance(records[0], dict):
        raise PiSessionError("missing Pi session header")  # noqa: TRY003 - archive validation contract
    header = records[0]
    session_id = header.get("id")
    if (
        header.get("type") != "session"
        or type(header.get("version")) is not int
        or header["version"] != 3
        or not isinstance(session_id, str)
        or not session_id.strip()
    ):
        raise PiSessionError("invalid Pi v3 session header")  # noqa: TRY003 - archive validation contract
    nodes = _nodes(records[1:])
    leaf = next(reversed(nodes), None)
    if value.leaf_id is not None:
        if value.leaf_id not in nodes:
            raise PiSessionError("selected Pi leaf does not exist")  # noqa: TRY003 - archive validation contract
        leaf = value.leaf_id
    return session_id, nodes, leaf


def _nodes(records: list[Any]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise PiSessionError("Pi nodes must be JSON objects")  # noqa: TRY003 - archive validation contract
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in nodes:
            raise PiSessionError("Pi nodes require distinct nonempty ids")  # noqa: TRY003 - archive validation contract
        if "parentId" not in record:
            raise PiSessionError("Pi node is missing parentId")  # noqa: TRY003 - archive validation contract
        parent = record["parentId"]
        if parent is not None and (not isinstance(parent, str) or parent not in nodes):
            raise PiSessionError("Pi parent must reference an earlier node")  # noqa: TRY003 - archive validation contract
        # Validate serializability before any connector submission, including hidden nodes.
        _json(record)
        nodes[event_id] = record
    return nodes


def _text(content: Any, *, tools: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif tools and block.get("type") == "toolCall":
            parts.append(
                f"[tool_call {block.get('name', '')} {block.get('id', '')}] {_json(block.get('arguments', {}))}"
            )
    return "\n".join(parts)


def _event(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise PiSessionError("Pi message requires a role")  # noqa: TRY003 - archive validation contract
        role = message["role"]
        if role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            if not isinstance(command, str) or not isinstance(output, str):
                raise PiSessionError("Pi bash command and output must be strings")  # noqa: TRY003 - archive validation contract
            body = f"$ {command}\n{output}" if command or output else ""
        else:
            body = _text(message.get("content"), tools=True)
            if role == "toolResult" and body.strip():
                body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {body}"
    elif event_type in ("compaction", "branch_summary"):
        role, body = event_type, record.get("summary", "")
        if not isinstance(body, str):
            raise PiSessionError("Pi summary must be a string")  # noqa: TRY003 - archive validation contract
    elif event_type == "custom_message":
        role, body = "custom", _text(record.get("content"))
    else:
        return None
    if not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record["parentId"],
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _branch(session_id: str, nodes: dict[str, dict[str, Any]], leaf: str | None) -> tuple[PiEventInput, ...]:
    path: list[dict[str, Any]] = []
    while leaf is not None:
        node = nodes[leaf]
        path.append(node)
        leaf = node["parentId"]
    return tuple(event for record in reversed(path) if (event := _event(session_id, record)) is not None)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate the complete archive, then expose the selected root-to-leaf history."""
    return _branch(*_archive(value))


class _PiEventAdapter:
    name = "pi-event"
    input_class = PiEventInput
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(**{key: getattr(source, key) for key in PiEventInput.model_fields})


class _PiTextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                key: getattr(source, key) for key in ("session_id", "event_id", "parent_id", "event_type", "role")
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(_PiEventAdapter(), version="1", projections=(_PiTextProjection(),))


class PiSessionConnector:
    """Checkpoint imported identities and raw-record digests through ConnectorLifecycle."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path, self.leaf_id = Path(path), leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, nodes, leaf = _archive(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        events = _branch(session_id, nodes, leaf)
        previous = session.checkpoint
        if previous is None:
            imported: dict[str, dict[str, str]] = {}
        elif isinstance(previous, dict) and previous.get("version") == 1 and isinstance(previous.get("sessions"), dict):
            imported = {key: dict(value) for key, value in previous["sessions"].items()}
        else:
            raise PiSessionError("invalid Pi connector checkpoint")  # noqa: TRY003 - archive validation contract
        known = imported.setdefault(session_id, {})
        for event_id, digest in known.items():
            if event_id not in nodes or hashlib.sha256(_json(nodes[event_id]).encode()).hexdigest() != digest:
                raise PiSessionError("previously imported Pi event was changed or removed")  # noqa: TRY003 - archive validation contract
        for event in events:
            if event.event_id in known:
                continue
            await session.submit(_identity(session_id, event.event_id), "pi-event", event)
            known[event.event_id] = hashlib.sha256(_json(event.record).encode()).hexdigest()
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": 1, "sessions": imported},
        )
