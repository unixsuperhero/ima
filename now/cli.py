"""`now` opens the TUI. Subcommands add tasks, capture noise, extract, and poll."""
from __future__ import annotations

import argparse
import sys
import time

from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="now", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    add = sub.add_parser("add", help="add a task without opening the TUI")
    add.add_argument("title", nargs="+")
    add.add_argument("--know", action="store_true", help="a to-know task instead of a to-do")
    sub.add_parser("ls", help="list pickable tasks")
    sub.add_parser("touch", help="record activity (call from shell or editor hooks)")

    cap = sub.add_parser("capture", help="capture raw noise; text from args or stdin")
    cap.add_argument("-s", "--source", default="cli")
    cap.add_argument("-r", "--ref", help="url or locator to link back to")
    cap.add_argument("text", nargs="*")
    sub.add_parser("noise", help="list open noise")
    ext = sub.add_parser("extract", help="turn a noise item into a task")
    ext.add_argument("noise_id")
    ext.add_argument("title", nargs="+")
    drop = sub.add_parser("drop", help="discard a noise item")
    drop.add_argument("noise_id", nargs="+")

    poll = sub.add_parser("poll", help="pull github/slack inboxes into noise")
    poll.add_argument("sources", nargs="*", default=["github", "slack"], choices=["github", "slack"])
    poll.add_argument("--loop", type=int, metavar="SECONDS", help="repeat forever at this interval")
    sub.add_parser("watch", help="follow tuios events: panes count as activity, agent states become noise")

    args = parser.parse_args(argv)
    store = Store()
    store.touch()  # any now command is activity for the active task

    if args.cmd == "add":
        t = store.add(" ".join(args.title), kind="know" if args.know else "do")
        print(t.id, t.title)
    elif args.cmd == "ls":
        active = store.active()
        for t in store.pickable():
            print(("*" if active and t.id == active.id else " "), t.id, ("?" if t.kind == "know" else " "), t.title)
    elif args.cmd == "touch":
        pass
    elif args.cmd == "capture":
        body = " ".join(args.text) if args.text else sys.stdin.read().strip()
        if not body:
            parser.error("nothing to capture")
        n = store.capture(body, source=args.source, ref=args.ref)
        print(n.id, n.source, n.body[:80])
    elif args.cmd == "noise":
        for n in store.open_noise():
            print(n.id, f"[{n.source}]", n.body[:100].replace("\n", " "), n.ref or "")
    elif args.cmd == "extract":
        t = store.extract(args.noise_id, " ".join(args.title))
        print(t.id, t.title)
    elif args.cmd == "drop":
        for nid in args.noise_id:
            store.drop(nid)
    elif args.cmd == "poll":
        from .poll import run
        while True:
            for name, count in run(store, args.sources).items():
                print(time.strftime("%H:%M:%S"), name, count)
            if not args.loop:
                break
            time.sleep(args.loop)
    elif args.cmd == "watch":
        from .watch import watch
        import os
        session = os.environ.get("TUIOS_SESSION")
        if not session:
            parser.error("not inside tuios (TUIOS_SESSION is unset)")
        watch(store, session, os.environ.get("TUIOS_PANE_ID"))
    else:
        from .app import run as tui
        tui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
