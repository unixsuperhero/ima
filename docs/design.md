# Design

## Levels, never mixed on one screen

| Level | What lives here | Only verb | Built |
| --- | --- | --- | --- |
| Noise | raw captures from every funnel source | extract | CLI: capture, noise, extract, drop, poll |
| Items | tasks, to-knows, deferred motivations | pick | tasks only |
| Now | the single active task and its children | finish | yes |
| Review | done log, counts per day | read | count in picker header only |

## Data model

Everything is a typed item with metadata. Collections are filters over items.

```js
task       = { id, type:'task', meta:{ title, state:'open'|'active'|'done', parent, from: noiseId } }
noise      = { id, type:'noise', meta:{ body, source, ref, at } }
know       = { id, type:'know', meta:{ question, answer, state } }
motivation = { id, type:'motivation', meta:{ title, why, unlockAt, from: learnRunId } }
action     = { id, type:'action', meta:{ verb, taskId, at } }
```

Today `task` and `noise` exist, stored as an append-only event log in
`events.jsonl`. State is derived by replaying the log. See `now/store.py`.
A task made by `extract` carries `from: noiseId`; the noise carries `task: taskId`.

## Funnel sources

Noise arrives from these sources and is extracted into tasks. Each noise item
keeps `source`, `ref`, and a dedup `key` so the resulting task links back to
where it came from and re-polling never duplicates. Pollers live in `now/poll.py`;
`clipboard` and `pane` are just `capture -s` values with no automation yet.

```js
source = 'cli' | 'clipboard' | 'pane'
       | 'slack.message' | 'slack.thread'
       | 'github.review_request' | 'github.pr_inbox'
       | 'github.ci_status' | 'github.pr_status'
```

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

## Not yet built

A Noise screen in the TUI (triage is CLI only), learn-for-later runs,
per-task theming, tuios activity and pane capture, review screen.
