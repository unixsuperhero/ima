"""Session rules. Pure: feed it clock readings, it returns a verdict."""
from __future__ import annotations

from dataclasses import dataclass

IDLE_S = 3 * 60      # no activity this long -> task is unset
BREAK_S = 60         # a gap at least this long (but under IDLE_S) counts as a break
OVERRUN_S = 15 * 60  # active this long with zero breaks -> reset and split


@dataclass
class Session:
    started_at: float
    last_activity: float
    breaks: int = 0


def tick(s: Session, now: float, last_activity: float) -> str | None:
    """Update the session from the latest activity reading.

    Returns "idle", "overrun", or None.
    """
    if last_activity > s.last_activity:
        gap = last_activity - s.last_activity
        if BREAK_S <= gap < IDLE_S:
            s.breaks += 1
        s.last_activity = last_activity
    if now - s.last_activity >= IDLE_S:
        return "idle"
    if now - s.started_at >= OVERRUN_S and s.breaks == 0:
        return "overrun"
    return None


def fmt(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
