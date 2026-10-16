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
```

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
| Picker | type + Enter | activate the top match, or create the typed task; a leading `?` makes a to-know task |
| Picker | Esc / i / `/` | normal mode (j k Enter on the list) / back to the filter |
| Picker | r, ctrl+r | activate a random pickable task |
| Picker | n | noise screen |
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
| any | ctrl+q | quit |

## Command line

Press `:` on the Now screen. Every run is attributed to the active task, executes in the
task's working directory, streams into the log panel, and is recorded as `run.start` and
`run.finish` events with the full output in `runs/<id>.log`.

```
:!CMD         run a shell command            (NOW_TASK and NOW_RUN are set in its environment)
:ask PROMPT   send a prompt to the agent     (NOW_AGENT_CMD, default `claude -p`)
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
