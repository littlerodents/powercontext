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

"""Cross-call embedding request coalescing with a bounded LRU vector cache.

One wrapper instance shared by concurrent callers in one event loop turns
duplicate texts into one underlying provider batch. Successful vectors feed a
bounded LRU cache; failures are never cached and the next caller retries.
Cancellation of one caller never cancels the underlying batch that other
callers still await, and every caller keeps its own usage summary of the
distinct batches it actually waited for.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.inference.protocols import EmbeddingModel

_ZERO_USAGE = InferenceUsage(requests=0)


def _add_usage(left: InferenceUsage, right: InferenceUsage) -> InferenceUsage:
    """Sum two provider usage reports without turning absent counters into zero claims."""

    def sum_optional(first: int | None, second: int | None) -> int | None:
        if first is None and second is None:
            return None
        return (first or 0) + (second or 0)

    return InferenceUsage(
        requests=left.requests + right.requests,
        input_tokens=sum_optional(left.input_tokens, right.input_tokens),
        output_tokens=sum_optional(left.output_tokens, right.output_tokens),
    )


@dataclass(slots=True)
class _Batch:
    """One underlying provider request shared by every waiter of its texts."""

    texts: tuple[str, ...]
    waiters: list[asyncio.Future[_Batch]] = field(default_factory=list)
    vectors: dict[str, tuple[float, ...]] | None = None
    usage: InferenceUsage = field(default_factory=lambda: _ZERO_USAGE)
    error: BaseException | None = None


class CoalescingEmbeddingModel:
    """Merge concurrent embedding calls and cache successful vectors.

    The wrapper satisfies the existing ``EmbeddingModel`` protocol: it exposes
    the wrapped model's ``profile`` and returns the existing ``EmbeddingResult``.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must be non-negative")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        # Flights not yet sent to the provider; they still absorb late texts.
        self._pending: OrderedDict[str, _Batch] = OrderedDict()
        # Flights whose provider request is running; late callers attach here.
        self._inflight: dict[str, _Batch] = {}
        self._drain_scheduled = False
        # Strong references keep dispatched work alive until it completes; the
        # event loop otherwise only holds weak task references.
        self._background: set[asyncio.Task[None]] = set()

    def _spawn(self, coroutine, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        texts = tuple(texts)
        if not texts:
            return EmbeddingResult(vectors=(), usage=_ZERO_USAGE)
        loop = asyncio.get_running_loop()
        vectors: dict[str, tuple[float, ...]] = {}
        usage = _ZERO_USAGE
        charged: dict[int, None] = {}
        waits: list[tuple[str, asyncio.Future[_Batch]]] = []
        for text in dict.fromkeys(texts):
            cached = self._cache_get(text)
            if cached is not None:
                vectors[text] = cached
                continue
            batch = self._pending.get(text) or self._inflight.get(text)
            if batch is None:
                batch = _Batch(texts=(text,))
                self._pending[text] = batch
            waiter = loop.create_future()
            batch.waiters.append(waiter)
            waits.append((text, waiter))
        self._schedule_drain()
        if waits:
            # Every waiter belongs to this caller only, so cancelling this call
            # leaves the shared batch and every other waiter untouched.
            await asyncio.gather(*(waiter for _, waiter in waits))
            for text, waiter in waits:
                batch = waiter.result()
                if batch.error is not None:
                    raise batch.error
                if batch.vectors is None:  # pragma: no cover - resolved before waking
                    raise RuntimeError("coalesced embedding batch resolved without vectors")  # noqa: TRY003
                vectors[text] = batch.vectors[text]
                if id(batch) not in charged:
                    # A caller that waited on the same batch through several of
                    # its texts summarizes that batch's usage exactly once.
                    charged[id(batch)] = None
                    usage = _add_usage(usage, batch.usage)
        return EmbeddingResult(vectors=tuple(vectors[text] for text in texts), usage=usage)

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is not None:
            # A read refreshes recency, so a hot text survives capacity pressure.
            self._cache.move_to_end(text)
        return vector

    def _schedule_drain(self) -> None:
        if self._drain_scheduled or not self._pending:
            return
        self._drain_scheduled = True
        self._spawn(self._drain(), "powercontext-embedding-coalesce-drain")

    async def _drain(self) -> None:
        if self._coalesce_seconds > 0:
            # One merge window absorbs callers that are about to submit the same
            # texts; callers that arrive during the request share its result.
            await asyncio.sleep(self._coalesce_seconds)
        # Reset before grouping so texts enqueued afterwards schedule their own
        # dispatch round instead of waiting for this one.
        self._drain_scheduled = False
        snapshot = list(self._pending.items())
        self._pending.clear()
        for start in range(0, len(snapshot), self._max_batch_size):
            members = snapshot[start : start + self._max_batch_size]
            batch = _Batch(
                texts=tuple(text for text, _ in members),
                waiters=[waiter for _, single in members for waiter in single.waiters],
            )
            for text, _ in members:
                self._inflight[text] = batch
            self._spawn(self._run_batch(batch), "powercontext-embedding-coalesce-batch")

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._model.embed(batch.texts)
            vectors = tuple(tuple(vector) for vector in result.vectors)
            if len(vectors) != len(batch.texts):
                raise ValueError(  # noqa: TRY003, TRY301 - caller-visible contract
                    "embedding provider returned the wrong vector count for the batch"
                )
            batch.vectors = dict(zip(batch.texts, vectors, strict=True))
            batch.usage = result.usage
            if self._cache_size > 0:
                for text, vector in batch.vectors.items():
                    self._cache[text] = vector
                    self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        except BaseException as error:
            # Failed results are never cached; the next caller retries.
            batch.error = error
        finally:
            for text in batch.texts:
                if self._inflight.get(text) is batch:
                    del self._inflight[text]
            for waiter in batch.waiters:
                if waiter.done():
                    # A cancelled caller's future stays behind; the shared batch
                    # still resolves for every remaining waiter.
                    continue
                waiter.set_result(batch)
            batch.waiters.clear()


__all__ = ["CoalescingEmbeddingModel"]
