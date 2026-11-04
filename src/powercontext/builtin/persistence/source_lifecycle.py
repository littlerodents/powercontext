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

"""Relational storage for Source-driven Memory lifecycle dependency graphs.

The lifecycle graph is a DAG per scope: registered Source nodes carry the
authoritative availability driven by lifecycle events, and derived Memory and
Topic Memory nodes record the availability propagated through their all/any
policies. Registration order (``ordinal``) is also the topological order
because a parent must be registered before any child references it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import LIFECYCLE_EVENTS_TABLE, LIFECYCLE_NODES_TABLE


@dataclass(frozen=True, slots=True)
class LifecycleNodeRow:
    """One persisted lifecycle graph node."""

    node_id: str
    ordinal: int
    kind: str
    available: bool
    policy: str | None
    parents: tuple[str, ...]
    citation: bytes
    entry_id: str | None
    artifact_id: str | None
    revision: int | None
    sequence: int | None


@dataclass(frozen=True, slots=True)
class LifecycleEventRow:
    """One persisted lifecycle event for idempotent replay and retry."""

    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str
    applied: bool


async def load_nodes(connection: AsyncConnection, scope_id: str, /) -> tuple[LifecycleNodeRow, ...]:
    """Return every lifecycle node of one scope in registration (topological) order."""

    rows = (
        await connection.execute(
            select(LIFECYCLE_NODES_TABLE)
            .where(LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
            .order_by(LIFECYCLE_NODES_TABLE.c.ordinal)
        )
    ).mappings()
    return tuple(_decode_node(row) for row in rows)


async def next_ordinal(connection: AsyncConnection, scope_id: str, /) -> int:
    """Return the next registration ordinal for one scope's lifecycle graph."""

    current = await connection.scalar(
        select(func.max(LIFECYCLE_NODES_TABLE.c.ordinal)).where(LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
    )
    return 1 if current is None else int(current) + 1


async def insert_node(connection: AsyncConnection, scope_id: str, node: LifecycleNodeRow, /) -> None:
    """Persist one new lifecycle graph node."""

    await connection.execute(
        insert(LIFECYCLE_NODES_TABLE).values(
            scope_id=scope_id,
            node_id=node.node_id,
            ordinal=node.ordinal,
            kind=node.kind,
            available=node.available,
            policy=node.policy,
            parents=None if not node.parents else _encode_parents(node.parents),
            citation=node.citation,
            entry_id=node.entry_id,
            artifact_id=node.artifact_id,
            revision=node.revision,
            sequence=node.sequence,
        )
    )


async def load_event(
    connection: AsyncConnection,
    scope_id: str,
    node_id: str,
    sequence: int,
    /,
) -> LifecycleEventRow | None:
    """Return one stored lifecycle event, or None when it was never recorded."""

    row = (
        (
            await connection.execute(
                select(LIFECYCLE_EVENTS_TABLE).where(
                    LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                    LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                    LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _decode_event(row)


async def record_event(
    connection: AsyncConnection,
    scope_id: str,
    event: LifecycleEventRow,
    /,
) -> None:
    """Persist one accepted lifecycle event before its propagation completes."""

    await connection.execute(
        insert(LIFECYCLE_EVENTS_TABLE).values(
            scope_id=scope_id,
            node_id=event.node_id,
            sequence=event.sequence,
            revision=event.revision,
            available=event.available,
            reason=event.reason,
            applied=event.applied,
        )
    )


async def mark_event_applied(
    connection: AsyncConnection,
    scope_id: str,
    node_id: str,
    sequence: int,
    /,
) -> None:
    """Flag one lifecycle event as fully propagated."""

    await connection.execute(
        update(LIFECYCLE_EVENTS_TABLE)
        .where(
            LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
            LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
            LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
        )
        .values(applied=True)
    )


async def set_source_state(
    connection: AsyncConnection,
    scope_id: str,
    node_id: str,
    /,
    *,
    available: bool,
    sequence: int,
) -> None:
    """Record one Source's event-driven availability and last event sequence."""

    await connection.execute(
        update(LIFECYCLE_NODES_TABLE)
        .where(
            LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
            LIFECYCLE_NODES_TABLE.c.node_id == node_id,
        )
        .values(available=available, sequence=sequence)
    )


async def store_availability(
    connection: AsyncConnection,
    scope_id: str,
    updates: Mapping[str, bool],
    /,
) -> None:
    """Persist propagated availability for the given node ids of one scope."""

    for node_id, available in sorted(updates.items()):
        await connection.execute(
            update(LIFECYCLE_NODES_TABLE)
            .where(
                LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                LIFECYCLE_NODES_TABLE.c.node_id == node_id,
            )
            .values(available=available)
        )


async def unavailable_topic_artifact_ids(connection: AsyncConnection, scope_id: str, /) -> frozenset[str]:
    """Return artifact ids of withdrawn Topic Memory nodes in one scope."""

    rows = await connection.scalars(
        select(LIFECYCLE_NODES_TABLE.c.artifact_id).where(
            LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
            LIFECYCLE_NODES_TABLE.c.kind == "topic",
            LIFECYCLE_NODES_TABLE.c.available.is_(False),
            LIFECYCLE_NODES_TABLE.c.artifact_id.is_not(None),
        )
    )
    return frozenset(str(value) for value in rows)


def _decode_node(row: Mapping[Any, Any], /) -> LifecycleNodeRow:
    parents = row["parents"]
    return LifecycleNodeRow(
        node_id=str(row["node_id"]),
        ordinal=int(row["ordinal"]),
        kind=str(row["kind"]),
        available=bool(row["available"]),
        policy=None if row["policy"] is None else str(row["policy"]),
        parents=() if parents is None else tuple(str(item) for item in _decode_parents(parents)),
        citation=bytes(row["citation"]),
        entry_id=None if row["entry_id"] is None else str(row["entry_id"]),
        artifact_id=None if row["artifact_id"] is None else str(row["artifact_id"]),
        revision=None if row["revision"] is None else int(row["revision"]),
        sequence=None if row["sequence"] is None else int(row["sequence"]),
    )


def _decode_event(row: Mapping[Any, Any], /) -> LifecycleEventRow:
    return LifecycleEventRow(
        node_id=str(row["node_id"]),
        sequence=int(row["sequence"]),
        revision=int(row["revision"]),
        available=bool(row["available"]),
        reason=str(row["reason"]),
        applied=bool(row["applied"]),
    )


def _encode_parents(parents: tuple[str, ...], /) -> bytes:
    return json.dumps(list(parents), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_parents(payload: Any, /) -> list[str]:
    return list(json.loads(bytes(payload).decode("utf-8")))
