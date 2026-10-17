"""One durable pending outline; committed batches are their own retry receipts."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from .store import Store, atomic_write, now_iso, sync_directory


def validate_identity(draft_id: str, revision: int) -> None:
    if not isinstance(draft_id, str) or not draft_id:
        raise ValueError("draft_id must be a nonempty string")
    if type(revision) is not int or revision < 0:
        raise ValueError("revision must be a nonnegative integer")


def parse_outline(text: str, cwd: str, indent_size: int = 2) -> list[dict]:
    if type(indent_size) is not int or indent_size <= 0:
        raise ValueError("indent_size must be a positive integer")
    tasks = []
    ancestors = []
    for number, line in enumerate(text.splitlines(), 1):
        if "\t" in line:
            raise ValueError(f"Line {number}: tabs are not supported; use spaces")
        if not line.strip():
            continue
        spaces = len(line) - len(line.lstrip(" "))
        depth, remainder = divmod(spaces, indent_size)
        if remainder or depth > len(ancestors):
            raise ValueError(f"Line {number}: invalid nesting; use {indent_size} spaces per level")
        title = line.strip()
        if title.lower().startswith("- [x]"):
            raise ValueError(f"Line {number}: checked tasks are not supported")
        if title == "- [ ]" or title.startswith("- [ ] "):
            title = title[5:].strip()
        elif title == "-" or title.startswith("- "):
            title = title[1:].strip()
        kind = "do"
        if title == "?" or title.startswith("? "):
            kind, title = "know", title[1:].strip()
        if not title:
            raise ValueError(f"Line {number}: task title is empty")
        ancestors = ancestors[:depth]
        task = {"id": secrets.token_hex(4), "title": title,
                "parent": ancestors[-1] if ancestors else None, "kind": kind, "cwd": cwd}
        tasks.append(task)
        ancestors.append(task["id"])
    if not tasks:
        raise ValueError("Draft is empty; enter at least one task before applying")
    return tasks


class Drafts:
    def __init__(self, store: Store):
        self.store = store
        self.path = store.root / "draft.json"

    def _load(self) -> dict | None:
        try:
            draft = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt draft in {self.path}; preserve the file before repairing it") from exc
        if not isinstance(draft, dict):
            raise ValueError(f"Invalid draft in {self.path}; preserve the file before repairing it")
        validate_identity(draft.get("draft_id"), draft.get("revision"))
        if not isinstance(draft.get("text"), str):
            raise ValueError(f"Invalid draft text in {self.path}")
        cwd = draft.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError(f"Invalid draft cwd in {self.path}")
        return draft

    def _write(self, draft: dict) -> dict:
        atomic_write(self.path, json.dumps(draft, ensure_ascii=False) + "\n")
        return draft

    @staticmethod
    def _receipt(events: list[dict], draft_id: str, revision: int) -> dict | None:
        event_id = f"{draft_id}:{revision}"
        for event in events:
            if event["type"] == "draft.apply" and event["id"] == event_id:
                return {"draft_id": draft_id, "revision": revision,
                        "task_ids": [task["id"] for task in event["tasks"]]}
        return None

    def read(self, cwd: str) -> dict:
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError("cwd must be an absolute path")
        with self.store.locked():
            draft = self._load()
            if draft is not None:
                events = self.store._events_unlocked()
                if self._receipt(events, draft["draft_id"], draft["revision"]) is None:
                    return draft
                # A prior apply may have failed after rename but before directory fsync.
                sync_directory(self.store.root)
            return self._write({"draft_id": secrets.token_hex(16), "revision": 0,
                                "text": "", "cwd": cwd})

    def _current(self, draft_id: str, revision: int) -> dict:
        draft = self._load()
        if draft is None or (draft["draft_id"], draft["revision"]) != (draft_id, revision):
            raise ValueError("Draft conflict: saved draft changed; read it again before saving or applying")
        return draft

    def save(self, draft_id: str, revision: int, text: str) -> dict:
        validate_identity(draft_id, revision)
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        with self.store.locked():
            events = self.store._events_unlocked()
            if self._receipt(events, draft_id, revision) is not None:
                raise ValueError("Draft revision already applied; read a fresh draft before saving")
            draft = self._current(draft_id, revision)
            if draft["text"] == text:
                sync_directory(self.store.root)
                return draft
            return self._write({**draft, "revision": revision + 1, "text": text})

    def apply(self, draft_id: str, revision: int, indent_size: int = 2) -> dict:
        validate_identity(draft_id, revision)
        with self.store.locked():
            events = self.store._events_unlocked()
            receipt = self._receipt(events, draft_id, revision)
            if receipt is not None:
                sync_directory(self.store.root)
                return receipt
            draft = self._current(draft_id, revision)
            tasks = parse_outline(draft["text"], draft["cwd"], indent_size)
            event = {"at": now_iso(), "type": "draft.apply", "id": f"{draft_id}:{revision}",
                     "tasks": tasks}
            # ponytail: rewriting the local log buys atomic batches; revisit for large logs.
            atomic_write(self.store.events_path,
                         "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in [*events, event]))
            self.path.unlink()
            sync_directory(self.store.root)
            return {"draft_id": draft_id, "revision": revision,
                    "task_ids": [task["id"] for task in tasks]}
