"""Throwaway "learn about X for Y" runs.

The agent answers with one line per item, `title | why`. Each becomes a motivation
locked for NOW_LEARN_UNLOCK_DAYS (default 7). The run itself is attributed to the
task you were on when curiosity struck, or to none from the CLI.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime, timedelta, timezone

from .store import Motivation, Store

PROMPT = (
    "I am deliberately NOT going to work on this now. Give me a list of things that would "
    "be motivating to build, read, or try later about: {topic}\n"
    "Answer with 3 to 8 lines, nothing else, each line exactly: title | one sentence why it is worth it"
)


def unlock_at(days: float | None = None) -> str:
    days = float(os.environ.get("NOW_LEARN_UNLOCK_DAYS", "7")) if days is None else days
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


def parse(text: str) -> list[tuple[str, str]]:
    items = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if "|" not in line:
            continue
        title, _, why = line.partition("|")
        if title.strip():
            items.append((title.strip(), why.strip()))
    return items


def agent_shell(topic: str) -> str:
    return f"{os.environ.get('NOW_AGENT_CMD', 'claude -p')} {shlex.quote(PROMPT.format(topic=topic))}"


def record(store: Store, text: str, run: str | None = None) -> list[Motivation]:
    when = unlock_at()
    return [store.motivate(title, why, when, run) for title, why in parse(text)]


def learn(store: Store, topic: str, cwd: str | None = None) -> list[Motivation]:
    """Blocking CLI path: run the agent and record its answer."""
    out = subprocess.run(agent_shell(topic), shell=True, capture_output=True, text=True, cwd=cwd).stdout
    return record(store, out)
