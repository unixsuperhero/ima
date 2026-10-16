# User stories

How a day with `now` feels, written against what is built. Screen sketches are
the actual layout, not aspiration. Gaps are listed at the end.

## 1. Starting the day

I open a pane and type `now`. There is no dashboard. The picker shows a one-line
header and an empty filter box.

```
0 pickable · 0 done today · 34 noise
┌──────────────────────────────────────────────┐
│ filter, or type a new task and press Enter   │
└──────────────────────────────────────────────┘
```

Nothing is pickable, so I type `triage noise` and press Enter. It is created and
activated in one keystroke. The screen swaps to Now: one title, one clock line.

```
                    triage noise

active 00:03 / 15:00   idle 00:01 / 03:00   breaks 0
```

In the next pane I run `now noise`, `now extract b1ef5421 "fix flaky login test"`,
`now drop 40e419d4 e5fc7d1d`. Eight minutes later I come back, press `d`. The
picker now says `6 pickable · 1 done today · 20 noise`. The first checkmark of
the day was clearing the inbox.

## 2. A task that fights back

I pick `fix flaky login test`. Fourteen minutes in, I have not looked up once.
At 15:00 the screen changes under me:

```
┌─────────────────────────────────────────────────┐
│ 15 minutes on: fix flaky login test             │
│ Smallest piece you can finish in 5 minutes?     │
│ ┌─────────────────────────────────────────────┐ │
│ │ leave empty to just put it back             │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

I type `reproduce it once with --seed 42`. That becomes the task on screen. The
parent disappears from the picker until this child is done. Four minutes later I
have a seed that fails every time. `d`. The picker shows the parent again, plus
whatever else is open. I pick it again and split it again: `assert on the order,
not the timestamp`. `d`. The parent is done too, in three checkmarks instead of
one forty-minute slog.

## 3. Getting pulled away

I pick `write release notes`. Someone grabs me in Slack and I am gone for five
minutes. When I switch back the app has moved on without me:

```
2 pickable · 4 done today · 20 noise
  Idle 3 min. Put back: write release notes
```

No timer kept running against me, no guilt screen. The task is open again, oldest
first. I press Enter and I am back on it, or I press ctrl+r and let it deal me
something else.

## 4. The funnel while I work

A second pane runs `now poll --loop 120`. I never look at it. While I am on a task,
CI fails on one of my PRs. The poller notices the fingerprint change and the noise
count in the picker header is one higher the next time I finish something.

```
  3 pickable · 7 done today · 21 noise
```

Between tasks I run `now noise | head`:

```
9c1d2e3f [github.ci_status] Add results site: review=NONE checks[SUCCESS=4] -> review=NONE checks[FAILURE=1 SUCCESS=3] https://github.com/o/r/pull/12
```

`now extract 9c1d2e3f "fix the failing check on #12"`. It is pickable, and it
links back to the PR. The PR does not interrupt me; it waits in the queue like
everything else.

## 5. Nothing feels urgent

After lunch every open task looks the same size and I do not want to choose.
I press ctrl+r. The picker activates a random pickable task. Choosing took zero
seconds, and the fifteen-minute cap means a wrong pick costs at most fifteen
minutes before I am asked to shrink it.

## 6. End of day

The picker header reads `1 pickable · 11 done today · 14 noise`. That number is
the only metric the app shows. Hours never appear anywhere.

## The feel

- The app is almost always showing exactly one sentence: the task.
- Every transition lands on either the Now screen or the picker. There is no
  third place to be.
- Finishing is one key. Choosing is one key. Shrinking is one sentence.

## 7. Running things without leaving

On `fix flaky login test` I press `:` and type `!uv run pytest -x tests/test_login.py`.
Output streams into the panel under the task. Exit 1, 40 seconds, recorded against the
task. `:ask why would this test pass alone and fail in the suite` sends the file to the
agent. I don't wait: `u`, pick the next thing. Three minutes later a notification says
the agent replied. Next time I'm in the picker, the noise count is one higher and the
reply is already linked to the test task.

## Rough edges today

- There is no Review screen; the done count in the picker header is all there is.
- Per-task theming is not built, so switching tasks does not change the colors.
- No web UI yet.

