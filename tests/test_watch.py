import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from now.store import Store
from now.watch import handle, watch

os.environ["NOW_NOTIFY"] = "0"

EVENTS = [
    {"seq": 1, "type": "subscribed"},
    {"seq": 2, "type": "prompt", "session": "work", "window": "aaaa-1"},
    {"seq": 3, "type": "command-started", "session": "work", "window": "bbbb-2"},
    {"seq": 4, "type": "agent-state", "session": "work", "window": "aaaa-1", "state": "working"},
    {"seq": 5, "type": "agent-state", "session": "work", "window": "aaaa-1", "state": "done"},
    {"seq": 6, "type": "agent-state", "session": "work", "window": "cccc-3", "state": "needs_input",
     "message": "allow Bash(rm -rf build)?"},
    {"seq": 7, "type": "agent-state", "session": "work", "window": "me-0", "state": "done"},
]


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_handle(self):
        results = [handle(self.store, e, own_window="me-0") for e in EVENTS]
        self.assertEqual(results, [None, "touch", "touch", None, "noise", "noise", None])
        noise = self.store.open_noise()
        self.assertEqual([n.source for n in noise], ["agent.done", "agent.needs_input"])
        self.assertEqual(noise[1].ref, "tuios://work/cccc-3")
        self.assertIn("allow Bash", noise[1].body)
        # the same event twice is one noise item
        self.assertIsNone(handle(self.store, EVENTS[4], own_window="me-0"))

    def test_watch_reads_a_stream(self):
        feed = Path(self.tmp.name) / "feed.jsonl"
        feed.write_text("\n".join(json.dumps(e) for e in EVENTS) + "\nnot json\n")
        with unittest.mock.patch.dict(os.environ, {"NOW_SUBSCRIBE_CMD": f"cat {feed}"}):
            self.assertEqual(watch(self.store, "work", "me-0"), 4)
        self.assertEqual(len(self.store.open_noise()), 2)
