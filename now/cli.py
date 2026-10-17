"""`now` opens the TUI. Subcommands add tasks, capture noise, extract, and poll."""
from __future__ import annotations

import argparse
import json
import sys
import time

from .store import Store


def draft_command(args: argparse.Namespace) -> int:
    from .drafts import Drafts

    try:
        store = Store()
        drafts = Drafts(store)
        if args.draft_cmd == "read":
            result = drafts.read(args.cwd)
        else:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("stdin must contain a JSON object")
            required = ("draft_id", "revision", "text" if args.draft_cmd == "save" else "indent_size")
            missing = [key for key in required if key not in payload]
            if missing:
                raise ValueError("Missing JSON fields: " + ", ".join(missing))
            if args.draft_cmd == "save":
                result = drafts.save(payload["draft_id"], payload["revision"], payload["text"])
            else:
                result = drafts.apply(payload["draft_id"], payload["revision"], payload["indent_size"])
        if args.draft_cmd != "save":
            store.touch()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"now draft {args.draft_cmd}: {exc}", file=sys.stderr)
        return 1



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
    learn = sub.add_parser("learn", help="throwaway 'learn about X for Y' run; output becomes locked motivations")
    learn.add_argument("topic", nargs="+")
    sub.add_parser("later", help="list motivations; locked ones show their unlock date")
    find = sub.add_parser("find", help="search every collection")
    find.add_argument("query", nargs="*")
    sub.add_parser("review", help="what got done, by day")

    draft = sub.add_parser("draft", help="read, save, or apply the durable task-entry draft")
    draft_sub = draft.add_subparsers(dest="draft_cmd", required=True)
    draft_read = draft_sub.add_parser("read", help="open the pending draft as JSON")
    draft_read.add_argument("--cwd", required=True, help="absolute working directory for a new draft")
    draft_sub.add_parser("save", help="save JSON from stdin without recording activity")
    draft_sub.add_parser("apply", help="apply the saved outline using JSON from stdin")


    args = parser.parse_args(argv)
    if args.cmd == "draft":
        return draft_command(args)
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
    elif args.cmd == "learn":
        from .learn import learn
        for m in learn(store, " ".join(args.topic)):
            print(m.id, "locked until", m.unlock_at[:10], "·", m.title)
    elif args.cmd == "later":
        for m in store.motivations():
            lock = f"locked until {m.unlock_at[:10]}" if m.locked() else "unlocked"
            print(m.id, f"[{lock}]", m.title, "·", m.why)
    elif args.cmd == "find":
        for kind, item_id, label in store.search(" ".join(args.query)):
            print(f"{kind:5} {item_id} {label}")
    elif args.cmd == "review":
        from .store import local_time
        for day, tasks in store.done_by_day().items():
            print(f"{day}  {len(tasks)} done")
            for t in tasks:
                print(f"  {local_time(t.done_at)}  {t.title}")
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
