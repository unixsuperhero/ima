"""Funnel pollers. Each turns an external inbox into noise items.

GitHub uses the `gh` CLI (already authenticated). Slack uses the Web API with
SLACK_TOKEN (and SLACK_COOKIE for xoxc session tokens). Per-source cursors and
fingerprints live in poll-state.json so only changes become noise.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter

from .store import Store


class State:
    def __init__(self, store: Store):
        self.path = store.root / "poll-state.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


# -- github -----------------------------------------------------------------

def gh(*args: str) -> object:
    proc = subprocess.run(["gh", "api", "-X", "GET", *args], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"gh api {args[0]}: {proc.stderr.strip() or proc.stdout.strip()[:200]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def gh_pr(url: str) -> dict:
    out = subprocess.run(["gh", "pr", "view", url, "--json", "statusCheckRollup,reviewDecision,mergeable"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def html_url(api_url: str) -> str:
    return api_url.replace("https://api.github.com/repos/", "https://github.com/").replace("/pulls/", "/pull/")


def pr_fingerprint(pr: dict) -> str:
    checks = Counter((c.get("conclusion") or c.get("state") or "PENDING").upper()
                     for c in pr.get("statusCheckRollup") or [])
    checks_s = " ".join(f"{k}={v}" for k, v in sorted(checks.items())) or "no-checks"
    return f"review={pr.get('reviewDecision') or 'NONE'} checks[{checks_s}]"


def poll_github(store: Store, state: State, gh=gh, gh_pr=gh_pr) -> int:
    added = 0

    for item in (gh("search/issues", "-f", "q=is:pr is:open review-requested:@me") or {}).get("items", []):
        repo = item["repository_url"].rsplit("/repos/", 1)[-1]
        if store.capture(f"Review requested: {repo} #{item['number']} {item['title']}",
                         source="github.review_request", ref=item["html_url"],
                         key=f"github.review_request:{item['html_url']}"):
            added += 1

    for n in gh("notifications") or []:
        if n["subject"].get("type") != "PullRequest":
            continue
        ref = html_url(n["subject"]["url"])
        if store.capture(f"{n['reason']}: {n['repository']['full_name']} {n['subject']['title']}",
                         source="github.pr_inbox", ref=ref,
                         key=f"github.pr_inbox:{n['id']}:{n['updated_at']}"):
            added += 1

    prs = state.data.setdefault("github_pr", {})
    seen = set()
    for item in (gh("search/issues", "-f", "q=is:pr is:open author:@me") or {}).get("items", []):
        url = item["html_url"]
        seen.add(url)
        fp = pr_fingerprint(gh_pr(url))
        prev = prs.get(url)
        prs[url] = fp
        if prev is None or prev == fp:
            continue
        source = "github.ci_status" if prev.split(" checks")[0] == fp.split(" checks")[0] else "github.pr_status"
        if store.capture(f"{item['title']}: {prev} -> {fp}", source=source, ref=url,
                         key=f"{source}:{url}:{fp}"):
            added += 1
    for url in list(prs):
        if url not in seen:
            del prs[url]
    return added


# -- slack ------------------------------------------------------------------

def slack_api(method: str, **params: object) -> dict:
    token = os.environ.get("SLACK_TOKEN")
    if not token:
        raise RuntimeError("SLACK_TOKEN is not set")
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    cookie = os.environ.get("SLACK_COOKIE")
    if cookie:
        req.add_header("Cookie", f"d={cookie}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"slack {method}: {data.get('error')}")
    return data


def poll_slack(store: Store, state: State, api=slack_api) -> int:
    added = 0
    me = api("auth.test")["user_id"]

    for m in api("search.messages", query=f"<@{me}>", sort="timestamp", sort_dir="desc", count=50)["messages"]["matches"]:
        ch = m.get("channel", {})
        source = "slack.thread" if m.get("thread_ts") and m.get("thread_ts") != m.get("ts") else "slack.message"
        who = m.get("username") or m.get("user", "?")
        if store.capture(f"#{ch.get('name', ch.get('id'))} {who}: {m.get('text', '')}", source=source,
                         ref=m.get("permalink"), key=f"slack:{ch.get('id')}:{m['ts']}"):
            added += 1

    oldest = state.data.setdefault("slack_dm_oldest", {})
    for conv in api("users.conversations", types="im,mpim", exclude_archived="true", limit=200)["channels"]:
        cid = conv["id"]
        since = oldest.get(cid, str(time.time() - 86400))
        hist = api("conversations.history", channel=cid, oldest=since, limit=50)["messages"]
        for m in hist:
            if m.get("user") == me or m.get("subtype"):
                continue
            if store.capture(f"DM {m.get('user', '?')}: {m.get('text', '')}", source="slack.message",
                             ref=None, key=f"slack:{cid}:{m['ts']}"):
                added += 1
        if hist:
            oldest[cid] = max(m["ts"] for m in hist)
    return added


POLLERS = {"github": poll_github, "slack": poll_slack}


def run(store: Store, sources: list[str]) -> dict[str, int | str]:
    state = State(store)
    result: dict[str, int | str] = {}
    for name in sources:
        try:
            result[name] = POLLERS[name](store, state)
        except Exception as exc:  # one failing source must not stop the others
            result[name] = f"error: {exc}"
    state.save()
    return result
