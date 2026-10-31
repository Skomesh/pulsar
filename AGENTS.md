# AGENTS.md

Coding policy for Pulsar. This file is the **canonical** coding policy.
`CONTRIBUTING.md` describes the workflow (setup, branching, PR process)
and links here for style; `src/README.md` describes the module layout.
Where two files would say the same thing, this one owns it.

## The shape of the codebase

```
src/pulsar/
  cli.py            command-line interface (argparse, JSON or text output)
  __main__.py       GUI entry point
  ui/main_window.py the Tkinter application — widgets and event handlers
  utils/            everything that touches no Tkinter
    pactl_runner.py  the PulseAudio/PipeWire command layer: run + parse
    pw_app_capture.py discovery of app audio streams (pw-dump)
    pw_app_route.py  per-app routing (native PipeWire port links)
    pw_diagnostics.py the Diagnostics tab's graph snapshot
    profile_manager.py / preset_manager.py / paths.py  saved state on disk
    device_names.py  device-name classification
```

The rule that shapes everything: **`utils/` must not import Tkinter.**
That is what makes it testable with no display and no audio server, which
is where nearly all the logic lives. Put new parsing and classification
in `utils/`, not in `ui/`.

### Trust boundaries

There are exactly three places where data from outside the process
enters, and each has one owner:

| Boundary | Owner | Notes |
|---|---|---|
| `pactl` / `pw-dump` / `pw-link` stdout | the parse functions in `utils/` | subprocess output is untrusted: it can be truncated, absent, or a different format on another version |
| User-typed text (the Create form, CLI arguments) | the handler that reads the widget / argparse | validated at the point of entry |
| Files on disk (profiles, presets, paths) | `profile_manager.py`, `preset_manager.py`, `paths.py` | also a **state** boundary: ownership and concurrent writes |

Everything past those points is internal and may assume its shape.

## Coding rules

### Validate at the boundary, then trust inward

Validate unknown data where it enters, and pass the validated value
inward. Do not repeat an equivalent check in every internal caller.

Before removing a check, **prove the upstream guarantee**. A type
annotation is not runtime validation — `def f(x: str)` does not stop
`x` being `None` at runtime. If the upstream guarantee is a type hint
alone, the check stays.

The parse functions in `utils/` are the boundary for subprocess output.
Normalise there, once: parse into a dict with the keys the rest of the
code expects, and let the UI layer use them directly rather than
re-deriving them.

### Keep checks that guard a different thing

Not all checks are redundant. These are load-bearing and must survive a
"simplification":

- **Separate trust boundaries.** A check on `pactl` output and a check
  on a user's typed sink name are different checks. Merging or dropping
  one because the other exists is wrong.
- **Changing state.** Ownership of a file being replaced, authorisation,
  revision/concurrency on a read-modify-write. A file can be swapped
  between your check and your write.

### Extract only for a reason

Inline straightforward single-use logic. Extract a helper when it is
reused, when it names a meaningful concept, or when it isolates a
complex boundary (a subprocess call, a file write, a graph mutation).

Both directions are mistakes. A helper used once that just renames two
lines is noise; a 300-line function is a bug waiting to happen.

### Do not swallow errors

Remove unnecessary `try`/`except` wrappers, silent swallowing, and
fallback values that mislead. Keep narrow catches where they buy
recovery, cleanup, or a genuinely more useful error message.

Preserve framework control-flow exceptions. `cli.main()` catches
`SystemExit` and translates it to an exit code — that is a deliberate,
documented translation, not a swallow.

The failure mode to watch for in this codebase: a function that returns
`[]`, `None`, `0` or `False` on error, where the caller cannot tell
"the command failed" from "there was nothing there". The caller then
reports success. If a caller cannot distinguish the two, the boundary
must raise instead of returning an empty value.

### Separate success, failure, and uncertainty

Three states, not two:

1. **Success** — the operation did what was asked.
2. **Confirmed failure** — it did not, and we know why.
3. **Uncertain** — it may have completed, or partially completed.

Do not report success after a failure. Do not retry a write that may
have completed without reconciling first — re-running a create can
leave two devices where the user asked for one.

When an operation can be uncertain, say so in the return value rather
than collapsing it into `False`.

### The smallest clear solution

Prefer the smallest clear solution to a *demonstrated* problem. No
speculative defensive layers, no generic frameworks, no unrelated
refactors bundled into a fix.

When reviewing, sort each finding into one of three buckets and say
which:

- **Demonstrated bug** — wrong behaviour, traced to a concrete failure.
- **Justified simplification** — provably redundant or obsolete, with
  the proof (e.g. "no caller remains").
- **Stylistic preference** — optional; not a reason to change code.

## Things specific to this codebase

These are not general style. They are properties of the audio server
that will bite anyone who forgets them.

### The audio server has traps

- **Module ids are recycled.** `pactl unload-module <id>` removes
  whatever holds that id *now*. Identify a module by its identity
  (its argument string) and read the id at the moment of the unload.
  Holding an id across time removes someone else's device.
- **Sink-input indices are recycled** for the same reason. Look the
  index up immediately before moving a stream; never cache it.
- **A sink's `.monitor` carries every stream on that sink.** It cannot
  address one app. Per-app routing needs per-app primitives.
- **The session manager owns the link to a stream's default sink.**
  Cutting that link by hand leaves the session manager thinking the
  stream is still there, so a later move to the same sink becomes a
  no-op. Let the session manager replace it.
- **`pw-link -l` prints the LINK id first on a link line**, not a port
  id. The port id comes after the arrow. Getting this wrong silently
  produces wrong links.

### Tests

- `utils/` is testable with no display and no audio server. Keep it
  that way.
- For a UI handler, `object.__new__(MainWindow)` gives a bare instance
  with no Tk — enough when the handler only touches attribute state.
- **Use real Tk widgets when the event delivery is what you are
  testing.** Tk does not deliver `<<ListboxSelect>>` to an unmapped
  widget, and a programmatic `selection_set()` does not fire it at all.
  A test that sets a selection and expects the handler to have run is
  testing nothing.
- **A modal `messagebox` blocks forever under Xvfb.** Stub it in any
  test that can reach one.
- The **command of record is `./tests/run_ci_equivalent.sh -q`**, not
  `make test`. It hides the audio tools so the run matches CI. A
  matching run reports `298 passed, 164 skipped`.
- The CI workflow asserts that the display run executes *more* tests
  than the headless run. If a test module loses its skip guard, the
  suite silently skips and the job goes green having verified nothing.

### Proving a change works

- A test that passes on the first run proves nothing. For a bug fix,
  revert **only the source change** and watch the test go red, then
  restore it. Record the result in the commit message.
- Assert on the **arguments** a mock was called with, not just that it
  was called. `assert_called_once()` survives a mutation that passes
  the wrong value.
- When a fix changes a contract, update the tests that pinned the old
  one and say so — do not delete them silently.
- Measure the effect, not just the presence of it. A sink listing
  proves a device exists, not that the right audio reached it.

## Changing the guidance

If a rule here is wrong or redundant, change it here and update the
links. `CONTRIBUTING.md` and `src/README.md` point at this file for
policy; they should not restate it.
