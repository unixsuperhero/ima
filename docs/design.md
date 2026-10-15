# Design

## Levels, never mixed on one screen

| Level | What lives here | Only verb | Built |
| --- | --- | --- | --- |
| Noise | raw captures from every funnel source | extract | no |
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

Today only `task` exists, stored as an append-only event log in
`events.jsonl`. State is derived by replaying the log. See `now/store.py`.

## Funnel sources (planned)

Noise arrives from these sources and is extracted into tasks. Each noise item
keeps `source` and `ref` so the resulting task links back to where it came from.

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

Noise capture and extraction, learn-for-later runs, per-task theming,
tuios activity integration, review screen.
