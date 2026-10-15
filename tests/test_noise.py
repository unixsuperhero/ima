import tempfile
import unittest
from pathlib import Path

from now.store import Store


class NoiseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_extract_links_task_to_noise(self):
        n = self.store.capture("ci failed on #12", source="github.ci_status", ref="https://x/pull/12")
        self.assertEqual([x.id for x in self.store.open_noise()], [n.id])
        t = self.store.extract(n.id, "fix ci on #12")
        self.assertEqual(self.store.open_noise(), [])
        self.assertEqual(self.store.noise()[n.id].task, t.id)
        self.assertEqual(self.store.tasks()[t.id].from_noise, n.id)
        self.assertEqual([x.id for x in self.store.pickable()], [t.id])

    def test_duplicate_key_is_ignored(self):
        self.assertIsNotNone(self.store.capture("a", key="k1"))
        self.assertIsNone(self.store.capture("a again", key="k1"))
        self.assertEqual(len(self.store.open_noise()), 1)

    def test_drop(self):
        n = self.store.capture("spam")
        self.store.drop(n.id)
        self.assertEqual(self.store.open_noise(), [])
