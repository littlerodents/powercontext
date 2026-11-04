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

"""Source-driven deactivation and restoration of Memory and Topic Memory.

Sources stop being usable and the content that depends on them must leave the
current search and Context surface without losing its recorded body. This
module connects the explicitly registered evidence dependency graph to the
native availability state: withdrawal and restoration propagate through each
scope's dependency DAG, deactivated content keeps its exact body for audit,
and a Source that becomes usable again restores everything that is supported
once more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.source_lifecycle import (
    LifecycleEventRow,
    LifecycleNodeRow,
    insert_node,
    load_event,
    load_nodes,
    mark_event_applied,
    next_ordinal,
    record_event,
    set_source_state,
    store_availability,
    unavailable_topic_artifact_ids,
)
from powercontext.builtin.runtime.models import MemoryCitation
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory import MemoryEntryVersion
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.relational import RelationalContexts

__all__ = [
    "ScopedSourceLifecycleService",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]

_PURPOSES = ("generation", "audit")
_MAX_NODE_ID_LENGTH = 256


class SourceLifecycleError(ValueError):
    """Report an invalid registration, event, or resolution against the lifecycle graph."""


class SourceLifecycleEvent(BaseModel):
    """One availability transition for a registered Source.

    ``revision`` must exactly match the Source's registered lifecycle version
    and ``sequence`` must increase per Source; identical events replay
    idempotently while regressed or conflicting sequences are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def _require_visible_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty or blank")  # noqa: TRY003
        return value


class SourceLifecycleApplication:
    """Select the scoped Source lifecycle service."""

    def __init__(self, runtime: BuiltinRuntime, /) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        return ScopedSourceLifecycleService(self._runtime, scope_id)

    async def unavailable_topic_artifact_ids(self, scope_id: str, /) -> frozenset[str]:
        """Return withdrawn Topic Memory artifact ids kept out of native Topic search."""

        scope_id = validate_scope_id(scope_id)
        async with cast("RelationalContexts", self._runtime._provider).database.transaction() as connection:
            return await unavailable_topic_artifact_ids(connection, scope_id)


class ScopedSourceLifecycleService:
    """Register one scope's evidence dependency graph and drive its lifecycle events."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str, /) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        """Register one Source node at its initial lifecycle version.

        Identical registration is idempotent; a different binding for the same
        node id conflicts. The reference must exist and be readable.
        """

        node_id = _require_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-ref")
        _require_positive_int(revision, "revision")
        binding: tuple[object, ...] = ("source", source_ref.model_dump_json(), revision)
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._contexts.database.transaction() as connection,
        ):
            nodes = {node.node_id: node for node in await load_nodes(connection, self.scope_id)}
            existing = nodes.get(node_id)
            if existing is not None:
                _require_same_binding(existing, binding)
                return
            try:
                await self._contexts.repositories.sources.get(connection, self.scope_id, source_ref)
            except RepositoryNotFoundError:
                raise SourceLifecycleError("unknown-source-ref") from None
            await insert_node(
                connection,
                self.scope_id,
                LifecycleNodeRow(
                    node_id=node_id,
                    ordinal=await next_ordinal(connection, self.scope_id),
                    kind="source",
                    available=True,
                    policy=None,
                    parents=(),
                    citation=source_ref.model_dump_json().encode("utf-8"),
                    entry_id=None,
                    artifact_id=None,
                    revision=revision,
                    sequence=None,
                ),
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...] = (),
        policy: str = "all",
    ) -> None:
        """Register one Memory entry node bound to its exact recorded citation."""

        await self._register_derived(node_id, citation, parents, policy, kind="memory")

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...] = (),
        policy: str = "all",
    ) -> None:
        """Register one Topic Memory revision node bound to its exact ArtifactRef."""

        await self._register_derived(node_id, reference, parents, policy, kind="topic")

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source lifecycle event and return every available node id, sorted.

        The event is recorded before propagation, so an interrupted propagation
        completes when the same event is retried, and a fully applied replay
        performs no writes and creates no new Memory revisions.
        """

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event-type")
        async with self._runtime._scope_operation(self.scope_id):
            async with self._runtime._locked(self.scope_id):
                async with self._contexts.database.transaction() as connection:
                    nodes = {node.node_id: node for node in await load_nodes(connection, self.scope_id)}
                    node = nodes.get(event.node_id)
                    if node is None or node.kind != "source":
                        raise SourceLifecycleError("unknown-source")
                    if node.revision != event.revision:
                        raise SourceLifecycleError("revision-mismatch")
                    replay = False
                    if node.sequence is not None:
                        if event.sequence < node.sequence:
                            raise SourceLifecycleError("sequence-regressed")
                        if event.sequence == node.sequence:
                            stored = await load_event(connection, self.scope_id, event.node_id, event.sequence)
                            if stored is None or stored.available != event.available or stored.reason != event.reason:
                                raise SourceLifecycleError("sequence-conflict")
                            replay = True
                    if not replay:
                        await record_event(
                            connection,
                            self.scope_id,
                            LifecycleEventRow(
                                node_id=event.node_id,
                                sequence=event.sequence,
                                revision=event.revision,
                                available=event.available,
                                reason=event.reason,
                                applied=False,
                            ),
                        )
                        await set_source_state(
                            connection,
                            self.scope_id,
                            event.node_id,
                            available=event.available,
                            sequence=event.sequence,
                        )
                await self._propagate(event.node_id, event.sequence, event.reason)
                async with self._contexts.database.transaction() as connection:
                    stored = await load_nodes(connection, self.scope_id)
            return sorted(stored_node.node_id for stored_node in stored if stored_node.available)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Resolve the exact registered reference for one node and purpose.

        ``generation`` requires current availability; ``audit`` always returns
        the original reference so historical bodies stay exactly readable.
        """

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("unknown-purpose")
        node_id = _require_node_id(node_id)
        async with self._contexts.database.transaction() as connection:
            nodes = {node.node_id: node for node in await load_nodes(connection, self.scope_id)}
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("unavailable-node")
        return _decode_reference(node.kind, node.citation)

    async def _register_derived(
        self,
        node_id: str,
        reference: MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
        /,
        *,
        kind: Literal["memory", "topic"],
    ) -> None:
        node_id = _require_node_id(node_id)
        parent_ids = _require_parents(parents)
        if policy not in ("all", "any"):
            raise SourceLifecycleError("policy")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            if kind == "memory":
                if not isinstance(reference, MemoryCitation):
                    raise SourceLifecycleError("memory-citation")
                entry_id = await self._memory_binding(citation=reference)
                artifact_id = None
            else:
                if not isinstance(reference, ArtifactRef):
                    raise SourceLifecycleError("topic-reference")
                entry_id = None
                artifact_id = await self._topic_artifact_id(reference=reference)
            binding: tuple[object, ...] = (kind, reference.model_dump_json(), parent_ids, policy)
            async with self._contexts.database.transaction() as connection:
                nodes = {node.node_id: node for node in await load_nodes(connection, self.scope_id)}
                for parent_id in parent_ids:
                    if node_id == parent_id or parent_id not in nodes:
                        raise SourceLifecycleError("unknown-parent")
                existing = nodes.get(node_id)
                if existing is not None:
                    _require_same_binding(existing, binding)
                    return
                support = [nodes[parent_id].available for parent_id in parent_ids]
                available = all(support) if policy == "all" else any(support)
                await insert_node(
                    connection,
                    self.scope_id,
                    LifecycleNodeRow(
                        node_id=node_id,
                        ordinal=await next_ordinal(connection, self.scope_id),
                        kind=kind,
                        available=available,
                        policy=policy,
                        parents=parent_ids,
                        citation=reference.model_dump_json().encode("utf-8"),
                        entry_id=entry_id,
                        artifact_id=artifact_id,
                        revision=None,
                        sequence=None,
                    ),
                )
            if kind == "memory" and entry_id is not None:
                async with self._contexts.database.transaction() as connection:
                    current = await load_nodes(connection, self.scope_id)
                desired = _desired_entry_states({node.node_id: node.available for node in current}, current)
                await self._apply_entry_states(desired, reason=f"lifecycle registration of {node_id}")

    async def _propagate(self, node_id: str, sequence: int, reason: str, /) -> None:
        """Recompute derived availability and move native state to match it.

        Derived availability is always recomputed from the Source flags, so a
        propagation interrupted by a failed Memory write finishes on retry and
        a completed replay finds no state to change.
        """

        async with self._contexts.database.transaction() as connection:
            nodes = await load_nodes(connection, self.scope_id)
        availability = {node.node_id: node.available for node in nodes}
        for node in nodes:
            if node.kind == "source":
                continue
            support = [availability[parent] for parent in node.parents]
            availability[node.node_id] = all(support) if node.policy == "all" else any(support)
        changed = {
            node.node_id: availability[node.node_id] for node in nodes if availability[node.node_id] != node.available
        }
        if any(node.kind == "memory" for node in nodes):
            await self._apply_entry_states(_desired_entry_states(availability, nodes), reason=reason)
        if changed:
            async with self._contexts.database.transaction() as connection:
                await store_availability(connection, self.scope_id, changed)
        async with self._contexts.database.transaction() as connection:
            await mark_event_applied(connection, self.scope_id, node_id, sequence)

    async def _apply_entry_states(self, desired: dict[str, bool], /, *, reason: str) -> None:
        """Move native Memory entry states to the propagated availability.

        Entries without lifecycle nodes are untouched, and entries already in
        their desired state produce no write, so replays never create revisions.
        """

        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            service = context.artifacts.memory
            try:
                head = await service.head(context.artifacts.memory_artifact_id)
            except ArtifactNotFoundError:
                return
            states = {item.entry_id: item.state for item in head.content.manifest.entries}
            entries: tuple[MemoryEntryVersion, ...] = await service.entries(head)
            to_active = tuple(
                entry
                for entry in entries
                if desired.get(entry.entry_id) is True and states.get(entry.entry_id) == "inactive"
            )
            if to_active:
                head = await service.reactivate(head, entries=to_active, reason=reason)
                states = {item.entry_id: item.state for item in head.content.manifest.entries}
                entries = await service.entries(head)
            to_inactive = tuple(
                entry
                for entry in entries
                if desired.get(entry.entry_id) is False and states.get(entry.entry_id) == "active"
            )
            if to_inactive:
                await service.forget(head, entries=to_inactive, reason=reason)

    async def _memory_binding(self, *, citation: MemoryCitation) -> str:
        """Validate one readable Memory citation and return its entry id."""

        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            if citation.memory_ref.artifact_id != context.artifacts.memory_artifact_id:
                raise SourceLifecycleError("memory-citation-scope")
            try:
                await service.validate_citation(citation)
                head = await service.head(context.artifacts.memory_artifact_id)
            except (ArtifactNotFoundError, InvalidMemoryCitationError) as error:
                raise SourceLifecycleError("unknown-memory-citation") from error
            if not any(
                item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
                for item in head.content.manifest.entries
            ):
                raise SourceLifecycleError("unknown-memory-citation")
        return citation.entry_id

    async def _topic_artifact_id(self, *, reference: ArtifactRef) -> str:
        """Validate one readable Topic Memory revision and return its artifact id."""

        if reference.family != TopicMemory.family:
            raise SourceLifecycleError("topic-family")
        try:
            async with self._contexts.database.transaction() as connection:
                await self._contexts.repositories.artifacts.get(connection, self.scope_id, reference)
        except RepositoryNotFoundError:
            raise SourceLifecycleError("unknown-topic-reference") from None
        return reference.artifact_id

    @property
    def _contexts(self):
        return self._runtime._provider


def _desired_entry_states(
    availability: dict[str, bool],
    nodes: tuple[LifecycleNodeRow, ...],
    /,
) -> dict[str, bool]:
    """Merge memory node availability per entry: any supporting node keeps it active."""

    desired: dict[str, bool] = {}
    for node in nodes:
        if node.kind == "memory" and node.entry_id is not None:
            desired[node.entry_id] = desired.get(node.entry_id, False) or availability[node.node_id]
    return desired


def _require_same_binding(existing: LifecycleNodeRow, binding: tuple[object, ...], /) -> None:
    if existing.kind == "source":
        stored: tuple[object, ...] = ("source", existing.citation.decode("utf-8"), existing.revision)
    else:
        stored = (existing.kind, existing.citation.decode("utf-8"), existing.parents, existing.policy)
    if binding != stored:
        raise SourceLifecycleError("node-conflict")


def _require_node_id(node_id: str, /) -> str:
    if not isinstance(node_id, str) or not node_id or not node_id.strip() or len(node_id) > _MAX_NODE_ID_LENGTH:
        raise SourceLifecycleError("node-id")
    return node_id


def _require_parents(parents: tuple[str, ...], /) -> tuple[str, ...]:
    if isinstance(parents, str) or not isinstance(parents, (tuple, list)):
        raise SourceLifecycleError("parents")
    parent_ids = tuple(parents)
    if not parent_ids:
        raise SourceLifecycleError("parents-required")
    if len(set(parent_ids)) != len(parent_ids):
        raise SourceLifecycleError("parents-duplicate")
    for parent in parent_ids:
        _require_node_id(parent)
    return parent_ids


def _require_positive_int(value: int, name: str, /) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceLifecycleError(name)


def _decode_reference(kind: str, citation: bytes, /) -> SourceRef | MemoryCitation | ArtifactRef:
    model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}.get(kind)
    if model is None:
        raise SourceLifecycleError("node-kind")
    return model.model_validate_json(citation.decode("utf-8"))
