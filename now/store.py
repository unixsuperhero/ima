"""Append-only JSONL event log; task state is derived by replay.

Events (one JSON object per line):
  {"at": iso, "type": "task.add",      "id": str, "title": str, "parent": str|None}
  {"at": iso, "type": "task.activate", "id": str}
  {"at": iso, "type": "task.unset",    "id": str, "reason": "idle"|"overrun"|"manual"}
  {"at": iso, "type": "task.done",     "id": str}
  {"at": iso, "type": "task.context",  "id": str, "cwd": str}
  {"at": iso, "type": "noise.add",     "id": str, "key": str, "source": str, "ref": str|None, "body": str, "task": str|None}
  {"at": iso, "type": "noise.done",    "id": str, "task": str|None}
  {"at": iso, "type": "run.start",     "id": str, "task": str, "kind": "command"|"prompt", "text": str, "cwd": str}
  {"at": iso, "type": "run.finish",    "id": str, "exit": int, "duration_ms": int, "tail": str}

A run is a command or an agent prompt started from inside the app, always
attributed to a task. Full output lives in runs/<id>.log; `tail` is the end.

Noise is raw input from a funnel source. `key` dedups re-polled items.
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
    from_noise: str | None = None
    kind: str = "do"  # do | know
    cwd: str | None = None


@dataclass
class Noise:
    id: str
    key: str
    source: str
    ref: str | None
    body: str
    created_at: str
    state: str = "open"  # open | done
    task: str | None = None


@dataclass
class Run:
    id: str
    task: str
    kind: str
    text: str
    cwd: str
    started_at: str
    exit: int | None = None
    duration_ms: int | None = None
    tail: str = ""


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
                tasks[e["id"]] = Task(e["id"], e["title"], e.get("parent"), e["at"],
                                      from_noise=e.get("from"), kind=e.get("kind", "do"))
                continue
            task = tasks.get(e["id"])
            if task is None:
                continue
            if t == "task.context":
                task.cwd = e["cwd"]
            elif t == "task.activate":
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

    def noise(self) -> dict[str, Noise]:
        items: dict[str, Noise] = {}
        for e in self.events():
            if e["type"] == "noise.add":
                items[e["id"]] = Noise(e["id"], e["key"], e["source"], e.get("ref"), e["body"], e["at"],
                                       task=e.get("task"))
            elif e["type"] == "noise.done" and e["id"] in items:
                items[e["id"]].state = "done"
                items[e["id"]].task = e.get("task") or items[e["id"]].task
        return items

    def runs(self, task_id: str | None = None) -> list[Run]:
        runs: dict[str, Run] = {}
        for e in self.events():
            if e["type"] == "run.start":
                runs[e["id"]] = Run(e["id"], e["task"], e["kind"], e["text"], e["cwd"], e["at"])
            elif e["type"] == "run.finish" and e["id"] in runs:
                r = runs[e["id"]]
                r.exit, r.duration_ms, r.tail = e["exit"], e["duration_ms"], e.get("tail", "")
        return [r for r in runs.values() if task_id is None or r.task == task_id]

    def open_noise(self) -> list[Noise]:
        return sorted((n for n in self.noise().values() if n.state == "open"),
                      key=lambda n: n.created_at)

    # -- commands ---------------------------------------------------------
    def add(self, title: str, parent: str | None = None, from_noise: str | None = None,
            kind: str = "do") -> Task:
        task_id = secrets.token_hex(4)
        e = self.append({"type": "task.add", "id": task_id, "title": title, "parent": parent,
                         "from": from_noise, "kind": kind})
        return Task(task_id, title, parent, e["at"], from_noise=from_noise, kind=kind)

    def set_cwd(self, task_id: str, cwd: str) -> None:
        self.append({"type": "task.context", "id": task_id, "cwd": cwd})

    def capture(self, body: str, source: str = "cli", ref: str | None = None,
                key: str | None = None, task: str | None = None) -> Noise | None:
        """Append a noise item. Returns None if `key` was already captured."""
        key = key or f"{source}:{secrets.token_hex(4)}"
        if any(n.key == key for n in self.noise().values()):
            return None
        noise_id = secrets.token_hex(4)
        e = self.append({"type": "noise.add", "id": noise_id, "key": key, "source": source,
                         "ref": ref, "body": body, "task": task})
        return Noise(noise_id, key, source, ref, body, e["at"], task=task)

    def run_start(self, task_id: str, kind: str, text: str, cwd: str) -> Run:
        run_id = secrets.token_hex(4)
        e = self.append({"type": "run.start", "id": run_id, "task": task_id, "kind": kind,
                         "text": text, "cwd": cwd})
        (self.root / "runs").mkdir(exist_ok=True)
        return Run(run_id, task_id, kind, text, cwd, e["at"])

    def run_log(self, run_id: str) -> Path:
        return self.root / "runs" / f"{run_id}.log"

    def run_finish(self, run_id: str, exit_code: int, duration_ms: int, tail: str) -> None:
        self.append({"type": "run.finish", "id": run_id, "exit": exit_code,
                     "duration_ms": duration_ms, "tail": tail[-2000:]})

    def extract(self, noise_id: str, title: str) -> Task:
        task = self.add(title, from_noise=noise_id)
        self.append({"type": "noise.done", "id": noise_id, "task": task.id})
        return task

    def drop(self, noise_id: str) -> None:
        self.append({"type": "noise.done", "id": noise_id, "task": None})

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
