# Design

## Levels, never mixed on one screen

| Level | What lives here | Only verb | Built |
| --- | --- | --- | --- |
| Noise | raw captures from every funnel source | extract | Noise screen + CLI: capture, noise, extract, drop, poll |
| Items | tasks, to-knows, deferred motivations | pick | Picker, Later screen |
| Now | the single active task and its children | finish | yes |
| Review | done log, counts per local day | read | Review screen (v) and `now review` |

## Data model

Everything is a typed item with metadata. Collections are filters over items.

```js
task       = { id, type:'task', meta:{ title, kind:'do'|'know', state:'open'|'active'|'done', parent, from: noiseId, cwd } }
run        = { id, type:'run', meta:{ task, kind:'command'|'prompt'|'learn'|'agent', text, cwd, exit, duration_ms, tail } }
noise      = { id, type:'noise', meta:{ body, source, ref, at, task } }
know       = { id, type:'know', meta:{ question, answer, state } }
motivation = { id, type:'motivation', meta:{ title, why, unlock_at, run, state:'open'|'done', task } }
action     = { id, type:'action', meta:{ verb, taskId, at } }
```

Today `task`, `noise`, `run`, and `motivation` exist, stored as an append-only event log in
`events.jsonl`. State is derived by replaying the log. See `now/store.py`.
A task made by `extract` carries `from: noiseId`; the noise carries `task: taskId`.
Persistent agent runs additionally record `agent_command`, `agent_session`,
`agent_window`, `agent_server`, `agent_state`, and `prompt_status` through
`run.agent` events. Pane ownership belongs to tuios, not the TUI worker.

Pending task-entry text lives separately in `draft.json` as
`{draft_id, revision, text, cwd}`. It survives restarts but is not yet a task
collection. Applying validates the entire outline and publishes one `draft.apply`
event containing every task and parent relationship. That event is also the
retry receipt. Only then is the draft consumed; read recovers an interrupted
cleanup without creating duplicate tasks. A new draft receives a new identity.

`now/drafts.py` owns validation, revision conflicts, and application. A stable
`store.lock` coordinates readers and writers across event-log replacement.
Whole-batch publication avoids partial imports and per-task progress journals.
Its cost is rewriting the log on apply, acceptable for the current local store.

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

An `:ask` reply that lands after you move on produces task-linked
`agent.response` noise. App-launched persistent panes also keep their original
task on `agent.done`, `agent.needs_input`, and `agent.errored` transitions.
Status refresh and the live watcher share an atomic transition update so they
cannot duplicate the same attention event. A later working-to-done turn can
produce new attention. Unowned panes still produce generic, unlinked noise.
Shell runs, prompts, and persistent agents start from the Now task screen.

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

The active task is global to the person. Runs retain the task that started them,
not whichever task is current when they finish. Every app-launched agent carries
`NOW_HOME`, `NOW_TASK`, and `NOW_RUN` in its environment. Projects are not a type:
a task's `cwd` groups work by repository, and parent tasks group subtasks.

`a` opens a task-scoped launcher; `g` opens the agent list. The launcher snapshots
task and cwd, then starts the native `tuios start-agent` CLI detached. A durable
run and unique pane name are recorded before spawning, so reopening Now can
reconcile startup even if the original TUI exits first. There is no custom
background supervisor and no automatic prompt resubmission.

Agent detail refreshes only changed state/output without moving selection,
focus, or scroll. `o` focuses the recorded native pane; `x` confirms stopping it.
Authentication and approvals stay in the agent's own UI. Native turn states are
not exit codes: done/idle panes can accept further prompts. An unreachable
session is unknown; an authoritatively missing pane is closed. Output is a
bounded pane-tail snapshot saved to the run log, not a continuous transcript
archive. The old `:ask` path remains one-shot and is not restart-safe.

## The selector

`ctrl+f` anywhere opens one search box over every collection: open tasks, open
noise, motivations, runs, done tasks, in that order. It is the only place where
levels mix, and only as a list of typed rows. Enter acts by type and returns you
to a single level: a task goes to Now, noise opens the extract prompt, an unlocked
motivation is promoted, a command/prompt run opens its log, an agent run opens
agent detail, and a done task opens Review.

## Later, strictly

`:learn TOPIC` or `now learn` is a run of kind `learn`. Its answer is parsed as
`title | why` lines into motivations with an `unlock_at` seven days out. The
Later screen lists them, and `promote` refuses while locked. Nothing from a
learn run is ever pickable on the day it was asked.

## Not yet built

A web UI mirroring the TUI. `know` tasks are stored and shown but have no behavior
of their own yet. Projects are not a type; `cwd` and parent tasks group work.
