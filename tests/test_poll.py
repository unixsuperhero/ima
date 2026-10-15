import tempfile
import unittest
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


class SlackPollTest(unittest.TestCase):
    def test_mentions_and_dms(self):
        calls = []

        def api(method, **params):
            calls.append(method)
            if method == "auth.test":
                return {"user_id": "UME"}
            if method == "search.messages":
                return {"messages": {"matches": [
                    {"ts": "1.0", "text": "hey <@UME> look", "channel": {"id": "C1", "name": "eng"},
                     "username": "bob", "permalink": "https://s/p/1"},
                    {"ts": "2.0", "thread_ts": "1.5", "text": "in thread", "channel": {"id": "C1", "name": "eng"},
                     "username": "ann", "permalink": "https://s/p/2"}]}}
            if method == "users.conversations":
                return {"channels": [{"id": "D1"}]}
            if method == "conversations.history":
                return {"messages": [{"ts": "3.0", "user": "UBOB", "text": "ping"},
                                     {"ts": "3.5", "user": "UME", "text": "my reply"},
                                     {"ts": "3.6", "user": "UBOB", "subtype": "channel_join", "text": ""}]}
            raise AssertionError(method)

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            state = State(store)
            self.assertEqual(poll_slack(store, state, api=api), 3)
            sources = sorted(n.source for n in store.open_noise())
            self.assertEqual(sources, ["slack.message", "slack.message", "slack.thread"])
            self.assertEqual(state.data["slack_dm_oldest"]["D1"], "3.6")
            self.assertEqual(poll_slack(store, state, api=api), 0)
