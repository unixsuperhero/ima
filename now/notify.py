"""Get the person's attention outside the TUI pane.

Writes an OSC 9 notification to the controlling terminal, which tuios surfaces as
a notification event, and on macOS also posts a system notification. NOW_NOTIFY=0
disables both.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def notify(title: str, body: str = "") -> None:
    if os.environ.get("NOW_NOTIFY") == "0":
        return
    message = f"{title}: {body}" if body else title
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(f"\x1b]9;{message}\x07")
            tty.flush()
    except OSError:
        pass
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
