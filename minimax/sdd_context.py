#!/usr/bin/env python3
"""Persistent SDD context CLI backed by real PowerContext Memory and seekdb.

A project alias is an idempotency key, never a fabricated scope ID. Each process
resolves its actual scope, queries the native database, and closes it on exit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.errors import ArtifactNotFoundError


async def head_or_none(memory):
    try:
        return await memory.head("memory")
    except ArtifactNotFoundError:
        return None


async def put(memory, key: str, kind: str, body: str):
    head = await head_or_none(memory)
    prefix = f"SDD_DOCUMENT {key}\n"
    entries = () if head is None else await memory.entries(head)
    previous = next((entry for entry in entries if entry.text.startswith(prefix)), None)
    text = (prefix + body).strip()
    if previous is not None and previous.text == text:
        return {"unchanged": True, "entry_id": previous.entry_id, "entry_version_id": previous.entry_version_id}
    committed = await memory.remember(
        memory=head,
        entries=(MemoryEntryInput(kind=kind, text=text, entry=previous),),
        mode="append",
    )
    current = next(entry for entry in await memory.entries(committed) if entry.text == text)
    return {
        "memory_ref": committed.as_ref().model_dump(mode="json"),
        "entry_id": current.entry_id,
        "entry_version_id": current.entry_version_id,
        "version": current.version,
    }


async def execute(args):
    config = BuiltinConfig(database=SeekDBConfig(path=Path(args.database).resolve()))
    async with open_builtin_contexts(config) as contexts:
        scope = await contexts.scopes.create(
            ScopeDraft(title="SDD " + args.project, summary="Versioned engineering context and task state", idempotency_key=args.project)
        )
        memory = (await contexts.get(scope.scope_id)).artifacts.memory
        if args.command == "import":
            path = Path(args.file).resolve()
            content = path.read_text(encoding="utf-8")
            key = args.identity or path.name
            result = await put(
                memory, key, args.kind,
                f"PATH {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path.name}\n"
                f"SHA256 {hashlib.sha256(content.encode()).hexdigest()}\n{content}",
            )
            return {"scope_id": scope.scope_id, "document": key, **result}
        head = await head_or_none(memory)
        if args.command == "prepare":
            if head is None:
                return {"scope_id": scope.scope_id, "task": args.task, "items": []}
            result = await memory.search(args.task, memories=(head,), mode="fts", limit=20)
            kinds = {entry.entry_id: entry.kind for entry in await memory.entries(head)}
            items = [
                {
                    "kind": kinds[hit.entry_id],
                    "citation": {
                        "scope_id": scope.scope_id,
                        "memory_ref": hit.memory_ref.model_dump(mode="json"),
                        "entry_id": hit.entry_id,
                        "entry_version_id": hit.entry_version_id,
                    },
                    "content": hit.text,
                    "matched_by": hit.matched_by,
                }
                for hit in result.hits if kinds[hit.entry_id] != "task-state"
            ]
            state = await load_state(memory, head)
            return {"scope_id": scope.scope_id, "task": args.task, "items": items, "saved_state": state}
        if args.command == "save-state":
            state = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not all(key in state for key in ("objective", "completed", "pending", "checks")):
                raise ValueError("state requires objective, completed, pending, and checks")
            return {"scope_id": scope.scope_id, **await put(memory, "task-state", "task-state", json.dumps(state, ensure_ascii=False, sort_keys=True))}
        return {"scope_id": scope.scope_id, "saved_state": await load_state(memory, head)}


async def load_state(memory, head):
    if head is None:
        return None
    entries = await memory.entries(head)
    state = next((entry for entry in entries if entry.kind == "task-state"), None)
    if state is None:
        return None
    return json.loads(state.text.split("\n", 1)[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(Path(__file__).parent / ".state" / "seekdb"))
    parser.add_argument("--project", default="shop-sdd")
    commands = parser.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("import")
    acquire.add_argument("--file", required=True)
    acquire.add_argument("--kind", choices=("spec", "code", "test"), required=True)
    acquire.add_argument("--identity", help="Stable document identity, including across specification revisions")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--task", required=True)
    state = commands.add_parser("save-state")
    state.add_argument("--file", required=True)
    commands.add_parser("resume")
    print(json.dumps(asyncio.run(execute(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
