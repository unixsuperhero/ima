"""Append-only JSONL event log; task state is derived by replay.

Events (one JSON object per line):
  {"at": iso, "type": "task.add",      "id": str, "title": str, "parent": str|None}
  {"at": iso, "type": "task.activate", "id": str}
  {"at": iso, "type": "task.unset",    "id": str, "reason": "idle"|"overrun"|"manual"}
  {"at": iso, "type": "task.done",     "id": str}
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir() -> Path:
    home = os.environ.get("NOW_HOME")
    if home:
        return Path(home)
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "now"


@dataclass
class Task:
    id: str
    title: str
    parent: str | None
    created_at: str
    state: str = "open"  # open | active | done
    activated_at: str | None = None
    done_at: str | None = None


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.activity_path = self.root / "activity"

    # -- events -----------------------------------------------------------
    def append(self, event: dict) -> dict:
        event = {"at": now_iso(), **event}
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        with self.events_path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # -- derived state ----------------------------------------------------
    def tasks(self) -> dict[str, Task]:
        tasks: dict[str, Task] = {}
        for e in self.events():
            t = e["type"]
            if t == "task.add":
                tasks[e["id"]] = Task(e["id"], e["title"], e.get("parent"), e["at"])
                continue
            task = tasks.get(e["id"])
            if task is None:
                continue
            if t == "task.activate":
                for other in tasks.values():
                    if other.state == "active":
                        other.state = "open"
                task.state = "active"
                task.activated_at = e["at"]
            elif t == "task.unset":
                if task.state == "active":
                    task.state = "open"
            elif t == "task.done":
                task.state = "done"
                task.done_at = e["at"]
        return tasks

    def active(self) -> Task | None:
        return next((t for t in self.tasks().values() if t.state == "active"), None)

    def children(self, task_id: str, tasks: dict[str, Task] | None = None) -> list[Task]:
        tasks = tasks or self.tasks()
        return [t for t in tasks.values() if t.parent == task_id]

    def pickable(self) -> list[Task]:
        """Open leaves: open tasks with no open children. Oldest first."""
        tasks = self.tasks()
        out = []
        for t in tasks.values():
            if t.state == "done":
                continue
            if any(c.state != "done" for c in self.children(t.id, tasks)):
                continue
            out.append(t)
        out.sort(key=lambda t: t.created_at)
        return out

    def done_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return sum(1 for t in self.tasks().values() if t.done_at and t.done_at.startswith(today))

    # -- commands ---------------------------------------------------------
    def add(self, title: str, parent: str | None = None) -> Task:
        task_id = secrets.token_hex(4)
        e = self.append({"type": "task.add", "id": task_id, "title": title, "parent": parent})
        return Task(task_id, title, parent, e["at"])

    def activate(self, task_id: str) -> None:
        self.append({"type": "task.activate", "id": task_id})
        self.touch()

    def unset(self, task_id: str, reason: str) -> None:
        self.append({"type": "task.unset", "id": task_id, "reason": reason})

    def done(self, task_id: str) -> None:
        self.append({"type": "task.done", "id": task_id})

    # -- activity ---------------------------------------------------------
    def touch(self) -> None:
        self.activity_path.touch()
        os.utime(self.activity_path, None)

    def last_activity(self) -> float:
        try:
            return self.activity_path.stat().st_mtime
        except FileNotFoundError:
            return time.time()
