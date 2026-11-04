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

"""Prefix-reuse Context Pack protocol for incremental context transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
_DIGEST_PREFIX = "sha256:"
_PACK_FIELDS = {"schema", "items", "digest", "byte_count"}
_DELTA_FIELDS = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}


class ContextPackError(Exception):
    """Raised for protocol, chunking, or checksum violations in pack transport."""


def _canonical_json(payload: dict[str, object], /) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_digest(items: tuple[bytes, ...], /) -> str:
    unsigned = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    return f"{_DIGEST_PREFIX}{digest}"


def _base64_items(raw_items: object, /) -> tuple[bytes, ...]:
    if not isinstance(raw_items, list):
        raise ContextPackError("items must be an array of standard Base64 strings")  # noqa: TRY003
    items: list[bytes] = []
    for value in raw_items:
        if not isinstance(value, str):
            raise ContextPackError("items must contain only strings")  # noqa: TRY003
        try:
            items.append(base64.b64decode(value, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("items must be standard Base64 strings") from error  # noqa: TRY003
    return tuple(items)


def _validated_digest(value: object, /) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError("digest must use the sha256:<hex> form")  # noqa: TRY003
    hex_part = value[len(_DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(character not in "0123456789abcdef" for character in hex_part):
        raise ContextPackError("digest must contain 64 lowercase hexadecimal characters")  # noqa: TRY003
    return value


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete context: ordered chunks plus self-describing checksum fields."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Build a verified pack from an ordered tuple of byte chunks."""

        validated = _validated_items(items)
        return cls(
            items=validated,
            digest=_pack_digest(validated),
            byte_count=_serialized_byte_count(validated, _pack_digest(validated)),
        )

    @classmethod
    def from_content(cls, content: str, /, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Chunk ``content`` deterministically into bounded, valid UTF-8 pieces."""

        if not isinstance(content, str):
            raise ContextPackError("content must be a string")  # noqa: TRY003
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        chunks: list[bytes] = []
        current = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("no valid chunk boundary exists within chunk_bytes")  # noqa: TRY003
            if len(current) + len(encoded) > chunk_bytes:
                chunks.append(bytes(current))
                current = bytearray()
            current.extend(encoded)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPack:
        """Decode and fully verify a serialized pack payload."""

        payload = _decoded_wire_object(value, _PACK_FIELDS)
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("pack schema does not match the protocol version")  # noqa: TRY003
        items = _base64_items(payload["items"])
        digest = _validated_digest(payload["digest"])
        byte_count = payload["byte_count"]
        if type(byte_count) is not int:
            raise ContextPackError("byte_count must be an integer")  # noqa: TRY003
        if digest != _pack_digest(items):
            raise ContextPackError("pack digest does not match its items")  # noqa: TRY003
        if byte_count != len(value):
            raise ContextPackError("byte_count does not match the serialized pack length")  # noqa: TRY003
        return cls(items=items, digest=digest, byte_count=byte_count)

    def to_bytes(self, /) -> bytes:
        """Serialize the pack with its self-reported byte count."""

        return _canonical_json(_pack_payload(self.items, self.digest, self.byte_count))


def _validated_items(items: tuple[bytes, ...], /) -> tuple[bytes, ...]:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("items must be a tuple of bytes")  # noqa: TRY003
    return items


def _pack_payload(items: tuple[bytes, ...], digest: str, byte_count: int, /) -> dict[str, object]:
    return {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
        "digest": digest,
        "byte_count": byte_count,
    }


def _serialized_byte_count(items: tuple[bytes, ...], digest: str, /) -> int:
    byte_count = 0
    for _ in range(16):
        payload = _pack_payload(items, digest, byte_count)
        length = len(_canonical_json(payload))
        if length == byte_count:
            return byte_count
        byte_count = length
    raise ContextPackError("byte_count failed to converge")  # noqa: TRY003


def _decoded_wire_object(value: bytes, expected_fields: set[str], /) -> dict[str, object]:
    if not isinstance(value, bytes):
        raise ContextPackError("wire payloads must be bytes")  # noqa: TRY003
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("wire payload must be UTF-8 encoded JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ContextPackError("wire payload fields do not match the protocol schema")  # noqa: TRY003
    return payload


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Reuse a verified prefix of ``base`` and append the target's remaining chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Select the longest shared item prefix; any valid prefix remains decodable."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires two ContextPack values")  # noqa: TRY003
        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPackDelta:
        """Decode and validate a serialized delta payload."""

        payload = _decoded_wire_object(value, _DELTA_FIELDS)
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("delta schema does not match the protocol version")  # noqa: TRY003
        base_digest = _validated_digest(payload["base_digest"])
        target_digest = _validated_digest(payload["target_digest"])
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_base64_items(payload["appended"]),
        )

    def to_bytes(self, /) -> bytes:
        """Serialize the delta; the byte count stays an in-memory property."""

        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        }
        return _canonical_json(payload)

    @property
    def byte_count(self, /) -> int:
        """Exact serialized wire size of this delta."""

        return len(self.to_bytes())


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Verify ``delta`` against ``base`` and rebuild the exact target pack."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("apply requires a ContextPack base and a ContextPackDelta")  # noqa: TRY003
    if type(delta.prefix_items) is not int or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("prefix_items is out of bounds for the base pack")  # noqa: TRY003
    if not isinstance(delta.appended, tuple) or any(not isinstance(item, bytes) for item in delta.appended):
        raise ContextPackError("appended must be a tuple of bytes")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("base pack digest does not match the delta")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack digest does not match the target digest")  # noqa: TRY003
    return target


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
