# now agent instructions

- Read [README.md](README.md) and [the docs index](docs/index.md) before changes.
- When starting implementation or organizing notes, read
  [Thinktank shared conventions](file:///Users/unixsuperhero/proj/thinktank/docs/shared-conventions.md).
  This local link needs updating if Thinktank moves or this repo changes machines.
- Work from this project's actual repository. Symlinks do not reliably provide
  parent instruction inheritance.
- Keep instructions and commands specific to the implementation that exists.
  Do not invent verification results or setup commands for an unchosen stack.
- Preserve unrelated changes. Exercise changed behavior, update affected docs,
  and commit completed changes. Commit often. Do not create Git worktrees.
- Add task-specific context through short links, not full copies of shared notes.

## Keyboard navigation

- Every option list must support `Ctrl+n` for next/down and `Ctrl+p` for
  previous/up, alongside its existing navigation keys. This includes lists
  controlled by a focused filter or search input. Keep that input focused and
  unchanged while navigating; Enter acts on the highlighted option.
- Scope navigation to the current screen or modal. Do not intercept these keys
  in unrelated text inputs or move a list behind an open modal.
- When adding or changing a list, verify both shortcuts and selection behavior.
