"""`now` opens the TUI. Subcommands add tasks, list them, or record activity."""
from __future__ import annotations

import argparse
import sys

from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="now", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    add = sub.add_parser("add", help="add a task without opening the TUI")
    add.add_argument("title", nargs="+")
    sub.add_parser("ls", help="list pickable tasks")
    sub.add_parser("touch", help="record activity (call from shell or editor hooks)")
    args = parser.parse_args(argv)

    store = Store()
    if args.cmd == "add":
        t = store.add(" ".join(args.title))
        print(t.id, t.title)
    elif args.cmd == "ls":
        active = store.active()
        for t in store.pickable():
            print(("*" if active and t.id == active.id else " "), t.id, t.title)
    elif args.cmd == "touch":
        store.touch()
    else:
        from .app import run
        run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
