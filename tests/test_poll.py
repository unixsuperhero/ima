import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from now.poll import State, poll_github, poll_slack, pr_fingerprint
from now.store import Store

PR_URL = "https://github.com/o/r/pull/7"


def fake_gh(prs_by_me):
    def gh(*args):
        if args[0] == "notifications":
            return [{"id": "n1", "reason": "review_requested", "updated_at": "2026-10-15T00:00:00Z",
                     "subject": {"type": "PullRequest", "title": "Add thing",
                                 "url": "https://api.github.com/repos/o/r/pulls/3"},
                     "repository": {"full_name": "o/r"}},
                    {"id": "n2", "reason": "subscribed", "updated_at": "x",
                     "subject": {"type": "Issue", "title": "ignored", "url": ""},
                     "repository": {"full_name": "o/r"}}]
        q = args[2]
        if "review-requested" in q:
            return {"items": [{"number": 3, "title": "Add thing", "html_url": "https://github.com/o/r/pull/3",
                               "repository_url": "https://api.github.com/repos/o/r"}]}
        return {"items": prs_by_me}
    return gh


class GithubPollTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_requests_and_inbox_once_then_ci_changes(self):
        mine = [{"title": "My PR", "html_url": PR_URL, "number": 7}]
        gh = fake_gh(mine)
        pr = {"reviewDecision": None, "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"state": "PENDING"}]}

        state = State(self.store)
        added = poll_github(self.store, state, gh=gh, gh_pr=lambda url: pr)
        self.assertEqual(added, 2)  # review request + PR inbox; first sighting of own PR is silent
        sources = sorted(n.source for n in self.store.open_noise())
        self.assertEqual(sources, ["github.pr_inbox", "github.review_request"])
        self.assertEqual(self.store.open_noise()[1].ref, "https://github.com/o/r/pull/3")

        # same poll again: nothing new
        self.assertEqual(poll_github(self.store, state, gh=gh, gh_pr=lambda url: pr), 0)

        # a check fails -> ci_status noise
        pr2 = {"reviewDecision": None, "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]}
        self.assertEqual(poll_github(self.store, state, gh=gh, gh_pr=lambda url: pr2), 1)
        ci = [n for n in self.store.open_noise() if n.source == "github.ci_status"]
        self.assertEqual(len(ci), 1)
        self.assertIn("FAILURE=1", ci[0].body)

        # review decision changes -> pr_status noise
        pr3 = {**pr2, "reviewDecision": "APPROVED"}
        self.assertEqual(poll_github(self.store, state, gh=gh, gh_pr=lambda url: pr3), 1)
        self.assertEqual(len([n for n in self.store.open_noise() if n.source == "github.pr_status"]), 1)
        state.save()
        self.assertEqual(State(self.store).data["github_pr"][PR_URL], pr_fingerprint(pr3))


STRUCTURED = {"structuredContent": {"ok": True, "messages": {"matches": [
    {"ts": "1.0", "text": "hey look", "channel": {"id": "C1", "name": "eng"}, "username": "bob",
     "permalink": "https://s/p/1"},
    {"ts": "2.0", "thread_ts": "1.5", "text": "in thread", "channel": {"id": "C1", "name": "eng"},
     "username": "ann", "permalink": "https://s/p/2"}]}}}

JSON_TEXT = {"content": [{"type": "text", "text": json.dumps(
    {"results": [{"ts": "3.0", "text": "ping", "channel": "D1", "user": "UBOB"}]})}]}

PROSE = {"content": [{"type": "text", "text": "Found 2 messages:\n\n#eng bob: hey look\n\n#eng ann: in thread"}]}


class SlackPollTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.state = State(self.store)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_poll(self, tool_names, result, env=None):
        def call(server, tool, args):
            self.calls.append((server, tool, args))
            return result
        with unittest.mock.patch.dict(os.environ, env or {}, clear=False):
            for k in ("NOW_SLACK_MCP", "NOW_SLACK_TOOL", "NOW_SLACK_QUERY", "NOW_SLACK_TOOL_ARG"):
                if not (env and k in env):
                    os.environ.pop(k, None)
            return poll_slack(self.store, self.state, tools=lambda s: tool_names, call=call)

    def test_picks_first_known_tool_and_parses_structured(self):
        added = self.run_poll(["slack_send_message", "slack_search_public_and_private"], STRUCTURED)
        self.assertEqual(added, 2)
        self.assertEqual(self.calls, [("slack", "slack_search_public_and_private", {"query": "to:me after:yesterday"})])
        self.assertEqual(sorted(n.source for n in self.store.open_noise()), ["slack.message", "slack.thread"])
        self.assertEqual(self.store.open_noise()[0].ref, "https://s/p/1")
        self.assertEqual(self.run_poll(["slack_search_public_and_private"], STRUCTURED), 0)

    def test_server_tool_and_query_from_env(self):
        env = {"NOW_SLACK_MCP": "slack-no-dms", "NOW_SLACK_TOOL": "conversations_search_messages",
               "NOW_SLACK_TOOL_ARG": "search_query", "NOW_SLACK_QUERY": "@josh"}
        self.assertEqual(self.run_poll([], JSON_TEXT, env), 1)
        self.assertEqual(self.calls, [("slack-no-dms", "conversations_search_messages", {"search_query": "@josh"})])
        self.assertIn("#D1 UBOB: ping", self.store.open_noise()[0].body)

    def test_prose_result_becomes_one_item_per_paragraph(self):
        self.assertEqual(self.run_poll(["search"], PROSE), 3)
        self.assertEqual(self.run_poll(["search"], PROSE), 0)

    def test_no_known_tool_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.run_poll(["slack_send_message"], STRUCTURED)
