"""Fault-injection transport tests; these do not impersonate a native tuios smoke."""
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from now import agents
from now.store import Store


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.task = self.store.add("originating task")
        self.store.activate(self.task.id)
        self.activity = self.store.activity_path.stat().st_mtime_ns

    def intent(self):
        run = self.store.run_start(self.task.id, "agent", "original prompt", self.tmp.name)
        self.store.agent_update(run.id, agent_command="claude", agent_session="isolated",
                                agent_window=f"now-agent-{run.id}", agent_state="starting",
                                prompt_status="pending")
        return run

    def pane(self, run, state="working", window="full-window-uuid"):
        return {"window_id": window, "custom_name": f"now-agent-{run.id}", "agent_state": state}

    def result(self, windows):
        return json.dumps({"success": True, "windows": windows})

    def test_launch_preserves_arguments_prompt_and_context_before_spawn(self):
        def spawn(argv, **kwargs):
            run = Store(self.store.root).runs()[0]
            self.assertEqual((run.text, run.agent_state, run.prompt_status),
                             ("first line\nsecond line", "starting", "pending"))
            self.assertEqual(argv[argv.index("--") + 1:], ["--model", "two words", ";touch nope"])
            self.assertIn(f"NOW_HOME={self.store.root.resolve()}", argv)
            self.assertIn(f"NOW_TASK={self.task.id}", argv)
            self.assertIn(f"NOW_RUN={run.id}", argv)
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("--focus", argv)
            kwargs["stdout"].write(json.dumps({"success": True, "ready": True, "prompt_status": "sent"}))
        with patch.dict(os.environ, {"TUIOS_SESSION": "isolated", "TUIOS_SERVER": "test"}), \
                patch.object(agents.shutil, "which", side_effect=lambda x: f"/bin/{x}"), \
                patch.object(agents.subprocess, "Popen", side_effect=spawn):
            run = agents.launch(self.store, self.task.id, 'claude --model "two words" ";touch nope"',
                                "first line\nsecond line", self.tmp.name)
        with patch.object(agents, "_call", return_value=self.result([self.pane(run)])):
            fresh = agents.refresh(Store(self.store.root))[0]
        self.assertEqual((fresh.agent_window, fresh.prompt_status, fresh.agent_server),
                         ("full-window-uuid", "sent", "test"))
        self.assertEqual(self.store.active().id, self.task.id)
        self.assertEqual(self.store.activity_path.stat().st_mtime_ns, self.activity)

    def test_validation_never_records_or_launches_invalid_requests(self):
        with patch.object(agents.subprocess, "Popen") as spawn:
            for task, command, prompt, cwd in [
                ("missing", "claude", "hello", self.tmp.name),
                (self.task.id, "", "hello", self.tmp.name),
                (self.task.id, "'broken", "hello", self.tmp.name),
                (self.task.id, "claude", " ", self.tmp.name),
                (self.task.id, "claude", "hello", self.tmp.name + "/missing"),
                (self.task.id, "now-nonexistent-executable-1234", "hello", self.tmp.name),
            ]:
                with self.assertRaises(ValueError):
                    agents.launch(self.store, task, command, prompt, cwd)
            spawn.assert_not_called()
        self.assertEqual(self.store.runs(), [])
        self.assertEqual(self.store.active().title, "originating task")

    def test_restart_attention_dedup_and_second_turn(self):
        run = self.intent()
        with patch.object(agents, "_call", return_value=self.result([self.pane(run, "done")])):
            agents.refresh(Store(self.store.root))
        before = self.store.events_path.read_bytes()
        self.assertFalse(agents.observe(self.store, "isolated", "full-window-uuid", "done"))
        self.assertEqual(before, self.store.events_path.read_bytes())
        self.store.agent_update(run.id, agent_state="unknown")
        self.assertFalse(agents.observe(self.store, "isolated", "full-window-uuid", "done"))
        agents.observe(self.store, "isolated", "full-window-uuid", "working")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: agents.observe(Store(self.store.root), "isolated",
                                                            "full-window-uuid", "done"), range(8)))
        self.assertEqual(sum(results), 1)
        noise = list(self.store.noise().values())
        self.assertEqual([n.task for n in noise], [self.task.id, self.task.id])
        self.assertEqual([n.source for n in noise], ["agent.done", "agent.done"])
        self.assertNotEqual(noise[0].key, noise[1].key)
        self.assertIn(run.id, noise[0].ref)
        self.assertIn("full-window-uuid", noise[0].ref)
        self.assertIsNone(self.store.runs()[0].exit)
        self.assertIn("agent done", next(label for kind, _, label in self.store.search("") if kind == "run"))
        self.assertEqual(self.store.activity_path.stat().st_mtime_ns, self.activity)

    def test_observe_resolves_initial_name_and_rejects_foreign_pane(self):
        run = self.intent()
        with patch.object(agents, "_call", return_value=self.result([self.pane(run)])):
            self.assertIsNone(agents.observe(self.store, "isolated", "unowned", "done"))
            self.assertTrue(agents.observe(self.store, "isolated", "full-window-uuid", "needs_input"))
        self.assertEqual(self.store.runs()[0].agent_window, "full-window-uuid")
        self.assertEqual(self.store.open_noise()[0].task, self.task.id)

    def test_unavailable_is_unknown_missing_is_closed_and_snapshot_survives(self):
        run = self.intent()
        with patch.object(agents, "_call", side_effect=[self.result([self.pane(run)]), "saved [literal] output\n"]):
            self.assertEqual(agents.transcript(self.store, run.id), "saved [literal] output\n")
        with patch.object(agents, "_call", side_effect=RuntimeError("offline")):
            self.assertEqual(agents.refresh(self.store)[0].agent_state, "unknown")
            self.assertEqual(agents.transcript(self.store, run.id), "saved [literal] output\n")
        with patch.object(agents, "_call", return_value=self.result([])):
            self.assertEqual(agents.refresh(self.store)[0].agent_state, "closed")
        self.assertEqual(agents.transcript(self.store, run.id), "saved [literal] output\n")
        self.assertIsNone(self.store.runs()[0].exit)

    def test_timeout_keeps_pane_and_failed_stop_does_not_claim_stopped(self):
        run = self.intent()
        self.store.run_log(run.id).with_suffix(".startup.json").write_text(json.dumps({
            "success": True, "outcome": "timeout", "ready": False, "reason": "Waiting for trust"}))
        with patch.object(agents, "_call", return_value=self.result([self.pane(run, "needs_input")])):
            fresh = agents.refresh(self.store)[0]
        self.assertEqual(fresh.agent_state, "needs_input")
        self.assertIn("Waiting for trust", fresh.prompt_status)
        with patch.object(agents, "_call", side_effect=[self.result([self.pane(run)]),
                self.result([self.pane(run)]), "last output", RuntimeError("permission denied")]):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                agents.stop(self.store, run.id)
        self.assertEqual(self.store.runs()[0].agent_state, "needs_input")
        with patch.object(agents, "_call", side_effect=[self.result([self.pane(run)]),
                self.result([self.pane(run)]), "last output", '{"success": true}']) as call:
            agents.stop(self.store, run.id)
        self.assertEqual(call.call_args.args, ("close-window", "isolated", None, "full-window-uuid", "--json"))
        self.store.agent_update(run.id, agent_state="working")
        self.assertEqual(self.store.runs()[0].agent_state, "stopped")
        self.assertEqual(agents.transcript(self.store, run.id), "last output")

    def test_full_id_never_adopts_replacement_with_same_name(self):
        run = self.intent()
        self.store.agent_update(run.id, agent_window="original-id")
        with patch.object(agents, "_call", return_value=self.result([self.pane(run, window="replacement")])):
            with self.assertRaises(RuntimeError):
                agents.stop(self.store, run.id)
        self.assertEqual(self.store.runs()[0].agent_state, "closed")

    def test_invalid_native_listing_is_not_authoritative_absence(self):
        run = self.intent()
        self.store.agent_update(run.id, agent_window="original-id")
        for response in ['{"success": false, "windows": []}', '{"success": true}', 'invalid']:
            with patch.object(agents, "_call", return_value=response):
                self.assertEqual(agents.refresh(self.store)[0].agent_state, "unknown")

    def test_unconfirmed_close_does_not_mark_stopped(self):
        run = self.intent()
        responses = [self.result([self.pane(run)]), self.result([self.pane(run)]),
                     "saved output", '{"success": false, "error": "denied"}']
        with patch.object(agents.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout=text, stderr="") for text in responses]):
            with self.assertRaises(RuntimeError):
                agents.stop(self.store, run.id)
        self.assertEqual(self.store.runs()[0].agent_state, "starting")
        self.assertEqual(self.store.run_log(run.id).read_text(), "saved output")

    def test_receipt_recovers_renamed_pane_after_restart(self):
        run = self.intent()
        self.store.run_log(run.id).with_suffix(".startup.json").write_text(json.dumps({
            "success": True, "name": f"now-agent-{run.id}", "session": "isolated",
            "window_id": "receipt-id", "prompt_status": "sent"}))
        with patch.object(agents, "_call", return_value=self.result([
                {"window_id": "receipt-id", "custom_name": "renamed", "agent_state": "working"}])):
            fresh = agents.refresh(Store(self.store.root))[0]
        self.assertEqual((fresh.agent_state, fresh.agent_window, fresh.prompt_status),
                         ("working", "receipt-id", "sent"))


if __name__ == "__main__":
    unittest.main()
