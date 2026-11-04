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

"""HTTP scoring-service reranker that selects Memory hits across batches."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANKER_POLICY_ID = "http-cross-encoder-v1"
_RESPONSE_SCHEMA_ERROR = "rerank response must contain exactly the documented schema"


class HTTPMemoryReranker:
    """Score candidates with an offline HTTP service and select a global top-``limit``."""

    def __init__(
        self,
        endpoint: str,
        /,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANKER_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("rerank endpoint must be a non-empty string")  # noqa: TRY003
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("rerank policy_id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Send every candidate once across bounded batches and select globally."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = tuple(enumerate(candidates[start : start + self._batch_size], start=start + 1))
            for rank, score in (await self._score_batch(query, batch)).items():
                if rank in scores:
                    raise ValueError(_RESPONSE_SCHEMA_ERROR)
                scores[rank] = score
            requests += 1
        selected_ranks = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=requests),
        )

    async def _score_batch(
        self,
        query: str,
        batch: tuple[tuple[int, MemoryHit], ...],
        /,
    ) -> dict[int, float]:
        """POST one batch of globally ranked candidates and validate the response."""

        ranks = dict(batch)
        payload = {
            "query": query,
            "candidates": [{"rank": rank, "text": hit.text} for rank, hit in batch],
        }
        response = await self._client.post(self._endpoint, json=payload)
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as error:
            raise ValueError("rerank response must be valid JSON") from error  # noqa: TRY003
        if not isinstance(body, dict) or set(body) != {"scores"}:
            raise ValueError(_RESPONSE_SCHEMA_ERROR)

        raw_scores = body["scores"]
        if not isinstance(raw_scores, list) or len(raw_scores) != len(ranks):
            raise ValueError(_RESPONSE_SCHEMA_ERROR)
        scores: dict[int, float] = {}
        for record in raw_scores:
            if not isinstance(record, dict) or set(record) != {"rank", "score"}:
                raise ValueError(_RESPONSE_SCHEMA_ERROR)
            rank = record["rank"]
            if type(rank) is not int or rank not in ranks or rank in scores:
                raise ValueError(_RESPONSE_SCHEMA_ERROR)
            score = _validated_score(record["score"])
            scores[rank] = score
        return scores


def _validated_score(value: Any, /) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # The HTTP contract classifies schema violations as ValueError, not TypeError.
        raise ValueError(_RESPONSE_SCHEMA_ERROR)  # noqa: TRY004
    score = float(value)
    if not math.isfinite(score):
        raise ValueError(_RESPONSE_SCHEMA_ERROR)
    return score


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["DEFAULT_HTTP_RERANKER_POLICY_ID", "HTTPMemoryReranker"]
