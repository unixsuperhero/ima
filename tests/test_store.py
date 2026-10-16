import tempfile
import unittest
from pathlib import Path

from now.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_activate_done_survive_reload(self):
        a = self.store.add("write tests")
        b = self.store.add("ship")
        self.store.activate(a.id)
        self.assertEqual(Store(Path(self.tmp.name)).active().id, a.id)
        self.store.done(a.id)
        again = Store(Path(self.tmp.name))
        self.assertIsNone(again.active())
        self.assertEqual([t.id for t in again.pickable()], [b.id])
        self.assertEqual(again.done_today(), 1)

    def test_only_one_active(self):
        a = self.store.add("a")
        b = self.store.add("b")
        self.store.activate(a.id)
        self.store.activate(b.id)
        tasks = self.store.tasks()
        self.assertEqual(tasks[a.id].state, "open")
        self.assertEqual(tasks[b.id].state, "active")

    def test_parent_hidden_until_children_done(self):
        parent = self.store.add("big")
        child = self.store.add("small", parent=parent.id)
        self.assertEqual([t.id for t in self.store.pickable()], [child.id])
        self.store.done(child.id)
        self.assertEqual([t.id for t in self.store.pickable()], [parent.id])

    def test_unset_returns_to_open(self):
        a = self.store.add("a")
        self.store.activate(a.id)
        self.store.unset(a.id, "idle")
        self.assertEqual(self.store.tasks()[a.id].state, "open")
        self.assertIsNone(self.store.active())


if __name__ == "__main__":
    unittest.main()


class SearchReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_spans_collections_in_order(self):
        a = self.store.add("alpha task")
        self.store.add("beta task")
        n = self.store.capture("alpha noise", source="cli")
        self.store.motivate("alpha later", "why", "2099-01-01T00:00:00+00:00")
        r = self.store.run_start(a.id, "command", "echo alpha", "/")
        self.store.run_finish(r.id, 0, 5, "alpha\n")
        self.store.done(a.id)
        kinds = [k for k, _, _ in self.store.search("alpha")]
        self.assertEqual(kinds, ["noise", "later", "run", "done"])
        self.assertEqual([i for k, i, _ in self.store.search("alpha") if k == "noise"], [n.id])
        self.assertEqual([k for k, _, _ in self.store.search("beta")], ["task"])
        self.assertEqual(len(self.store.search("")), 5)

    def test_done_by_day_uses_local_dates(self):
        a = self.store.add("a")
        self.store.done(a.id)
        days = self.store.done_by_day()
        self.assertEqual(len(days), 1)
        self.assertEqual(self.store.done_today(), 1)
