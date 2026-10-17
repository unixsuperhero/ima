"""Turn tuios events into activity and noise.

`tuios subscribe` streams JSON lines for the session. A prompt or command in any
pane is activity for the active task. An agent in another pane reaching done,
needs_input, or errored becomes noise so it waits in the funnel instead of
interrupting. NOW_SUBSCRIBE_CMD overrides the command (tests feed a file).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess

from . import agents
from .notify import notify
from .store import Store

ACTIVITY = {"prompt", "command-started", "command-finished"}
ATTENTION = {"done", "needs_input", "errored"}
TYPES = "prompt,command-started,command-finished,agent-state"


def subscribe_cmd(session: str) -> list[str]:
    override = os.environ.get("NOW_SUBSCRIBE_CMD")
    if override:
        return shlex.split(override)
    return ["tuios", "subscribe", "-s", session, "--types", TYPES]


def handle(store: Store, event: dict, own_window: str | None = None) -> str | None:
    """Apply one event. Returns "touch", "noise", or None."""
    kind = event.get("type")
    if kind in ACTIVITY:
        store.touch()
        return "touch"
    if kind == "agent-state":
        window = event.get("window") or "?"
        if window == own_window:
            return None
        state = event.get("state")
        if not isinstance(state, str):
            return None
        owned = agents.observe(store, event.get("session", ""), window, state,
                               server=os.environ.get("TUIOS_SERVER") or None)
        message = event.get("message") or ""
        if owned is not None:
            if owned:
                notify(f"now: agent {state}", message or window[:8])
                return "noise"
            return None
        if state not in ATTENTION:
            return None
        body = f"agent {state} in pane {window[:8]}" + (f": {message}" if message else "")
        if store.capture(body, source=f"agent.{state}", ref=f"tuios://{event.get('session', '')}/{window}",
                         key=f"tuios:{window}:{event.get('seq')}"):
            notify(f"now: agent {state}", message or window[:8])
            return "noise"
    return None


def parse(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def watch(store: Store, session: str, own_window: str | None = None) -> int:
    """Blocking: read the stream until it ends. Returns how many events did something."""
    count = 0
    with subprocess.Popen(subscribe_cmd(session), stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout
        for line in proc.stdout:
            event = parse(line)
            if event and handle(store, event, own_window):
                count += 1
    return count
