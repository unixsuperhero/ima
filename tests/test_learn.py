import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from now.learn import learn, parse, record
from now.store import Store

ANSWER = """Here you go:
1. Build a tiny event-sourced todo | it is the smallest thing that proves the log idea
- Read the Textual theming docs | the whole app should take the task's color
no pipe on this line
Try tuios subscribe from Python | panes as activity is the missing link
"""


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse(self):
        self.assertEqual([t for t, _ in parse(ANSWER)],
                         ["Build a tiny event-sourced todo", "Read the Textual theming docs", "Try tuios subscribe from Python"])

    def test_record_locks_and_promote_refuses_until_unlocked(self):
        with unittest.mock.patch.dict(os.environ, {"NOW_LEARN_UNLOCK_DAYS": "7"}):
            items = record(self.store, ANSWER, run="r1")
        self.assertEqual(len(items), 3)
        self.assertTrue(all(m.locked() for m in self.store.motivations()))
        with self.assertRaises(ValueError):
            self.store.promote(items[0].id)
        self.assertEqual(self.store.pickable(), [])

    def test_unlocked_promote_becomes_task(self):
        with unittest.mock.patch.dict(os.environ, {"NOW_LEARN_UNLOCK_DAYS": "-1"}):
            items = record(self.store, ANSWER)
        self.assertFalse(items[0].locked())
        task = self.store.promote(items[0].id)
        self.assertEqual([t.id for t in self.store.pickable()], [task.id])
        self.assertEqual(len(self.store.motivations()), 2)

    def test_learn_runs_agent_command(self):
        with unittest.mock.patch.dict(os.environ, {"NOW_AGENT_CMD": "echo 'a | b\nc | d' #", "NOW_LEARN_UNLOCK_DAYS": "7"}):
            items = learn(self.store, "rust for tuis")
        self.assertEqual([(m.title, m.why) for m in items], [("a", "b"), ("c", "d")])
