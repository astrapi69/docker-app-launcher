# Looking at the launcher — a QA guide

How to see every state of the launcher window, without Docker and without
changing anything on your machine.

## Why this exists

Everything else in this project checks that elements **exist** and what they
**mean**: the three frontends must render the same elements, every error
class must have an explanation text in all 11 languages, every operation
must end in a defined idle state. None of that shows how the window
**looks**.

That is not a theoretical gap. The text-wrapping bug (#47) and the window
that could not be resized were both found by a person looking at a screen,
after they had shipped. The preview switch exists so that looking does not
require installing an app, starting Docker, and provoking a failure.

## The 60-second version

```bash
make preview-tour
```

Six windows, five seconds each. Each one is titled `[n/6] <state>`, so you
always know which one you are looking at — including on a screenshot or a
phone photo sent into a chat.

```bash
make preview-tour PREVIEW_SECONDS=10                              # slower
make preview-tour PREVIEW_CONFIG=test-configs/adaptive-learner.json  # a real app config
```

Closing a window early skips to the next one.

## One state at a time

```bash
poetry run docker-app-launcher --preview small_window
```

| # | State | What it shows |
|---|-------|---------------|
| 1 | `fresh` | the window right after start, nothing installed |
| 2 | `busy_cancellable` | a long-running operation with a visible Cancel control |
| 3 | `failure_problem_card` | a failed check with the problem card (class, meaning, fix) |
| 4 | `guard_unavailable` | the note that the concurrency guard cannot do its job |
| 5 | `long_log` | a long, wrapping text in the log panel |
| 6 | `small_window` | the window at its minimum size |

`--help` lists them too, and refuses anything that is not on the list.

**Start with `small_window`.** That is the state the #47 wrapping bug lived
in, and small screens are where layout problems surface first.

## Real or fed — read the marker

Every preview prints one line before the window opens, and the same marker
is in `--help`:

```
small_window [real]: the real geometry path; what a small screen actually gets
guard_unavailable [fed]: the real localized guard_unavailable text; producing it
  for real needs an unreadable marker path, which would mean writing - forbidden here
```

* **`[real]`** — produced end to end by the same machinery the app uses.
* **`[fed]`** — the rendering chain is the shipped one, but its *input* is
  supplied here, because producing that state for real would need Docker or
  a write.

Three of the six are `[fed]`: `fresh` (its app state would come from the
daemon), `failure_problem_card` (a real doctor run queries Docker),
`guard_unavailable` (a real one needs an unreadable marker file, i.e. a
write).

**This matters for bug reports.** A screenshot labelled "this is what a
failure looks like" must not quietly be a drawing of one. When you file a
finding from a preview, paste the marker line with it.

What `[fed]` does **not** mean: that the texts are fake. The problem card
renders the shipped meaning/fix texts for a real error class — a test
enforces that, because a preview of an empty card would teach the wrong
thing.

## What to look for

| State | Should be true |
|---|---|
| all | the title starts with `[n/6] <state>` |
| all | no text is cut off, no label overlaps another |
| `busy_cancellable` | Cancel is visible and enabled while everything else is disabled |
| `failure_problem_card` | class, "What does this mean?" **and** "What you can do" are all filled |
| `guard_unavailable` | the note is visible without opening the log panel |
| `long_log` | lines wrap inside the window; no horizontal scrolling |
| `small_window` | every button is reachable; the status text still wraps |

An empty problem card, a Cancel button you cannot see, or text running past
the edge — those are findings. File them with the state name and the
marker line.

## The two promises, and how to check them yourself

The preview **touches no Docker** and **writes nothing**. Do not take that
on trust — both are ten-second checks.

Machine-checked, over all six states:

```bash
poetry run pytest tests/test_preview_states.py -v --no-cov
```

That replaces the daemon call with a landmine and compares the config
directory file-by-file before and after.

By hand, without the test harness — take Docker off the PATH entirely:

```bash
env PATH=/usr/bin:/bin poetry run docker-app-launcher --preview fresh
ls -la ~/.my-app 2>/dev/null || echo "nothing written"
```

The window opens anyway. Nothing appears in the config directory.

Note the deliberate difference from `--render-probe`: that one *does* arm
the concurrency marker, on purpose, to prove the guard works at the built
artifact's anchor. The preview is a looking tool, not a proof, so it writes
nothing at all.

## The other frontends

The launcher has three: `tk` (default and the one in the frozen bundle),
`ctk` and `qt`. All three render the same previews. Until
[#119](https://github.com/astrapi69/docker-app-launcher/issues/119) adds a
`--gui-backend` flag, choosing one means a config file:

```bash
poetry run docker-app-launcher --config <(echo '{"app_name":"P","gui_backend":"qt"}') --preview busy_cancellable
poetry run docker-app-launcher --config <(echo '{"app_name":"P","gui_backend":"ctk"}') --preview small_window
```

`ctk` and `qt` are optional extras. Without them installed you get an error
naming the missing extra, not a broken window.

**Known open point:** the appearance (light/dark) cannot be chosen at all
today, and the three frontends do not agree on what they follow — see
[#118](https://github.com/astrapi69/docker-app-launcher/issues/118). If a
preview looks wrong on a dark desktop, that is that issue, not a new one.

## Setup

```bash
poetry install --with dev --all-extras
```

You need a display. On a headless machine, wrap the command:

```bash
xvfb-run -a make preview-tour
```

There is nothing to see that way — but it is how CI runs the same windows,
and it proves the states still open.

## Pictures from the CI run — no local setup at all

Every CI run photographs the window and attaches the images. Open the run
on GitHub, scroll to **Artifacts**, download **`gui-screenshots`**. Inside:

* `preview_<frontend>_<n>_<state>.png` — the six preview states, for `tk`,
  `ctk` and `qt`. The window title in the picture carries the same
  `[n/N] <state>` marker, so a screenshot pulled out of the folder still
  names what it shows.
* the older per-state and per-language shots (`not_installed_de.png`,
  `qt_running_en.png`, …)
* `MANIFEST.md` — which states are `[real]` and which are `[fed]`, so a
  folder of PNGs can answer the one question that decides whether a picture
  is evidence.

Kept for 30 days. The images are taken in a dark palette applied by the
test helper — that is **not** the product's appearance; the launcher itself
has no theme setting yet ([#118](https://github.com/astrapi69/docker-app-launcher/issues/118)).

To produce the same set locally:

```bash
make screenshots        # -> test-screenshots/
```

Deliberately **not** an automatic image comparison — three toolkits with
different font rendering produce more false alarms than insight. The
pictures are for people.
