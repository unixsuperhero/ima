import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from textual.widgets import Input, ListView

from now.app import Noise, Now, NowApp, Picker
from now.store import Store

os.environ["NOW_NOTIFY"] = "0"
os.environ.pop("TUIOS_ENV", None)  # never attach the tuios watcher in tests


async def settle(pilot, app, n=3):
    for _ in range(n):
        await pilot.pause()
    await app.workers.wait_for_complete()


class AppTest(unittest.IsolatedAsyncioTestCase):
    async def test_pick_split_done_random_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                self.assertIsInstance(app.screen, Picker)
                await pilot.press(*"fix the login bug", "enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Now)
                self.assertEqual(store.active().title, "fix the login bug")

                await pilot.press("s")
                await pilot.pause()
                await pilot.press(*"write failing test", "enter")
                await pilot.pause()
                self.assertEqual(app.screen.item.title, "write failing test")

                await pilot.press("d")
                await pilot.pause()
                self.assertIsInstance(app.screen, Picker)
                self.assertEqual([t.title for t in store.pickable()], ["fix the login bug"])
                self.assertEqual(store.done_today(), 1)

                await pilot.press("ctrl+r")
                await pilot.pause()
                self.assertIsInstance(app.screen, Now)
                await pilot.press("u")
                await pilot.pause()
                self.assertIsInstance(app.screen, Picker)
                self.assertIsNone(store.active())

    async def test_picker_normal_mode_and_know_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.add("first")
            store.add("second")
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("escape")  # normal mode: list focused
                await pilot.pause()
                self.assertIsInstance(app.focused, ListView)
                await pilot.press("j", "enter")
                await pilot.pause()
                self.assertEqual(app.screen.item.title, "second")
                await pilot.press("u")
                await pilot.pause()
                await pilot.press("escape")  # the fresh picker starts in insert mode
                await pilot.pause()
                self.assertIsInstance(app.focused, ListView)
                await pilot.press("i")  # back to insert
                await pilot.pause()
                self.assertIsInstance(app.focused, Input)
                await pilot.press(*"?how does textual focus work", "enter")
                await pilot.pause()
                self.assertEqual(store.active().kind, "know")
                self.assertEqual(store.active().title, "how does textual focus work")

    async def test_command_line_runs_shell_and_agent(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"NOW_AGENT_CMD": "echo reply:"}):
            store = Store(Path(tmp))
            t = store.add("ship it")
            store.activate(t.id)
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                self.assertIsInstance(app.screen, Now)
                await pilot.press("colon")
                await pilot.pause()
                self.assertIsInstance(app.focused, Input)
                await pilot.press(*"!echo hello; exit 3", "enter")
                await settle(pilot, app)
                runs = store.runs(t.id)
                self.assertEqual((runs[0].kind, runs[0].text, runs[0].exit), ("command", "echo hello; exit 3", 3))
                self.assertEqual(runs[0].tail, "hello\n")
                self.assertEqual(store.run_log(runs[0].id).read_text(), "hello\n")

                await pilot.press("colon", *"cd /", "enter")
                await pilot.pause()
                self.assertEqual(store.tasks()[t.id].cwd, "/")

                await pilot.press("colon", *"add write docs", "enter")
                await pilot.pause()
                self.assertEqual([c.title for c in store.children(t.id)], ["write docs"])

                await pilot.press("colon", *"ask summarize this", "enter")
                await settle(pilot, app)
                prompt_run = [r for r in store.runs(t.id) if r.kind == "prompt"][0]
                self.assertEqual(prompt_run.tail, "reply: summarize this\n")
                self.assertEqual(store.open_noise(), [])  # task still active: reply shown inline, not noise

    async def test_agent_reply_after_switching_task_becomes_noise(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
                os.environ, {"NOW_AGENT_CMD": "sleep 0.3; echo slow reply;"}):
            store = Store(Path(tmp))
            t = store.add("ask the agent")
            store.activate(t.id)
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("colon", *"ask think hard", "enter")
                await pilot.pause()
                await pilot.press("u")  # put the task back while the agent runs
                await settle(pilot, app, n=6)
                noise = store.open_noise()
                self.assertEqual(len(noise), 1)
                self.assertEqual((noise[0].source, noise[0].task), ("agent.response", t.id))
                self.assertIn("slow reply", noise[0].body)

    async def test_noise_screen_extract_and_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.capture("CI failed on #12", source="github.ci_status", ref="https://x/pull/12")
            store.capture("spam", source="slack.message")
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("escape", "n")
                await pilot.pause()
                self.assertIsInstance(app.screen, Noise)
                await pilot.press("e")
                await pilot.pause()
                await pilot.press("enter")  # accept the prefilled title
                await pilot.pause()
                self.assertEqual([t.title for t in store.pickable()], ["CI failed on #12"])
                await pilot.press("x")
                await pilot.pause()
                self.assertEqual(store.open_noise(), [])
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, Picker)


class LaterTest(unittest.IsolatedAsyncioTestCase):
    async def test_learn_command_then_later_screen_respects_lock(self):
        from now.learn import record
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
                os.environ, {"NOW_AGENT_CMD": "echo 'x | y' #", "NOW_LEARN_UNLOCK_DAYS": "7"}):
            store = Store(Path(tmp))
            t = store.add("real work")
            store.activate(t.id)
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                self.assertTrue(app.theme.startswith("task-"))
                await pilot.press("colon", *"learn rust for tuis", "enter")
                await settle(pilot, app)
                self.assertEqual([m.title for m in store.motivations()], ["x"])
                self.assertEqual([r.kind for r in store.runs(t.id)], ["learn"])
                await pilot.press("u", "escape", "l")
                await pilot.pause()
                self.assertEqual(app.theme, "textual-dark")
                await pilot.press("p")  # locked: refused
                await pilot.pause()
                self.assertEqual([x.title for x in store.pickable()], ["real work"])
                os.environ["NOW_LEARN_UNLOCK_DAYS"] = "-1"
                record(store, "free | now")
                app.screen.refresh_rows()
                await pilot.press("k", "p")  # the unlocked row sorts first
                await pilot.pause()
                self.assertEqual([x.title for x in store.pickable()], ["real work", "free"])


class SelectorReviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_find_from_anywhere_and_review(self):
        from now.app import Review, Selector
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            a = store.add("alpha work")
            store.add("beta work")
            store.capture("gamma noise about alpha", source="cli")
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("ctrl+f")
                await pilot.pause()
                self.assertIsInstance(app.screen, Selector)
                await pilot.press(*"alpha", "enter")  # first match is the open task
                await pilot.pause()
                self.assertIsInstance(app.screen, Now)
                self.assertEqual(store.active().id, a.id)

                await pilot.press("ctrl+f")  # works on the Now screen too
                await pilot.pause()
                await pilot.press(*"gamma", "enter")  # a noise row: extract prompt
                await pilot.pause()
                await pilot.press("enter")  # accept prefilled title
                await pilot.pause()
                self.assertIn("gamma noise about alpha", [t.title for t in store.pickable()])

                await pilot.press("d")
                await pilot.pause()
                await pilot.press("escape", "v")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                self.assertEqual(store.done_today(), 1)
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, Picker)


class ListNavigationTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_navigation_keeps_input_and_selects_highlight(self):
        for find in (False, True):
            with self.subTest(find=find), tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp))
                store.add("work first")
                store.add("work second")
                store.add("unrelated")
                app = NowApp(store)
                async with app.run_test(size=(100, 30)) as pilot:
                    if find:
                        await pilot.press("ctrl+f")
                    await pilot.press(*"work")
                    await pilot.pause()
                    field = app.focused
                    rows = app.screen.query_one(ListView)
                    await pilot.press("ctrl+p")
                    self.assertEqual(rows.index, 0)
                    await pilot.press("ctrl+n", "ctrl+n")
                    self.assertEqual(rows.index, 1)
                    await pilot.press("ctrl+p")
                    self.assertEqual(rows.index, 0)
                    await pilot.press("ctrl+n")
                    self.assertIs(app.focused, field)
                    self.assertEqual(field.value, "work")
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(store.active().title, "work second")

    async def test_modal_navigation_does_not_move_underlying_list(self):
        from now.app import TextPrompt
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.capture("first noise", source="cli")
            store.capture("second noise", source="cli")
            app = NowApp(store)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("escape", "n", "ctrl+n")
                await pilot.pause()
                rows = app.screen.query_one(ListView)
                self.assertEqual(rows.index, 1)
                await pilot.press("e")
                await pilot.pause()
                self.assertIsInstance(app.screen, TextPrompt)
                field = app.focused
                title = field.value
                await pilot.press("ctrl+p", "ctrl+n", "ctrl+p")
                self.assertEqual(rows.index, 1)
                self.assertIs(app.focused, field)
                self.assertEqual(field.value, title)
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual([t.title for t in store.pickable()], ["second noise"])


class PersistentAgentUITest(unittest.IsolatedAsyncioTestCase):
    async def test_launcher_preserves_multiline_errors_and_opening_context(self):
        from now.app import AgentLauncher
        from textual.widgets import Static, TextArea

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            task = store.add("original task")
            store.set_cwd(task.id, tmp)
            store.activate(task.id)
            app = NowApp(store)

            def launch(store, task_id, command, prompt, cwd):
                run = store.run_start(task_id, "agent", prompt, cwd)
                store.agent_update(run.id, agent_command=command, agent_state="starting")
                return store.runs(task_id)[-1]

            with unittest.mock.patch("now.app.agents.launch", side_effect=ValueError("invalid command")) as native:
                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.press("a")
                    await pilot.pause()
                    launcher = app.screen
                    self.assertIsInstance(launcher, AgentLauncher)
                    await pilot.press(*"first", "enter", *"second agx")
                    prompt = launcher.query_one("#agent-prompt", TextArea)
                    self.assertEqual(prompt.text, "first\nsecond agx")
                    await pilot.press("ctrl+f", "ctrl+shift+p")
                    self.assertIs(app.screen, launcher)
                    self.assertIs(app.focused, prompt)
                    command = launcher.query_one("#agent-command", Input)
                    command.value = 'claude --model "model with spaces"'
                    store.set_cwd(task.id, "/")
                    await pilot.press("ctrl+enter")
                    await settle(pilot, app)
                    self.assertIs(app.screen, launcher)
                    self.assertEqual(prompt.text, "first\nsecond agx")
                    self.assertEqual(command.value, 'claude --model "model with spaces"')
                    self.assertIn("invalid command", str(launcher.query_one("#agent-error", Static).content))
                    native.side_effect = launch
                    await pilot.click("#agent-launch")
                    await settle(pilot, app)
                    self.assertIsInstance(app.screen, Now)
                    run = store.runs(task.id)[0]
                    self.assertEqual((run.task, run.cwd, run.text), (task.id, tmp, "first\nsecond agx"))
                    self.assertEqual(store.active().id, task.id)

    async def test_poll_preserves_navigation_scroll_modal_and_activity(self):
        from now.app import AgentDetail, Agents, StopAgent
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            task = store.add("origin")
            other = store.add("other task")
            store.activate(task.id)
            for i in range(18):
                run = store.run_start(task.id, "agent", f"prompt {i}", tmp)
                store.agent_update(run.id, agent_command="claude", agent_state="working")
            foreign = store.run_start(other.id, "agent", "another task", tmp)
            store.agent_update(foreign.id, agent_state="idle")
            output = "\n".join(f"plain [bold] line {i}" for i in range(100))
            app = NowApp(store)
            with (unittest.mock.patch("now.app.agents.refresh", side_effect=lambda store, task=None: store.runs(task)),
                  unittest.mock.patch("now.app.agents.transcript", return_value=output) as transcript,
                  unittest.mock.patch("now.app.agents.stop") as stop):
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.press("g")
                    await settle(pilot, app)
                    screen = app.screen
                    self.assertIsInstance(screen, Agents)
                    self.assertEqual(len(screen.rows), 18)
                    await pilot.press("ctrl+n", "ctrl+n", "ctrl+p", "j", "k")
                    rows = screen.query_one(ListView)
                    self.assertEqual(rows.index, 1)
                    selected = screen.rows[1].id
                    widget = screen.query_one(f"#agent-{selected}")
                    activity = store.last_activity()
                    store.agent_update(selected, agent_state="needs_input")
                    await screen._refresh()
                    self.assertEqual(store.last_activity(), activity)
                    self.assertEqual(screen.rows[rows.index].id, selected)
                    self.assertIs(screen.query_one(f"#agent-{selected}"), widget)
                    self.assertIs(app.focused, rows)
                    await pilot.press("enter")
                    await settle(pilot, app)
                    detail = app.screen
                    self.assertIsInstance(detail, AgentDetail)
                    self.assertEqual(detail.run.id, selected)
                    viewport = detail.query_one("#agent-transcript", VerticalScroll)
                    viewport.scroll_to(y=12, animate=False, force=True)
                    await pilot.pause()
                    offset = viewport.scroll_offset
                    transcript.return_value = output + "\nnew output"
                    await detail._refresh()
                    await pilot.pause()
                    self.assertEqual(viewport.scroll_offset, offset)
                    self.assertIn("[bold]", str(detail.query_one("#agent-output", Static).content))
                    await pilot.press("x")
                    await pilot.pause()
                    modal = app.screen
                    self.assertIsInstance(modal, StopAgent)
                    focused = app.focused
                    transcript.return_value += "\nheld while modal open"
                    await detail._refresh()
                    await pilot.press("ctrl+n", "ctrl+p")
                    self.assertIs(app.screen, modal)
                    self.assertIs(app.focused, focused)
                    self.assertEqual(rows.index, 1)
                    self.assertNotIn("held while modal", detail.output)
                    await pilot.press("enter")  # Cancel is the default, never Stop.
                    await pilot.pause()
                    stop.assert_not_called()
                    await pilot.press("x")
                    await pilot.pause()
                    await pilot.click("#stop-confirm")
                    await settle(pilot, app)
                    stop.assert_called_once_with(store, selected)
                    await pilot.press("escape", "escape", "u")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, Picker)
                    await pilot.press("g")  # Filter input must receive this letter.
                    self.assertIsInstance(app.screen, Picker)
                    self.assertEqual(app.screen.query_one(Input).value, "g")
                    await pilot.press("escape", "g")
                    await settle(pilot, app)
                    self.assertIsInstance(app.screen, Agents)
                    self.assertIn(foreign.id, [run.id for run in app.screen.rows])

