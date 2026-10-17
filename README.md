# now

One active task at a time. The TUI shows the task you are on and two clocks.
Finishing opens the picker. Nothing else is on screen.

## Brief

```js
{
  name: "now",
  firstOutcome: "Pick a task, see it alone on screen, press d, land in the picker",
  acceptance: "Idle 3 min puts the task back; 15 min with no break forces a split; state survives restart",
  verification: "uv run python -m unittest discover -s tests -p 'test_*.py'",
  constraints: ["Local JSONL store", "No network", "Python 3.12+ and uv"],
  path: "~/proj/now"
}
```

## Run

```sh
uv sync
uv run now                 # open the TUI
uv run now add "title"     # add a task from a shell; --know for a to-know task
uv run now ls              # list pickable tasks (* marks the active one)
uv run now touch           # record activity; call from shell precmd or editor hooks

uv run now capture "text"             # raw noise from a shell; -s SOURCE -r URL; reads stdin if no text
uv run now noise                      # list open noise
uv run now extract NOISE_ID "title"   # noise -> task, linked both ways
uv run now drop NOISE_ID...           # discard noise
uv run now poll                       # pull github and slack once (or: now poll github)
uv run now poll --loop 120            # keep polling every 2 minutes
uv run now watch                      # inside tuios: panes count as activity, agent states become noise
uv run now learn "rust for tuis"      # throwaway run; prints the motivations it locked
uv run now later                      # list motivations with their unlock dates
uv run now find alpha                 # search every collection
uv run now review                     # done tasks by local day
```

## Durable task drafts

`now draft read --cwd /absolute/project/path` opens the app-owned pending draft as
JSON. It returns `{draft_id, revision, text, cwd}`. The file is `draft.json` in the
Now data directory, not an OS temporary directory. Reopening preserves its text
and original working directory.

`now draft save` accepts `{draft_id, revision, text}` as JSON on stdin and returns
the saved draft. It rejects stale revisions without overwriting another edit.
Saving a draft does not refresh the active task's activity clock.

`now draft apply` accepts `{draft_id, revision, indent_size}` as JSON on stdin.
It validates the entire saved outline and creates its tasks together. Plain
lines, `- title`, and `- [ ] title` are accepted; indentation defines subtasks.
Use `indent_size: 2` for two spaces per level. A leading `? ` makes a to-know
task. Blank lines are ignored; tabs, indentation jumps, empty task markers, and
checked boxes are rejected without consuming the draft. The final unformatted
line is included.

Successful application returns `{draft_id, revision, task_ids}` and consumes the
draft. Retrying that same identity returns the same task IDs, even if a newer
draft now exists. A fresh draft can intentionally create the same titles again.

Draft files and batch commits use atomic replacement and durable filesystem
flushes. Event writers share a lock; applying a batch rewrites the local event
log. This storage implementation requires POSIX file locking.

## Theme

The Now screen registers a Textual theme whose primary color is a hue derived from the
task id, so every task has its own color and the picker returns to the default. The task
is the mode; the color says so.

## Funnel

`now poll github` uses the already-authenticated `gh` CLI and captures:

- open PRs where your review is requested (`github.review_request`)
- unread PR notifications (`github.pr_inbox`)
- check or review-decision changes on your own open PRs (`github.ci_status`, `github.pr_status`);
  the first sighting of a PR is recorded silently, only later changes become noise

`now poll slack` does not talk to Slack directly. It calls a Slack-capable MCP server
through `h-mcp`, so whatever is registered there (slack, slack-no-dms, glean, runlayer,
runlayer-plugin) is the Slack connection. Setup on a machine that has one:

```sh
h-mcp add slack --agent claude      # or --config FILE; register the exact server name
h-mcp discover slack                # save its tool catalog
NOW_SLACK_MCP=slack now poll slack
```

The poller picks the first search tool the server exposes, in this order:
`slack_search_public_and_private`, `slack_search_public`, `conversations_search_messages`,
`search_messages`, `search`. Override with `NOW_SLACK_TOOL` and `NOW_SLACK_TOOL_ARG`
(the argument that carries the query). `NOW_SLACK_QUERY` defaults to `to:me after:yesterday`;
set it to your handle to catch mentions, or add `app:slack` for glean. Structured results and
JSON text blocks become one noise item per message (`slack.message`, `slack.thread`). Prose
results become one item per paragraph, keyed by content.

Re-polled items are deduplicated by key, so polling is safe to repeat. Poller cursors
live in `poll-state.json` next to the event log.

Data lives in `~/.local/share/now/` (override with `NOW_HOME`).

## Keys

Modal, like vim. A focused text box is insert mode; Esc leaves it. Everything else is
single letters in normal mode. Every key anywhere counts as activity.

| Screen | Key | Does |
| --- | --- | --- |
| Picker | type + Enter | activate the highlighted match, or create the typed task if there are no matches; a leading `?` makes a to-know task |
| Picker | Esc / i / `/` | normal mode (j k Enter on the list) / back to the filter |
| Picker | r, ctrl+r | activate a random pickable task |
| Picker | n | noise screen |
| Picker | l | later screen: motivations from learn runs |
| Picker | v | review screen: what got done, by day |
| Picker | f | find: one search over every collection |
| Now | d | done, back to picker |
| Now | s | split: name the smallest piece, it becomes the active task |
| Now | u | put the task back |
| Now | n | noise screen |
| Now | : | command line (below) |
| Noise | j k | move |
| Noise | e, Enter | extract into a task (title prefilled from the first line) |
| Noise | x | drop |
| Noise | o | open the ref with `open` |
| Noise | Esc, q | back to picker |
| Later | p, Enter | promote an unlocked motivation to a task; locked rows refuse |
| Later | x | dismiss |
| Review | j k, Esc | move, back; read only |
| Any option list | ctrl+n / ctrl+p | next/down / previous/up, including while typing in a filter or search |
| any | ctrl+f | find: tasks, noise, later, runs, done. Enter acts by type |
| any | ctrl+shift+p | open the command palette; ctrl+n / ctrl+p navigate its options |
| any | ctrl+q | quit |

Find acts on what you pick: a task is activated, noise opens the extract prompt, an
unlocked motivation is promoted, a run opens its log, a done task opens Review.

## Command line

Press `:` on the Now screen. Every run is attributed to the active task, executes in the
task's working directory, streams into the log panel, and is recorded as `run.start` and
`run.finish` events with the full output in `runs/<id>.log`.

```
:!CMD         run a shell command            (NOW_TASK and NOW_RUN are set in its environment)
:ask PROMPT   send a prompt to the agent     (NOW_AGENT_CMD, default `claude -p`)
:learn TOPIC  throwaway run; the answer becomes motivations locked for NOW_LEARN_UNLOCK_DAYS (7)
:add TITLE    add a child task
:cd PATH      set this task's working directory
:done :split :unset :noise :q
```

If you are still on the task when an agent reply finishes, it is shown in the log. If you
have moved on, the reply becomes noise (`agent.response`) linked to the task, and you get a
notification. Waiting on an agent is never a thing you do.

## Rules

- Any keypress in the TUI, any `now` CLI command, or `now touch` from a hook counts as activity.
  Inside tuios, a prompt or command in any pane of the session counts too: the TUI subscribes
  to tuios events itself, and `now watch` does the same from a plain shell.
- An agent in another pane reaching done, needs_input, or errored becomes noise
  (`agent.done`, `agent.needs_input`, `agent.errored`) with a `tuios://session/window` ref.
- No activity for 3 minutes puts the task back and opens the picker.
- A gap of 1 to 3 minutes counts as a break.
- 15 minutes active with zero breaks puts the task back and asks for a split.
- Idle and overrun also send a notification outside the pane: OSC 9 to the terminal
  (tuios shows it) and a macOS notification. `NOW_NOTIFY=0` silences both.
- Only open tasks with no open children are pickable, oldest first. No priority field.

## Context

- [Agent instructions](AGENTS.md)
- [Documentation index](docs/index.md)
- [Brief](docs/brief.md)
- [Design: levels, data model, funnel](docs/design.md)
- [User stories](docs/user-stories.md)
- [Thinktank shared conventions](file:///Users/unixsuperhero/proj/thinktank/docs/shared-conventions.md)
