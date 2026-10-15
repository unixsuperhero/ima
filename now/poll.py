"""Funnel pollers. Each turns an external inbox into noise items.

GitHub uses the `gh` CLI (already authenticated). Slack goes through a Slack-capable
MCP server registered in h-mcp (NOW_SLACK_MCP: slack, slack-no-dms, glean, runlayer...).
Per-source fingerprints live in poll-state.json so only changes become noise.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
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


# -- slack, through a registered MCP server via h-mcp ------------------------

SLACK_MCP_TOOLS = [  # (tool name, query argument); the first one the server exposes wins
    ("slack_search_public_and_private", "query"),   # Slack's own MCP server, e.g. behind runlayer
    ("slack_search_public", "query"),
    ("conversations_search_messages", "search_query"),  # slack-mcp-server
    ("search_messages", "query"),
    ("search", "query"),                              # glean
]


def mcp_tools(server: str) -> list[str]:
    proc = subprocess.run(["h-mcp", "tools", server, "--json"], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"h-mcp tools {server}: {proc.stderr.strip() or proc.stdout.strip()}")
    return [t["name"] for t in json.loads(proc.stdout)]


def mcp_call(server: str, tool: str, args: dict) -> dict:
    proc = subprocess.run(["h-mcp", "call", server, tool, "--args-file", "-"],
                          input=json.dumps(args), capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"h-mcp call {server} {tool}: {proc.stderr.strip() or proc.stdout.strip()[:300]}")
    return json.loads(proc.stdout)


def _message_list(node: object) -> list[dict] | None:
    """Depth-first: the first list of dicts that look like messages (have a text field)."""
    if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
        if any("text" in x for x in node):
            return node
    if isinstance(node, dict):
        for v in node.values():
            found = _message_list(v)
            if found:
                return found
    if isinstance(node, list):
        for v in node:
            found = _message_list(v)
            if found:
                return found
    return None


def slack_messages(result: dict) -> list[dict] | None:
    """Structured messages from an MCP result, or None when the tool returned prose."""
    if "structuredContent" in result:
        return _message_list(result["structuredContent"]) or []
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if len(texts) == 1:
        try:
            return _message_list(json.loads(texts[0])) or []
        except json.JSONDecodeError:
            pass
    return None


def poll_slack(store: Store, state: State, tools=mcp_tools, call=mcp_call) -> int:
    server = os.environ.get("NOW_SLACK_MCP", "slack")
    query = os.environ.get("NOW_SLACK_QUERY", "to:me after:yesterday")
    tool = os.environ.get("NOW_SLACK_TOOL")
    arg = os.environ.get("NOW_SLACK_TOOL_ARG", "query")
    if not tool:
        available = set(tools(server))
        tool, arg = next(((t, a) for t, a in SLACK_MCP_TOOLS if t in available),
                         (None, None))
        if not tool:
            raise RuntimeError(f"{server} exposes none of {[t for t, _ in SLACK_MCP_TOOLS]}; "
                               "set NOW_SLACK_TOOL and NOW_SLACK_TOOL_ARG")
    result = call(server, tool, {arg: query})
    added = 0
    messages = slack_messages(result)
    if messages is None:  # prose: one noise item per paragraph, keyed by content
        for c in result.get("content", []):
            for chunk in filter(None, (x.strip() for x in c.get("text", "").split("\n\n"))):
                digest = hashlib.sha256(chunk.encode()).hexdigest()[:16]
                if store.capture(chunk, source="slack.message", key=f"slack:{server}:{digest}"):
                    added += 1
        return added
    for m in messages:
        ch = m.get("channel") or m.get("channel_id") or ""
        if isinstance(ch, dict):
            ch_id, ch_name = ch.get("id", ""), ch.get("name") or ch.get("id", "")
        else:
            ch_id = ch_name = str(ch)
        ts = str(m.get("ts") or m.get("timestamp") or "")
        key = m.get("permalink") or f"{ch_id}:{ts}" or hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:16]
        thread = m.get("thread_ts")
        source = "slack.thread" if thread and str(thread) != ts else "slack.message"
        who = m.get("username") or m.get("user_name") or m.get("user") or "?"
        if store.capture(f"#{ch_name} {who}: {m.get('text', '')}", source=source,
                         ref=m.get("permalink"), key=f"slack:{key}"):
            added += 1
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
