import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from now.drafts import Drafts
from now.store import Store


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.drafts = Drafts(self.store)
        self.cwd = str(self.root / "project")

    def saved(self, text):
        draft = self.drafts.read(self.cwd)
        return self.drafts.save(draft["draft_id"], draft["revision"], text)

    def apply(self, draft, indent_size=2):
        return self.drafts.apply(draft["draft_id"], draft["revision"], indent_size)

    def cli(self, *args, payload=None, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "now.cli", *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, env={**os.environ, "NOW_HOME": str(self.root)},
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def test_hierarchy_kind_cwd_and_final_unformatted_line(self):
        draft = self.saved("- [ ] Ship\n  - ? Learn API\n    Verify docs\n\n  - Test\nFinal task")
        receipt = self.apply(draft)
        tasks = list(Store(self.root).tasks().values())
        self.assertEqual([t.title for t in tasks], ["Ship", "Learn API", "Verify docs", "Test", "Final task"])
        self.assertEqual([t.parent for t in tasks], [None, tasks[0].id, tasks[1].id, tasks[0].id, None])
        self.assertEqual([t.kind for t in tasks], ["do", "know", "do", "do", "do"])
        self.assertEqual([t.cwd for t in tasks], [self.cwd] * 5)
        self.assertEqual(receipt["task_ids"], [t.id for t in tasks])
        self.assertFalse(self.drafts.path.exists())
        self.assertEqual([t.title for t in self.store.pickable()], ["Verify docs", "Test", "Final task"])
        self.store.done(tasks[2].id)
        self.assertEqual([t.title for t in self.store.pickable()], ["Learn API", "Test", "Final task"])

    def test_restart_preserves_raw_text_revision_and_original_cwd(self):
        draft = self.saved("  unfinished\n\tstill editing\n- [ ] ")
        restarted = Drafts(Store(self.root))
        self.assertEqual(restarted.read("/other/project"), draft)
        self.assertEqual(draft["revision"], 1)
        self.assertEqual(restarted.save(draft["draft_id"], 1, draft["text"]), draft)

    def test_invalid_outlines_never_add_tasks_or_consume_text(self):
        existing = self.store.add("existing")
        for text in ["", " \n\n", "  first", "root\n    jumped", "root\n child",
                     "root\n\tchild", "root\n- [ ]", "root\n-", "root\n- [x] done",
                     "root\n- [X] done", "root\n? "]:
            with self.subTest(text=text):
                draft = self.saved(text)
                with self.assertRaises(ValueError):
                    self.apply(draft)
                self.assertEqual(self.drafts.read(self.cwd), draft)
                self.assertEqual(list(self.store.tasks()), [existing.id])

    def test_configured_indentation_and_invalid_sizes(self):
        draft = self.saved("Parent\n    Child")
        for size in [0, -1, True, "4", 1.5]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.apply(draft, size)
        self.apply(draft, 4)
        tasks = list(self.store.tasks().values())
        self.assertEqual([(t.title, t.parent) for t in tasks], [("Parent", None), ("Child", tasks[0].id)])

    def test_retry_preserves_newer_draft_and_identical_future_input_is_distinct(self):
        draft = self.saved("Same task")
        receipt = self.apply(draft)
        newer = self.saved("Same task")
        self.assertNotEqual(newer["draft_id"], draft["draft_id"])
        self.assertEqual(self.apply(draft), receipt)
        self.assertEqual(self.drafts.read(self.cwd), newer)
        with self.assertRaisesRegex(ValueError, "already applied"):
            self.drafts.save(draft["draft_id"], draft["revision"], "old editor text")
        self.assertEqual(self.drafts.read(self.cwd), newer)
        other_receipt = self.apply(newer)
        self.assertNotEqual(other_receipt["task_ids"], receipt["task_ids"])
        self.assertEqual([t.title for t in self.store.tasks().values()], ["Same task", "Same task"])

    def test_crash_after_commit_recovers_receipt_without_reapplying(self):
        draft = self.saved("Parent\n  Child")
        unlink = Path.unlink

        def interrupted_unlink(path, *args, **kwargs):
            if path == self.drafts.path:
                raise OSError("simulated interruption before draft cleanup")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupted_unlink), self.assertRaises(OSError):
            self.apply(draft)
        self.assertEqual(json.loads(self.drafts.path.read_text()), draft)
        ids = list(Store(self.root).tasks())
        receipt = self.apply(draft)
        self.assertEqual(receipt["task_ids"], ids)
        fresh = Drafts(Store(self.root)).read("/new/project")
        self.assertEqual((fresh["text"], fresh["revision"], fresh["cwd"]), ("", 0, "/new/project"))
        self.assertNotEqual(fresh["draft_id"], draft["draft_id"])
        self.assertEqual(self.apply(draft), receipt)
        self.assertEqual(list(self.store.tasks()), ids)
        self.assertEqual(self.drafts.read(self.cwd), fresh)

    def test_publish_failure_keeps_entire_draft_and_existing_tasks(self):
        existing = self.store.add("existing")
        draft = self.saved("Parent\n  Child")
        with patch("now.store.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.apply(draft)
        self.assertEqual(list(self.store.tasks()), [existing.id])
        self.assertEqual(self.drafts.read(self.cwd), draft)
        self.apply(draft)
        self.assertEqual([t.title for t in self.store.tasks().values()], ["existing", "Parent", "Child"])

    def test_directory_sync_failure_after_publish_is_retryable(self):
        draft = self.saved("one\ntwo")
        with patch("now.store.sync_directory", side_effect=OSError("sync failed")), self.assertRaises(OSError):
            self.apply(draft)
        receipt = self.apply(draft)
        self.assertEqual([t.title for t in self.store.tasks().values()], ["one", "two"])
        self.assertEqual(receipt["task_ids"], list(self.store.tasks()))
        self.assertEqual(self.drafts.read(self.cwd)["text"], "")

    def test_stale_save_and_apply_retain_winning_text(self):
        old = self.saved("old")
        newer = self.drafts.save(old["draft_id"], old["revision"], "winner")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.drafts.save(old["draft_id"], old["revision"], "loser")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.apply(old)
        self.assertEqual(self.drafts.read(self.cwd), newer)
        self.assertEqual(self.store.tasks(), {})

    def test_corrupt_log_blocks_apply_without_discarding_draft(self):
        draft = self.saved("keep this")
        self.store.events_path.write_text('{"type": "task.add"')
        with self.assertRaisesRegex(ValueError, "Corrupt event log"):
            self.apply(draft)
        self.assertEqual(json.loads(self.drafts.path.read_text()), draft)
        self.assertEqual(self.store.events_path.read_text(), '{"type": "task.add"')

    def test_failed_save_preserves_previous_durable_text(self):
        draft = self.saved("previous")
        with patch("now.store.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.drafts.save(draft["draft_id"], draft["revision"], "unsaved")
        self.assertEqual(Drafts(Store(self.root)).read(self.cwd), draft)

    def test_corrupt_batch_receipt_is_reported_without_consuming_draft(self):
        draft = self.saved("keep this")
        event = {"type": "draft.apply", "at": "2026-10-17T00:00:00+00:00",
                 "id": f"{draft['draft_id']}:{draft['revision']}", "tasks": [{}]}
        self.store.events_path.write_text(json.dumps(event) + "\n")
        error = self.cli("draft", "apply", payload={**draft, "indent_size": 2}, ok=False)
        self.assertIn("Invalid draft batch", error)
        self.assertEqual(json.loads(self.drafts.path.read_text()), draft)


    def test_cli_json_errors_and_autosave_does_not_record_activity(self):
        draft = self.cli("draft", "read", "--cwd", self.cwd)
        os.utime(self.store.activity_path, (1000, 1000))
        draft = self.cli("draft", "save", payload={**draft, "text": "CLI task"})
        self.cli("draft", "save", payload=draft)
        self.assertEqual(self.store.activity_path.stat().st_mtime, 1000)
        for payload in [[], {}, {"draft_id": draft["draft_id"], "revision": True, "text": "bad"}]:
            self.cli("draft", "save", payload=payload, ok=False)
        self.cli("draft", "read", "--cwd", "relative", ok=False)
        receipt = self.cli("draft", "apply", payload={**draft, "indent_size": 2})
        self.assertEqual(self.cli("draft", "apply", payload={**draft, "indent_size": 2}), receipt)
        self.assertEqual([t.title for t in Store(self.root).tasks().values()], ["CLI task"])

    def test_concurrent_cli_appends_and_apply_keep_every_task(self):
        draft = self.saved("Parent\n  Child")
        env = {**os.environ, "NOW_HOME": str(self.root)}

        def add(number):
            result = subprocess.run([sys.executable, "-m", "now.cli", "add", f"Task {number}"],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

        with ThreadPoolExecutor(max_workers=9) as pool:
            additions = [pool.submit(add, i) for i in range(8)]
            applies = [pool.submit(self.cli, "draft", "apply", payload={**draft, "indent_size": 2})
                       for _ in range(3)]
            for future in additions:
                future.result()
            receipts = [future.result() for future in applies]
        self.assertEqual(receipts, [receipts[0]] * 3)
        self.assertEqual(sorted(t.title for t in self.store.tasks().values()),
                         ["Child", "Parent", *[f"Task {i}" for i in range(8)]])


if __name__ == "__main__":
    unittest.main()
