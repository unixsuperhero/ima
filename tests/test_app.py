import tempfile
import unittest
from pathlib import Path

from now.app import Now, NowApp, Picker
from now.store import Store


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
