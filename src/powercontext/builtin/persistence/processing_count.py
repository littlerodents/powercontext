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

"""Count-threshold admission for Source-driven Artifact processing.

Pending work is counted per Scope and per processing Family from the Source
cursor of that Family's last successful commit through the current journal
end. Journal identity is the persisted Source row, so replayed captures and
receipt trust upgrades never add count, while lineage-only server Sources do.
Reaching the threshold accepts one ordinary processing invocation through the
existing requested-intent channel; it never bypasses windows or flushes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE


@dataclass(frozen=True, slots=True)
class SourceCountThresholds:
    """Per-binding pending-count admission thresholds; empty means disabled."""

    thresholds: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for binding_name, threshold in self.thresholds.items():
            if type(threshold) is not int or threshold < 1:
                raise ValueError("count thresholds must be strict positive integers")  # noqa: TRY003
            if not binding_name or binding_name != binding_name.strip():
                raise ValueError("count threshold binding names must be nonempty and trimmed")  # noqa: TRY003

    def __bool__(self) -> bool:
        return bool(self.thresholds)

    def binding_enabled(self, binding_name: str, /) -> bool:
        return binding_name in self.thresholds

    async def admit(self, connection: AsyncConnection, scope_id: str, position: int, /) -> tuple[str, ...]:
        """Accept one invocation for every idle binding whose pending count reached its threshold.

        Runs inside the caller's transaction. Returns the binding names that
        received a new request generation, so the caller can wake a supervisor
        after the transaction commits.
        """

        if not self.thresholds or position < 0:
            return ()
        intents = ArtifactProcessingIntentRepository()
        cursors = SourceCursorRepository()
        accepted: list[str] = []
        for binding_name, threshold in self.thresholds.items():
            cursor = await cursors.load(connection, scope_id, binding_name)
            consumed = 0 if cursor is None else cursor.cursor.sequence
            if position - consumed < threshold:
                continue
            intent = await intents.load(connection, scope_id, binding_name, for_update=True)
            if intent is not None and intent.requested_generation != intent.handled_generation:
                # An accepted invocation is still pending or running; its own
                # success re-checks the remaining count without a new request.
                continue
            await intents.request(connection, scope_id, binding_name)
            accepted.append(binding_name)
        return tuple(accepted)

    async def admit_at_head(self, connection: AsyncConnection, scope_id: str, /) -> tuple[str, ...]:
        """Admit against the journal head, locking it like every other reader."""

        if not self.thresholds:
            return ()
        # Freeze the head the same way other journal readers do before deciding.
        await connection.execute(
            update(SOURCE_JOURNAL_HEADS_TABLE)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
            .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
        )
        head = await connection.scalar(
            select(func.max(SOURCE_JOURNAL_HEADS_TABLE.c.position)).where(
                SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id
            )
        )
        return await self.admit(connection, scope_id, int(head or 0))


__all__ = ["SourceCountThresholds"]
