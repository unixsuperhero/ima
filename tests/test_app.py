import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from textual.widgets import Input, ListView

from now.app import Noise, Now, NowApp, Picker
from now.store import Store

os.environ["NOW_NOTIFY"] = "0"


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
