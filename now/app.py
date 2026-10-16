"""Textual TUI. Three screens: Picker, Now, Noise. Modal keys like vim.

Normal mode: single letters act. Insert mode: a focused Input takes text, Esc leaves it.
Every key anywhere counts as activity for the active task.
"""
from __future__ import annotations

import asyncio
import os
import random
import shlex
import subprocess
import time

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, Label, ListItem, ListView, RichLog, Static

from . import engine
from . import watch
from .notify import notify
from .store import Noise as NoiseItem
from .store import Store, Task


class ListScreen(Screen):
    """Shared j/k list movement for normal mode."""

    LIST_ID = "rows"

    def action_cursor_down(self) -> None:
        self.query_one(f"#{self.LIST_ID}", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(f"#{self.LIST_ID}", ListView).action_cursor_up()


class Picker(ListScreen):
    """Insert mode: type to filter, Enter activates the top match or creates the typed task.
    Esc enters normal mode: j/k move, Enter picks, i or / back to the filter."""

    BINDINGS = [
        Binding("escape", "normal", "Normal", show=False),
        Binding("i,slash", "insert", "Filter"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "random", "Deal one"),
        Binding("ctrl+r", "random", "Deal one", show=False),
        Binding("n", "noise", "Noise"),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.rows: list[Task] = []

    def compose(self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder="filter, or type a new task and press Enter", id="filter")
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_rows("")
        self.query_one("#filter", Input).focus()

    def refresh_rows(self, text: str) -> None:
        text = text.strip().lower()
        tasks = self.store.tasks()
        self.rows = [t for t in self.store.pickable() if text in t.title.lower()]
        lv = self.query_one("#rows", ListView)
        lv.clear()
        for t in self.rows:
            crumb = f"{tasks[t.parent].title} › " if t.parent and t.parent in tasks else ""
            kind = "? " if t.kind == "know" else ""
            lv.append(ListItem(Label(f"{kind}{crumb}{t.title}")))
        self.query_one("#count", Static).update(
            f"{len(self.rows)} pickable · {self.store.done_today()} done today"
            f" · {len(self.store.open_noise())} noise"
        )

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.store.touch()
        self.refresh_rows(event.value)

    @on(Input.Submitted, "#filter")
    def filter_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self.rows:
            self.pick(self.rows[0])
        elif text:
            kind = "know" if text.startswith("?") else "do"
            self.pick(self.store.add(text.lstrip("? ").strip(), kind=kind))

    @on(ListView.Selected, "#rows")
    def row_selected(self, event: ListView.Selected) -> None:
        if event.list_view.index is not None:
            self.pick(self.rows[event.list_view.index])

    def action_normal(self) -> None:
        lv = self.query_one("#rows", ListView)
        if lv.index is None and self.rows:
            lv.index = 0
        lv.focus()

    def action_insert(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_random(self) -> None:
        if self.rows:
            self.pick(random.choice(self.rows))

    def action_noise(self) -> None:
        self.app.switch_screen(Noise(self.store))

    def pick(self, task: Task) -> None:
        self.store.activate(task.id)
        self.app.switch_screen(Now(self.store, task))


class TextPrompt(ModalScreen[str | None]):
    def __init__(self, heading: str, question: str, placeholder: str = "", value: str = ""):
        super().__init__()
        self.heading, self.question, self.placeholder, self.value = heading, question, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt"):
            yield Label(self.heading)
            yield Label(self.question)
            yield Input(value=self.value, placeholder=self.placeholder, id="answer")

    def on_mount(self) -> None:
        self.query_one("#answer", Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def key_escape(self) -> None:
        self.dismiss(None)


class Noise(ListScreen):
    """Triage. j/k move, e or Enter extract into a task, x drop, o open the ref, Esc back."""

    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("e,enter", "extract", "Extract"),
        Binding("x", "drop", "Drop"),
        Binding("o", "open_ref", "Open"),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.rows: list[NoiseItem] = []

    def compose(self) -> ComposeResult:
        yield Static(id="count")
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_rows()
        self.query_one("#rows", ListView).focus()

    def refresh_rows(self) -> None:
        self.rows = self.store.open_noise()
        lv = self.query_one("#rows", ListView)
        index = lv.index or 0
        lv.clear()
        for n in self.rows:
            lv.append(ListItem(Label(f"[{n.source}] {n.body[:160]}".replace("\n", " "))))
        lv.index = min(index, len(self.rows) - 1) if self.rows else None
        self.query_one("#count", Static).update(f"{len(self.rows)} noise · e extract · x drop · o open · Esc back")

    def current(self) -> NoiseItem | None:
        lv = self.query_one("#rows", ListView)
        return self.rows[lv.index] if self.rows and lv.index is not None else None

    def action_back(self) -> None:
        self.app.switch_screen(Picker(self.store))

    def action_extract(self) -> None:
        n = self.current()
        if not n:
            return

        def after(title: str | None) -> None:
            if title:
                self.store.extract(n.id, title)
                self.refresh_rows()

        self.app.push_screen(TextPrompt(n.body[:200], "Task title:", value=n.body.split("\n")[0][:72]), after)

    def action_drop(self) -> None:
        n = self.current()
        if n:
            self.store.drop(n.id)
            self.refresh_rows()

    def action_open_ref(self) -> None:
        n = self.current()
        if n and n.ref:
            subprocess.Popen(["open", n.ref], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Now(Screen):
    """The active task. d done, s split, u put back, : command line, n noise.

    Command line (vim style):
      :!CMD        run a shell command in the task's cwd, output captured to the run log
      :ask PROMPT  send a prompt to the agent (NOW_AGENT_CMD, default `claude -p`)
      :add TITLE   add a child task
      :cd PATH     set the task's working directory
      :done :split :unset :noise :q
    """

    BINDINGS = [
        Binding("d", "done", "Done"),
        Binding("s", "split", "Split"),
        Binding("u", "unset", "Put back"),
        Binding("colon", "command", ":"),
        Binding("n", "noise", "Noise"),
        Binding("escape", "hide_command", "Esc", show=False),
    ]

    def __init__(self, store: Store, task: Task):
        super().__init__()
        self.store = store
        self.item = task
        t = time.time()
        self.session = engine.Session(started_at=t, last_activity=t)

    def compose(self) -> ComposeResult:
        yield Static(self.item.title, id="title")
        yield Static(id="clock")
        yield ListView(id="children")
        yield RichLog(id="log", wrap=True, markup=False)
        yield Input(placeholder=":!cmd  :ask prompt  :add title  :cd path  :done :split :unset :noise :q", id="cmd")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#cmd", Input).display = False
        self.query_one("#children", ListView).can_focus = False
        self.refresh_children()
        log = self.query_one("#log", RichLog)
        for r in self.store.runs(self.item.id)[-5:]:
            log.write(f"$ {r.text}  [{'running' if r.exit is None else f'exit {r.exit}'}]")
        self.set_interval(1, self.tick)
        self.tick()

    def refresh_children(self) -> None:
        lv = self.query_one("#children", ListView)
        lv.clear()
        for c in self.store.children(self.item.id):
            lv.append(ListItem(Label(f"[{'x' if c.state == 'done' else ' '}] {c.title}")))

    def cwd(self) -> str:
        return self.store.tasks()[self.item.id].cwd or os.getcwd()

    # -- timers -------------------------------------------------------------
    def tick(self) -> None:
        now = time.time()
        verdict = engine.tick(self.session, now, self.store.last_activity())
        s = self.session
        self.query_one("#clock", Static).update(
            f"active {engine.fmt(now - s.started_at)} / {engine.fmt(engine.OVERRUN_S)}"
            f"   idle {engine.fmt(now - s.last_activity)} / {engine.fmt(engine.IDLE_S)}"
            f"   breaks {s.breaks}   cwd {self.cwd()}"
        )
        if verdict == "idle":
            self.store.unset(self.item.id, "idle")
            notify("now: idle 3 min", f"put back: {self.item.title}")
            self.app.notify(f"Idle 3 min. Put back: {self.item.title}", severity="warning")
            self.app.switch_screen(Picker(self.store))
        elif verdict == "overrun":
            self.store.unset(self.item.id, "overrun")
            notify("now: 15 min, no break", f"split: {self.item.title}")
            self.action_split()

    # -- actions ------------------------------------------------------------
    def action_done(self) -> None:
        self.store.done(self.item.id)
        self.app.notify(f"Done: {self.item.title}")
        self.app.switch_screen(Picker(self.store))

    def action_unset(self) -> None:
        self.store.unset(self.item.id, "manual")
        self.app.switch_screen(Picker(self.store))

    def action_noise(self) -> None:
        self.app.switch_screen(Noise(self.store))

    def action_split(self) -> None:
        def after(piece: str | None) -> None:
            if piece:
                child = self.store.add(piece, parent=self.item.id)
                self.store.activate(child.id)
                self.app.switch_screen(Now(self.store, child))
            else:
                active = self.store.active()
                if active and active.id == self.item.id:
                    self.store.unset(self.item.id, "manual")
                self.app.switch_screen(Picker(self.store))

        self.app.push_screen(TextPrompt(f"15 minutes on: {self.item.title}",
                                        "Smallest piece you can finish in 5 minutes?",
                                        placeholder="leave empty to just put it back"), after)

    def action_command(self) -> None:
        cmd = self.query_one("#cmd", Input)
        cmd.display = True
        cmd.value = ""
        cmd.focus()

    def action_hide_command(self) -> None:
        cmd = self.query_one("#cmd", Input)
        cmd.display = False
        self.set_focus(None)

    @on(Input.Submitted, "#cmd")
    def command_submitted(self, event: Input.Submitted) -> None:
        self.action_hide_command()
        line = event.value.strip().lstrip(":")
        if not line:
            return
        word, _, rest = line.partition(" ")
        rest = rest.strip()
        if line.startswith("!"):
            self.run("command", line[1:].strip())
        elif word == "ask" and rest:
            self.run("prompt", rest)
        elif word == "add" and rest:
            self.store.add(rest, parent=self.item.id)
            self.refresh_children()
        elif word == "cd" and rest:
            path = os.path.abspath(os.path.expanduser(rest))
            if os.path.isdir(path):
                self.store.set_cwd(self.item.id, path)
            else:
                self.query_one("#log", RichLog).write(f"cd: not a directory: {path}")
        elif word in ("done", "split", "unset", "noise"):
            getattr(self, f"action_{word}")()
        elif word == "q":
            self.app.exit()
        else:
            self.query_one("#log", RichLog).write(f"unknown command: {line}")

    # -- runs ---------------------------------------------------------------
    def run(self, kind: str, text: str) -> None:
        if kind == "prompt":
            shell = f"{os.environ.get('NOW_AGENT_CMD', 'claude -p')} {shlex.quote(text)}"
        else:
            shell = text
        run = self.store.run_start(self.item.id, kind, text, self.cwd())
        self.query_one("#log", RichLog).write(f"$ {text}")
        # Owned by the app, not this screen: a run must outlive switching tasks.
        self.app.run_worker(self._execute(run.id, kind, text, shell), exclusive=False, group=f"run-{run.id}")

    async def _execute(self, run_id: str, kind: str, text: str, shell: str) -> None:
        log = self.query_one("#log", RichLog)
        started = time.time()
        chunks: list[str] = []
        with self.store.run_log(run_id).open("w", encoding="utf-8") as out:
            proc = await asyncio.create_subprocess_shell(
                shell, cwd=self.cwd(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "NOW_TASK": self.item.id, "NOW_RUN": run_id})
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                out.write(line)
                chunks.append(line)
                log.write(line.rstrip("\n"))
            code = await proc.wait()
        duration = int((time.time() - started) * 1000)
        tail = "".join(chunks)[-2000:]
        self.store.run_finish(run_id, code, duration, tail)
        log.write(f"[exit {code} in {engine.fmt(duration / 1000)}]")
        if kind == "prompt":
            active = self.store.active()
            if not active or active.id != self.item.id:
                self.store.capture(tail or "(empty reply)", source="agent.response",
                                   ref=str(self.store.run_log(run_id)), key=f"run:{run_id}", task=self.item.id)
                notify("now: agent replied", text[:80])


class NowApp(App):
    CSS = """
    #title { padding: 2 4; text-style: bold; content-align: center middle; height: 7; }
    #clock { padding: 0 4; color: $text-muted; }
    #children { margin: 1 4; height: auto; max-height: 8; }
    #log { margin: 0 4; height: 1fr; border: round $surface-lighten-2; }
    #cmd { dock: bottom; margin: 0 0 1 0; }
    #count { padding: 1 2; color: $text-muted; }
    #prompt { padding: 2 4; border: thick $warning; background: $surface; width: 80%; height: auto; margin: 4 8; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, store: Store | None = None):
        super().__init__()
        self.store = store or Store()

    def on_key(self) -> None:
        self.store.touch()

    def on_mount(self) -> None:
        active = self.store.active()
        self.push_screen(Now(self.store, active) if active else Picker(self.store))
        session = os.environ.get("TUIOS_SESSION")
        if os.environ.get("TUIOS_ENV") == "1" and session:
            self.run_worker(self._watch(session, os.environ.get("TUIOS_PANE_ID")), group="watch")

    async def _watch(self, session: str, own_window: str | None) -> None:
        """Other panes in the session count as activity; their agents' states become noise."""
        proc = await asyncio.create_subprocess_exec(*watch.subscribe_cmd(session), stdout=asyncio.subprocess.PIPE)
        assert proc.stdout
        async for raw in proc.stdout:
            event = watch.parse(raw.decode(errors="replace"))
            if event and watch.handle(self.store, event, own_window) == "noise":
                screen = self.screen
                if isinstance(screen, Picker):
                    screen.refresh_rows(screen.query_one("#filter", Input).value)
                elif isinstance(screen, Noise):
                    screen.refresh_rows()


def run() -> None:
    NowApp().run()
