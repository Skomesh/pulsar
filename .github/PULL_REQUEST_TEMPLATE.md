<!--
Keep this short and skimmable. Every item should be a checkbox so reviewers
can see at a glance what a contributor forgot.
-->

## What this changes

<!-- One or two sentences. Link the issue it closes: Closes #123 -->

## Why

<!-- The problem this solves, not a restatement of the diff. -->

## How it was verified

<!--
Be specific. "Run these commands, paste the output" is useful;
"tested locally" is not.
-->

- [ ] `./tests/run_ci_equivalent.sh -q` — suite result:
- [ ] `ruff check src/` — result:

## Checklist

- [ ] Tests added or updated for this change
- [ ] `./tests/run_ci_equivalent.sh -q` passes with no new failures
      (baseline is **8 pre-existing failures** in `tests/test_pw_app_capture.py`,
      all from the `gdbus` dependency — those are expected)
- [ ] `ruff check src/` clean
- [ ] No new 3.9+ syntax; CI matrix includes Python 3.8
- [ ] No secrets, personal paths, or hostnames in code or fixtures

## Notes for reviewers

<!-- Optional. Flag anything you're unsure about. -->

> Note: CI runs on `ubuntu-latest` with a real `gdbus`, so the 8 failures
> above do not appear there. Local numbers will differ from CI — this is
> expected and is why the CI-equivalent runner exists.