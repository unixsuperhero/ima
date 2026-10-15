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
uv run now add "title"     # add a task from a shell
uv run now ls              # list pickable tasks (* marks the active one)
uv run now touch           # record activity; call from shell precmd or editor hooks
```

Data lives in `~/.local/share/now/` (override with `NOW_HOME`).

## Keys

| Screen | Key | Does |
| --- | --- | --- |
| Picker | type + Enter | activate top match, or create the typed task |
| Picker | ctrl+r | activate a random pickable task |
| Now | d | done, back to picker |
| Now | s | split: name the smallest piece, it becomes the active task |
| Now | u | put the task back |
| any | ctrl+q | quit |

## Rules

- Any keypress in the TUI, or `now touch` from anywhere, counts as activity.
- No activity for 3 minutes puts the task back and opens the picker.
- A gap of 1 to 3 minutes counts as a break.
- 15 minutes active with zero breaks puts the task back and asks for a split.
- Only open tasks with no open children are pickable, oldest first. No priority field.

## Context

- [Agent instructions](AGENTS.md)
- [Documentation index](docs/index.md)
- [Design: levels, data model, funnel](docs/design.md)
- [Thinktank shared conventions](file:///Users/unixsuperhero/proj/thinktank/docs/shared-conventions.md)
