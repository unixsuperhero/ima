# Design

## Levels, never mixed on one screen

| Level | What lives here | Only verb | Built |
| --- | --- | --- | --- |
| Noise | raw captures from every funnel source | extract | Noise screen + CLI: capture, noise, extract, drop, poll |
| Items | tasks, to-knows, deferred motivations | pick | tasks only |
| Now | the single active task and its children | finish | yes |
| Review | done log, counts per day | read | count in picker header only |

## Data model

Everything is a typed item with metadata. Collections are filters over items.

```js
task       = { id, type:'task', meta:{ title, kind:'do'|'know', state:'open'|'active'|'done', parent, from: noiseId, cwd } }
run        = { id, type:'run',  meta:{ task, kind:'command'|'prompt', text, cwd, exit, duration_ms, tail } }
noise      = { id, type:'noise', meta:{ body, source, ref, at, task } }
know       = { id, type:'know', meta:{ question, answer, state } }
motivation = { id, type:'motivation', meta:{ title, why, unlock_at, run, state:'open'|'done', task } }
action     = { id, type:'action', meta:{ verb, taskId, at } }
```

Today `task`, `noise`, `run`, and `motivation` exist, stored as an append-only event log in
`events.jsonl`. State is derived by replaying the log. See `now/store.py`.
A task made by `extract` carries `from: noiseId`; the noise carries `task: taskId`.

## Funnel sources

Noise arrives from these sources and is extracted into tasks. Each noise item
keeps `source`, `ref`, and a dedup `key` so the resulting task links back to
where it came from and re-polling never duplicates. Pollers live in `now/poll.py`. Slack is reached only through an MCP server
registered in h-mcp, never a Slack token held by this app;
`clipboard` and `pane` are just `capture -s` values with no automation yet.

```js
source = 'cli' | 'clipboard' | 'pane'
       | 'agent.response' | 'agent.done' | 'agent.needs_input' | 'agent.errored'
       | 'slack.message' | 'slack.thread'
       | 'github.review_request' | 'github.pr_inbox'
       | 'github.ci_status' | 'github.pr_status'
```

`agent.response` is the one source that arrives already linked to a task: an
`:ask` whose reply lands after you moved on. Shell runs and prompts are the
first two infinitives the app gates: run and ask. Both are impossible without
an active task because the command line only exists on the Now screen.

Extraction is itself a task ("triage noise") so clearing the inbox is a checkmark.

## Session rules

```js
rules = [
  { when: 'idle >= 3min',                   then: 'unset task, notify, open picker' },
  { when: 'active >= 15min && breaks == 0', then: 'unset task, ask for the smallest 5-minute piece' },
  { when: 'done',                           then: 'log, open picker immediately' },
]
```

Activity is the mtime of one file. Any process can bump it with `now touch`,
so editors and shells outside the TUI count as working.

## Terminal, agents, and the app

Panes and windows are where work physically happens; a pane id is only ever a
`ref`. The active task is global to the person. Every run started from the app
carries `NOW_TASK` and `NOW_RUN` in its environment so child processes and hooks
can attribute their own events. Projects are not a type: a task's `cwd` (its
repo) is the grouping that exists in practice, and a parent task groups subtasks.

## Later, strictly

`:learn TOPIC` or `now learn` is a run of kind `learn`. Its answer is parsed as
`title | why` lines into motivations with an `unlock_at` seven days out. The
Later screen lists them, and `promote` refuses while locked. Nothing from a
learn run is ever pickable on the day it was asked.

## Not yet built

A review screen, a generic selector across every collection, a web UI mirroring
the TUI. `know` tasks are stored and shown but have no behavior of their own yet.
