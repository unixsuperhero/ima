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
from textual.command import CommandPalette
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, RichLog, Static, TextArea

from . import agents, engine
from . import learn, watch
from .notify import notify
from .store import Noise as NoiseItem
from .store import Motivation, Run, Store, Task, local_time


class NavigationCommandPalette(CommandPalette):
    BINDINGS = [
        Binding("ctrl+n", "cursor_down", "Down", show=False),
        Binding("ctrl+p", "command_list('cursor_up')", "Up", show=False),
    ]


class ListScreen(Screen):
    """Shared list movement, including while a filter input has focus."""

    LIST_ID = "rows"
    BINDINGS = [
        Binding("ctrl+n", "cursor_down", "Down", show=False),
        Binding("ctrl+p", "cursor_up", "Up", show=False),
    ]

    def action_cursor_down(self) -> None:
        self.query_one(f"#{self.LIST_ID}", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(f"#{self.LIST_ID}", ListView).action_cursor_up()


class Picker(ListScreen):
    """Insert mode: type to filter, Enter activates the highlighted match or creates the typed task.
    Esc enters normal mode: j/k move, Enter picks, i or / back to the filter."""

    BINDINGS = [
        Binding("escape", "normal", "Normal", show=False),
        Binding("i,slash", "insert", "Filter"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "random", "Deal one"),
        Binding("ctrl+r", "random", "Deal one", show=False),
        Binding("n", "noise", "Noise"),
        Binding("l", "later", "Later"),
        Binding("v", "review", "Review"),
        Binding("g", "agents", "Agents"),
        Binding("f", "app.find", "Find"),
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
        lv.index = 0 if self.rows else None
        self.query_one("#count", Static).update(
            f"{len(self.rows)} pickable · {self.store.done_today()} done today"
            f" · {len(self.store.open_noise())} noise · {len(self.store.motivations())} later"
        )

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.store.touch()
        self.refresh_rows(event.value)

    @on(Input.Submitted, "#filter")
    def filter_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self.rows:
            index = self.query_one("#rows", ListView).index
            self.pick(self.rows[index if index is not None else 0])
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

    def action_later(self) -> None:
        self.app.switch_screen(Later(self.store))

    def action_review(self) -> None:
        self.app.switch_screen(Review(self.store))
    def action_agents(self) -> None:
        self.app.push_screen(Agents(self.store))

    def on_screen_resume(self) -> None:
        self.app.theme = "textual-dark"

    def on_mount(self) -> None:
        self.app.theme = "textual-dark"
        self.refresh_rows("")
        self.query_one("#filter", Input).focus()

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



class AgentLauncher(ModalScreen[Run | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+enter", "launch", "Launch", priority=True),
    ]

    def __init__(self, store: Store, task: Task, cwd: str):
        super().__init__()
        self.store, self.task_id = store, task.id
        self.task_title, self.cwd = task.title, cwd
        self.launching = False

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-launcher"):
            yield Label(f"Persistent agent · {self.task_title}", markup=False)
            yield Static(f"Working directory: {self.cwd}", markup=False)
            yield Label("Command (quoted arguments allowed; not a shell expression)")
            yield Input(os.environ.get("NOW_AGENT_SESSION_CMD", "claude"), id="agent-command")
            yield Label("Prompt · Enter: newline · Ctrl+Enter: launch")
            yield TextArea(id="agent-prompt")
            yield Static("Runs in tuios after Now exits. :ask is one-shot and owned by Now.", id="agent-error", markup=False)
            with Horizontal(classes="agent-buttons"):
                yield Button("Launch", id="agent-launch", variant="primary")
                yield Button("Cancel", id="agent-cancel")

    def on_mount(self) -> None:
        self.query_one("#agent-prompt", TextArea).focus()

    @on(Button.Pressed, "#agent-launch")
    def action_launch(self) -> None:
        if self.launching:
            return
        self.launching = True
        command = self.query_one("#agent-command", Input).value
        prompt = self.query_one("#agent-prompt", TextArea).text
        self.query_one("#agent-launch", Button).disabled = True
        self.query_one("#agent-error", Static).update("Starting native pane…")
        self.app.run_worker(self._launch(command, prompt), group="agent-launch", exclusive=False)

    async def _launch(self, command: str, prompt: str) -> None:
        try:
            run = await asyncio.to_thread(agents.launch, self.store, self.task_id, command, prompt, self.cwd)
        except Exception as error:
            if self.is_mounted:
                self.query_one("#agent-error", Static).update(str(error))
                self.query_one("#agent-launch", Button).disabled = False
                self.launching = False
            return
        self.app.notify(f"Agent {run.agent_state or 'starting'} · g: agents · Enter: details · o: open pane")
        if self.is_mounted and self.app.screen is self:
            self.dismiss(run)

    @on(Button.Pressed, "#agent-cancel")
    def action_cancel(self) -> None:
        if self.launching:
            self.app.notify("Launch already submitted; inspect it with g → Enter.")
        self.dismiss(None)


class Agents(ListScreen):
    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, store: Store, task_id: str | None = None):
        super().__init__()
        self.store, self.task_id = store, task_id
        self.rows: list[Run] = []
        self.labels: dict[str, str] = {}
        self.polling = False

    def compose(self) -> ComposeResult:
        yield Static("Loading agents…", id="count", markup=False)
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#rows", ListView).focus()
        self.set_interval(2, self.poll)
        self.poll()

    def on_screen_resume(self) -> None:
        self.poll()

    def poll(self) -> None:
        if not self.polling and self.app.screen is self:
            self.polling = True
            self.run_worker(self._refresh(), group="agent-poll")

    async def _refresh(self) -> None:
        try:
            runs = await asyncio.to_thread(agents.refresh, self.store, self.task_id)
            tasks = await asyncio.to_thread(self.store.tasks)
            if self.app.screen is not self:
                return
            lv = self.query_one("#rows", ListView)
            selected = self.rows[lv.index].id if self.rows and lv.index is not None else None
            scroll = lv.scroll_offset
            known = {r.id for r in self.rows}
            incoming = {r.id for r in runs}
            for r in self.rows:
                if r.id not in incoming:
                    await self.query_one(f"#agent-{r.id}", ListItem).remove()
                    self.labels.pop(r.id, None)
            for run in runs:
                title = tasks[run.task].title if run.task in tasks else run.task
                label = (f"[{run.agent_state or 'unknown'}] {run.agent_command or ''}"
                         f" · {title} · {run.text}").replace("\n", " ")
                if run.id not in known:
                    await lv.append(ListItem(Label(label, markup=False), id=f"agent-{run.id}"))
                elif self.labels.get(run.id) != label:
                    self.query_one(f"#agent-{run.id} Label", Label).update(label)
                self.labels[run.id] = label
            # Existing rows retain their widgets and order; new runs append.
            by_id = {r.id: r for r in runs}
            self.rows = [by_id[item.id.removeprefix("agent-")] for item in lv.children]
            ids = [r.id for r in self.rows]
            index = ids.index(selected) if selected in ids else (0 if ids else None)
            if lv.index != index:
                lv.index = index
            lv.scroll_to(scroll.x, scroll.y, animate=False, force=True)
            scope = tasks[self.task_id].title if self.task_id in tasks else "All tasks"
            heading = f"{scope} · {len(runs)} persistent agents · Enter: details · Esc: back"
            count = self.query_one("#count", Static)
            if str(count.content) != heading:
                count.update(heading)
        except Exception as error:
            if self.is_mounted and self.app.screen is self:
                self.query_one("#count", Static).update(f"Agents unavailable: {error}")
        finally:
            self.polling = False

    @on(ListView.Selected, "#rows")
    def selected(self, event: ListView.Selected) -> None:
        if event.list_view.index is not None and self.rows:
            self.app.push_screen(AgentDetail(self.store, self.rows[event.list_view.index]))

    def action_back(self) -> None:
        self.app.pop_screen()


class StopAgent(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, run: Run):
        super().__init__()
        self.run = run

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-stop-dialog"):
            yield Static(f"Stop agent {self.run.id}?\nCloses only its recorded tuios pane. Unsaved work in that pane may be lost.", markup=False)
            with Horizontal(classes="agent-buttons"):
                yield Button("Cancel", id="stop-cancel")
                yield Button("Stop agent", id="stop-confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#stop-cancel", Button).focus()

    @on(Button.Pressed, "#stop-confirm")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#stop-cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class AgentDetail(Screen):
    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("o", "open", "Open native pane"),
        Binding("x", "stop", "Stop agent"),
    ]

    def __init__(self, store: Store, run: Run):
        super().__init__()
        self.store, self.run = store, run
        self.polling = False
        self.operating = False
        self.output: str | None = None
        self.heading: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="agent-status", markup=False)
        with VerticalScroll(id="agent-context"):
            yield Static(f"Command: {self.run.agent_command}\nWorking directory: {self.run.cwd}\nOriginal prompt:\n{self.run.text}", markup=False)
        yield Static("Live pane / last saved snapshot. Approvals and follow-up prompts belong in the native pane.", classes="agent-hint")
        with VerticalScroll(id="agent-transcript"):
            yield Static("Loading transcript…", id="agent-output", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#agent-transcript", VerticalScroll).focus()
        self.set_interval(2, self.poll)
        self.poll()

    def on_screen_resume(self) -> None:
        self.poll()

    def poll(self) -> None:
        if not self.polling and not self.operating and self.app.screen is self:
            self.polling = True
            self.run_worker(self._refresh(), group="agent-detail-poll")

    async def _refresh(self) -> None:
        try:
            runs = await asyncio.to_thread(agents.refresh, self.store, self.run.task)
            run = next((r for r in runs if r.id == self.run.id), self.run)
            output = await asyncio.to_thread(agents.transcript, self.store, run.id)
            tasks = await asyncio.to_thread(self.store.tasks)
            if self.app.screen is not self:
                return
            self.run = run
            title = tasks[run.task].title if run.task in tasks else run.task
            heading = (f"{title} · agent {run.id} · {run.agent_state or 'unknown'}"
                       f" · prompt: {run.prompt_status or 'unknown'}"
                       f"\nPane: {run.agent_session}/{run.agent_window} · o: open · x: stop")
            if heading != self.heading:
                self.query_one("#agent-status", Static).update(heading)
                self.heading = heading
            if output != self.output:
                transcript = self.query_one("#agent-transcript", VerticalScroll)
                scroll = transcript.scroll_offset
                self.query_one("#agent-output", Static).update(output or "(No transcript captured yet.)")
                transcript.scroll_to(scroll.x, scroll.y, animate=False, force=True)
                self.output = output
        except Exception as error:
            if self.is_mounted and self.app.screen is self:
                self.query_one("#agent-status", Static).update(f"Agent unavailable: {error}")
                self.heading = None
        finally:
            self.polling = False

    def action_open(self) -> None:
        if not self.operating:
            self.operating = True
            self.run_worker(self._operate(False), group="agent-operation")

    def action_stop(self) -> None:
        if not self.operating:
            self.app.push_screen(StopAgent(self.run), self._confirm_stop)

    def _confirm_stop(self, confirmed: bool) -> None:
        if confirmed:
            self.operating = True
            self.run_worker(self._operate(True), group="agent-operation")

    async def _operate(self, stop: bool) -> None:
        try:
            await asyncio.to_thread(agents.stop if stop else agents.open_pane, self.store, self.run.id)
            self.app.notify("Agent stopped" if stop else "Opened native agent pane")
        except Exception as error:
            self.app.notify(str(error), severity="error")
        finally:
            self.operating = False
            if self.is_mounted:
                self.poll()

    def action_back(self) -> None:
        self.app.pop_screen()


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
            if n.ref.startswith("run:"):
                self.app.selected(("run", n.ref.split()[0].removeprefix("run:")))
            else:
                subprocess.Popen(["open", n.ref], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Later(ListScreen):
    """Motivations from learn runs. Locked rows cannot be promoted. p promote, x dismiss, Esc back."""

    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("p,enter", "promote", "Promote"),
        Binding("x", "dismiss_item", "Dismiss"),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.rows: list[Motivation] = []

    def compose(self) -> ComposeResult:
        yield Static(id="count")
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_rows()
        self.query_one("#rows", ListView).focus()

    def refresh_rows(self) -> None:
        self.rows = self.store.motivations()
        lv = self.query_one("#rows", ListView)
        index = lv.index or 0
        lv.clear()
        for m in self.rows:
            lock = f"🔒 {m.unlock_at[:10]}" if m.locked() else "open"
            lv.append(ListItem(Label(f"[{lock}] {m.title} · {m.why}")))
        lv.index = min(index, len(self.rows) - 1) if self.rows else None
        locked = sum(1 for m in self.rows if m.locked())
        self.query_one("#count", Static).update(
            f"{len(self.rows)} later · {locked} locked · p promote · x dismiss · Esc back")

    def current(self) -> Motivation | None:
        lv = self.query_one("#rows", ListView)
        return self.rows[lv.index] if self.rows and lv.index is not None else None

    def action_back(self) -> None:
        self.app.switch_screen(Picker(self.store))

    def action_promote(self) -> None:
        m = self.current()
        if not m:
            return
        if m.locked():
            self.app.notify(f"Locked until {m.unlock_at[:10]}. Not now.", severity="warning")
            return
        self.store.promote(m.id)
        self.refresh_rows()

    def action_dismiss_item(self) -> None:
        m = self.current()
        if m:
            self.store.dismiss(m.id)
            self.refresh_rows()


class Review(ListScreen):
    """Read only. What got finished, by day. j/k move, Esc back."""

    BINDINGS = [
        Binding("escape,q", "back", "Back"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store

    def compose(self) -> ComposeResult:
        yield Static(id="count")
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        days = self.store.done_by_day()
        total = sum(len(v) for v in days.values())
        lv = self.query_one("#rows", ListView)
        for day, tasks in days.items():
            lv.append(ListItem(Label(f"{day}  ·  {len(tasks)} done"), classes="day"))
            for t in tasks:
                runs = len(self.store.runs(t.id))
                extra = f"  · {runs} run{'s' if runs != 1 else ''}" if runs else ""
                lv.append(ListItem(Label(f"    {local_time(t.done_at)}  {t.title}{extra}")))
        self.query_one("#count", Static).update(
            f"{self.store.done_today()} done today · {total} all time · {len(days)} days · Esc back")
        lv.index = 0 if days else None
        lv.focus()

    def action_back(self) -> None:
        self.app.switch_screen(Picker(self.store))


class Selector(ModalScreen[tuple[str, str] | None]):
    """One search box over every collection. Enter picks the highlighted row, Esc cancels."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("down,ctrl+n", "cursor_down", "Down", show=False),
        Binding("up,ctrl+p", "cursor_up", "Up", show=False),
    ]

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.rows: list[tuple[str, str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="find"):
            yield Input(placeholder="find anything: tasks, noise, later, runs, done", id="query")
            yield ListView(id="matches")
            yield Static(id="hint")

    def on_mount(self) -> None:
        self.refresh_rows("")
        self.query_one("#query", Input).focus()

    def refresh_rows(self, text: str) -> None:
        self.rows = self.store.search(text)
        lv = self.query_one("#matches", ListView)
        lv.clear()
        for kind, _, label in self.rows[:200]:
            lv.append(ListItem(Label(f"{kind:5} {label}")))
        lv.index = 0 if self.rows else None
        self.query_one("#hint", Static).update(
            f"{len(self.rows)} matches · Enter: task→go, noise→extract, later→promote, run→details/log, done→show")

    @on(Input.Changed, "#query")
    def changed(self, event: Input.Changed) -> None:
        self.refresh_rows(event.value)

    @on(Input.Submitted, "#query")
    def submitted(self) -> None:
        lv = self.query_one("#matches", ListView)
        self.choose(lv.index if lv.index is not None else 0)

    @on(ListView.Selected, "#matches")
    def selected(self, event: ListView.Selected) -> None:
        self.choose(event.list_view.index)

    def action_cursor_down(self) -> None:
        self.query_one("#matches", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#matches", ListView).action_cursor_up()

    def choose(self, index: int | None) -> None:
        if index is not None and index < len(self.rows):
            kind, item_id, _ = self.rows[index]
            self.dismiss((kind, item_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


def task_theme(task_id: str) -> Theme:
    """The whole app takes the task's color: a hue derived from its id."""
    hue = int(task_id[:6], 16) % 360
    import colorsys
    r, g, b = colorsys.hls_to_rgb(hue / 360, 0.55, 0.65)
    primary = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    return Theme(name=f"task-{task_id}", primary=primary, dark=True)


class Now(Screen):
    """The active task. d done, s split, u put back, : command line, n noise.

    Command line (vim style):
      :!CMD        run a shell command in the task's cwd, output captured to the run log
      :ask PROMPT  one-shot prompt owned by Now (NOW_AGENT_CMD, default `claude -p`)
      :learn TOPIC throwaway run; the answer becomes locked motivations for later
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
        Binding("a", "launch_agent", "Launch agent"),
        Binding("g", "agents", "Agents"),
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
        theme = task_theme(self.item.id)
        self.app.register_theme(theme)
        self.app.theme = theme.name
        self.query_one("#cmd", Input).display = False
        self.query_one("#children", ListView).can_focus = False
        self.refresh_children()
        log = self.query_one("#log", RichLog)
        for r in self.store.runs(self.item.id)[-5:]:
            state = r.agent_state or "unknown" if r.kind == "agent" else ("running" if r.exit is None else f"exit {r.exit}")
            log.write(f"$ {r.text}  [{state}]")
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
        if self.app.screen is not self:
            return
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

    def action_launch_agent(self) -> None:
        def launched(run: Run | None) -> None:
            if run and self.is_mounted:
                self.query_one("#log", RichLog).write(
                    f"Agent {run.id}: {run.agent_state or 'starting'} · g → Enter → o: open native pane")

        self.app.push_screen(AgentLauncher(self.store, self.item, self.cwd()), launched)

    def action_agents(self) -> None:
        self.app.push_screen(Agents(self.store, self.item.id))

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
        elif word == "learn" and rest:
            self.run("learn", rest)
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
        elif kind == "learn":
            shell = learn.agent_shell(text)
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
        if kind == "learn":
            items = learn.record(self.store, "".join(chunks), run=run_id)
            log.write(f"[{len(items)} motivations locked until {items[0].unlock_at[:10] if items else '-'}]")
            return
        if kind == "prompt":
            active = self.store.active()
            if not active or active.id != self.item.id:
                self.store.capture(tail or "(empty reply)", source="agent.response",
                                   ref=str(self.store.run_log(run_id)), key=f"run:{run_id}", task=self.item.id)
                notify("now: agent replied", text[:80])


class NowApp(App):
    COMMAND_PALETTE_BINDING = "ctrl+shift+p"
    CSS = """
    #title { padding: 2 4; text-style: bold; content-align: center middle; height: 7; color: $primary; border: heavy $primary; margin: 0 4; }
    #clock { padding: 0 4; color: $text-muted; }
    #children { margin: 1 4; height: auto; max-height: 8; }
    #log { margin: 0 4; height: 1fr; border: round $surface-lighten-2; }
    #cmd { dock: bottom; margin: 0 0 1 0; }
    #count { padding: 1 2; color: $text-muted; }
    #prompt { padding: 2 4; border: thick $warning; background: $surface; width: 80%; height: auto; margin: 4 8; }
    #find { padding: 1 2; border: thick $primary; background: $surface; width: 90%; height: 80%; margin: 2 4; }
    #find ListView { height: 1fr; }
    #hint { color: $text-muted; }
    .day { text-style: bold; color: $primary; }
    AgentLauncher, StopAgent { align: center middle; }
    #agent-launcher { padding: 1 2; border: thick $primary; background: $surface; width: 90%; height: 90%; }
    #agent-prompt { height: 1fr; min-height: 3; }
    #agent-error { height: auto; max-height: 4; color: $text-muted; }
    .agent-buttons { height: 3; margin-top: 1; }
    .agent-buttons Button { margin-right: 2; }
    #agent-stop-dialog { padding: 1 2; border: thick $warning; background: $surface; width: 80%; height: auto; }
    #agent-status { padding: 1 2; color: $primary; }
    #agent-context { height: auto; max-height: 9; margin: 0 2; }
    .agent-hint { padding: 1 2; color: $text-muted; }
    #agent-transcript { height: 1fr; margin: 0 2; border: round $surface-lighten-2; }
    #agent-output { height: auto; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+f", "find", "Find", priority=True),
    ]

    def __init__(self, store: Store | None = None):
        super().__init__()
        self.store = store or Store()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"find", "command_palette"} and isinstance(self.screen, (AgentLauncher, StopAgent)):
            return False
        return super().check_action(action, parameters)

    def on_key(self) -> None:
        self.store.touch()

    def action_command_palette(self) -> None:
        if self.use_command_palette and not CommandPalette.is_open(self):
            self.push_screen(NavigationCommandPalette(id="--command-palette"))

    def action_find(self) -> None:
        if isinstance(self.screen, Selector):
            return
        self.push_screen(Selector(self.store), self.selected)

    def selected(self, result: tuple[str, str] | None) -> None:
        """Act on a selector result by its type."""
        if not result:
            return
        kind, item_id = result
        if kind == "task":
            task = self.store.tasks()[item_id]
            self.store.activate(task.id)
            self.switch_screen(Now(self.store, task))
        elif kind == "noise":
            n = self.store.noise()[item_id]

            def after(title: str | None) -> None:
                if title:
                    self.store.extract(n.id, title)
                    screen = self.screen
                    if isinstance(screen, (Picker, Noise)):
                        screen.refresh_rows(*([screen.query_one("#filter", Input).value] if isinstance(screen, Picker) else []))

            self.push_screen(TextPrompt(n.body[:200], "Task title:", value=n.body.split("\n")[0][:72]), after)
        elif kind == "later":
            m = next(m for m in self.store.motivations() if m.id == item_id)
            if m.locked():
                self.notify(f"Locked until {m.unlock_at[:10]}. Not now.", severity="warning")
            else:
                self.store.promote(m.id)
                self.notify(f"Promoted: {m.title}")
        elif kind == "run":
            run = next((run for run in self.store.runs() if run.id == item_id), None)
            if run and run.kind == "agent":
                self.push_screen(AgentDetail(self.store, run))
            else:
                subprocess.Popen(["open", str(self.store.run_log(item_id))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "done":
            self.switch_screen(Review(self.store))

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
