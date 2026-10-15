"""Textual TUI: a picker screen and a Now screen. Nothing else."""
from __future__ import annotations

import random
import time

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from . import engine
from .store import Store, Task


class Picker(Screen):
    """Type to filter. Enter activates the top match, or creates the typed task."""

    BINDINGS = [
        Binding("ctrl+r", "random", "Deal one"),
        Binding("ctrl+q", "app.quit", "Quit"),
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
            lv.append(ListItem(Label(f"{crumb}{t.title}")))
        self.query_one("#count", Static).update(
            f"{len(self.rows)} pickable · {self.store.done_today()} done today"
            f" · {len(self.store.open_noise())} noise"
        )

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.refresh_rows(event.value)

    @on(Input.Submitted, "#filter")
    def filter_submitted(self, event: Input.Submitted) -> None:
        if self.rows:
            self.pick(self.rows[0])
        elif event.value.strip():
            self.pick(self.store.add(event.value.strip()))

    @on(ListView.Selected, "#rows")
    def row_selected(self, event: ListView.Selected) -> None:
        if event.list_view.index is not None:
            self.pick(self.rows[event.list_view.index])

    def action_random(self) -> None:
        if self.rows:
            self.pick(random.choice(self.rows))

    def pick(self, task: Task) -> None:
        self.store.activate(task.id)
        self.app.switch_screen(Now(self.store, task))


class SplitPrompt(ModalScreen[str | None]):
    def __init__(self, parent_title: str):
        super().__init__()
        self.parent_title = parent_title

    def compose(self) -> ComposeResult:
        with Vertical(id="split"):
            yield Label(f"15 minutes on: {self.parent_title}")
            yield Label("Smallest piece you can finish in 5 minutes?")
            yield Input(placeholder="leave empty to just put it back", id="piece")

    def on_mount(self) -> None:
        self.query_one("#piece", Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class Now(Screen):
    BINDINGS = [
        Binding("d", "done", "Done"),
        Binding("s", "split", "Split"),
        Binding("u", "unset", "Put back"),
        Binding("ctrl+q", "app.quit", "Quit"),
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
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_children()
        self.set_interval(1, self.tick)
        self.tick()

    def refresh_children(self) -> None:
        lv = self.query_one("#children", ListView)
        lv.clear()
        for c in self.store.children(self.item.id):
            mark = "x" if c.state == "done" else " "
            lv.append(ListItem(Label(f"[{mark}] {c.title}")))
        lv.can_focus = False

    def on_key(self) -> None:
        self.store.touch()

    def tick(self) -> None:
        now = time.time()
        verdict = engine.tick(self.session, now, self.store.last_activity())
        s = self.session
        self.query_one("#clock", Static).update(
            f"active {engine.fmt(now - s.started_at)} / {engine.fmt(engine.OVERRUN_S)}"
            f"   idle {engine.fmt(now - s.last_activity)} / {engine.fmt(engine.IDLE_S)}"
            f"   breaks {s.breaks}"
        )
        if verdict == "idle":
            self.store.unset(self.item.id, "idle")
            self.app.notify(f"Idle 3 min. Put back: {self.item.title}", severity="warning")
            self.app.switch_screen(Picker(self.store))
        elif verdict == "overrun":
            self.store.unset(self.item.id, "overrun")
            self.action_split()

    def action_done(self) -> None:
        self.store.done(self.item.id)
        self.app.notify(f"Done: {self.item.title}")
        self.app.switch_screen(Picker(self.store))

    def action_unset(self) -> None:
        self.store.unset(self.item.id, "manual")
        self.app.switch_screen(Picker(self.store))

    def action_split(self) -> None:
        def after(piece: str | None) -> None:
            if piece:
                child = self.store.add(piece, parent=self.item.id)
                self.store.activate(child.id)
                self.app.switch_screen(Now(self.store, child))
            else:
                if self.store.active() and self.store.active().id == self.item.id:
                    self.store.unset(self.item.id, "manual")
                self.app.switch_screen(Picker(self.store))

        self.app.push_screen(SplitPrompt(self.item.title), after)


class NowApp(App):
    CSS = """
    #title { padding: 2 4; text-style: bold; content-align: center middle; height: 7; }
    #clock { padding: 0 4; color: $text-muted; }
    #children { margin: 1 4; }
    #count { padding: 1 2; color: $text-muted; }
    #split { padding: 2 4; border: thick $warning; background: $surface; width: 70%; height: auto; margin: 4 8; }
    """

    def __init__(self, store: Store | None = None):
        super().__init__()
        self.store = store or Store()

    def on_mount(self) -> None:
        active = self.store.active()
        if active:
            self.push_screen(Now(self.store, active))
        else:
            self.push_screen(Picker(self.store))


def run() -> None:
    NowApp().run()
