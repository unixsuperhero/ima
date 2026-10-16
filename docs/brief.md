# Brief (updated 2026-10-15)

The owner's statement of what `now` is for. Kept verbatim in spirit; trimmed of
repetition. The design doc and user stories derive from this.

## Goal

All tasks and subtasks, and the noise that action items are extracted from, are
funneled into a single app so they can be managed and reviewed from one central
system. Getting things done is the central mode. The mode is always now, not
later, not what's next.

## Wants

- A TUI. Keyboard driven and modal, like vim.
- Possibly a terminal multiplexer, but probably not: the real need is kicking
  off commands and agent prompts and capturing their inputs and outputs. If a
  multiplexer is ever needed, it is tuios or herdr, ideally the Go one built on
  charm.
- A web UI, if any, that works exactly like the TUI and is easier on the eyes.
- Different levels of abstraction, one at a time, never mixed except rarely.
- Every action a user can take is gated by, and associated with, a task or subtask.
  Gate means attributed, never forbidden: no gates on when or why anyone does
  anything.
- Maybe projects as a way to group tasks.
- A generic selector that searches any collection.
- Items in collections or associations are a type plus metadata.
- Idle past a threshold sends a notification or otherwise redirects attention.
- Track everything; a raw event log is one way.
- Task kinds: to know, and to do or finish.
- The whole app's theme follows the task's theme. The task is the mode.
- All actions managed by the app, so they can be gated on infinitives.
- Encouragement to write and document.
- For the industry: own the abstraction layer of human Time, Effort, Attention,
  and Context, and the system that defines their shape.

## Rules

- After picking a task: idle more than 3 minutes resets it. Working more than
  15 minutes with zero one-minute breaks resets it. Picking a task means the
  goal is to complete one task or subtask.
- No task is off the table. Never wonder whether something else is higher
  priority. Think about what to produce or finish, not what to do.
- Check as many small things off in a day as possible.

## Later, strictly

"Learn about x for y" prompts are throwaway runs whose output is a list of
motivation for later. Those items cannot be worked on until later.

## Concepts

- Git: repo, worktree, branch, remote
- GitHub: owner, repo, PR, CI
- Multiplexer: session, workspace, pane, tab
- Tasks: task, subtask
- Context
- Shell: bin, path, dir, file, command (exit status, stdin, stdout, stderr)
- AI: provider, model, agent, session, turn (prompt, response)

## Ambitions (separate from the app)

Build the system that treats task as a mode. Build systems. Enjoy building.
Not a compulsion or something driven by external forces. Take ownership.
