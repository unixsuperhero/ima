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

    def owned_run(self):
        task = self.store.add("Originating task")
        run = self.store.run_start(task.id, "agent", "Inspect the change", self.tmp.name)
        self.store.agent_update(run.id, agent_command="claude", agent_session="work",
                                agent_window="owned-pane", agent_server="watch-test",
                                agent_state="working")
        return run

    def test_owned_transitions_share_noise_dedup_and_original_task(self):
        run = self.owned_run()
        other = self.store.add("Current task")
        self.store.activate(other.id)
        os.utime(self.store.activity_path, (100, 100))
        event = {"type": "agent-state", "session": "work", "window": "owned-pane",
                 "state": "done", "seq": 10}
        with unittest.mock.patch.dict(os.environ, {"TUIOS_SERVER": "watch-test"}):
            self.assertEqual(handle(self.store, event), "noise")
            self.assertIsNone(handle(self.store, {**event, "seq": 11}))
            self.assertFalse(self.store.agent_update(run.id, agent_state="done"))
            self.assertIsNone(handle(self.store, {**event, "state": "working"}))
            self.assertTrue(self.store.agent_update(run.id, agent_state="done"))
            self.assertIsNone(handle(self.store, {**event, "seq": 12}))
            self.assertEqual(handle(self.store, {**event, "state": "needs_input"}), "noise")
            self.assertEqual(handle(self.store, {**event, "state": "errored"}), "noise")
        noise = self.store.open_noise()
        self.assertEqual([(n.source, n.task) for n in noise], [
            ("agent.done", run.task), ("agent.done", run.task),
            ("agent.needs_input", run.task), ("agent.errored", run.task),
        ])
        self.assertEqual(len({n.key for n in noise}), 4)
        self.assertEqual(self.store.runs(run.task)[0].agent_state, "errored")
        self.assertIsNone(self.store.runs(run.task)[0].exit)
        self.assertEqual(self.store.active().id, other.id)
        self.assertEqual(self.store.activity_path.stat().st_mtime, 100)

    def test_owned_window_exclusion_and_server_scope(self):
        run = self.owned_run()
        event = {"type": "agent-state", "session": "work", "window": "owned-pane",
                 "state": "done", "seq": 20}
        with unittest.mock.patch.dict(os.environ, {"TUIOS_SERVER": "watch-test"}):
            self.assertIsNone(handle(self.store, event, own_window="owned-pane"))
        self.assertEqual(self.store.runs(run.task)[0].agent_state, "working")
        with unittest.mock.patch.dict(os.environ, {"TUIOS_SERVER": "other-test-server"}):
            self.assertEqual(handle(self.store, event), "noise")
        with unittest.mock.patch.dict(os.environ, {"TUIOS_SERVER": "watch-test"}):
            self.assertEqual(handle(self.store, event), "noise")
        self.assertEqual([n.task for n in self.store.open_noise()], [None, run.task])
        self.assertEqual(self.store.runs(run.task)[0].agent_state, "done")

    def test_watch_counts_only_new_owned_attention(self):
        run = self.owned_run()
        feed = Path(self.tmp.name) / "owned-feed.jsonl"
        events = [{"seq": seq, "type": "agent-state", "session": "work",
                   "window": "owned-pane", "state": state}
                  for seq, state in enumerate(["done", "done", "working", "done"])]
        feed.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        with unittest.mock.patch.dict(os.environ, {
            "NOW_SUBSCRIBE_CMD": f"cat {feed}", "TUIOS_SERVER": "watch-test",
        }):
            self.assertEqual(watch(self.store, "work", "me-0"), 2)
        self.assertEqual([(n.source, n.task) for n in self.store.open_noise()],
                         [("agent.done", run.task), ("agent.done", run.task)])

