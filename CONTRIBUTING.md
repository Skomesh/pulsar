# Contributing to Pulsar

## Welcome

Pulsar is a fork of [pactl-gui](https://github.com/Skrappjaw/pactl-gui) by
Skrappjaw, focused on streamers, gamers, and anyone who needs quick control
over PulseAudio / PipeWire sources and sinks without leaving their fullscreen
app. Contributions of all sizes are welcome: bug reports, patches, docs,
and tests.

## Development setup

Requirements:

- Python 3.6 or newer
- Tkinter (usually bundled with Python; on Debian/Ubuntu install `python3-tk`)
- `pactl` (part of PulseAudio or the PipeWire `pulse-cli-utils` package)

Verify your environment:

```sh
pactl info
pactl version
python3 --version
```

Get the source and run it:

```sh
git clone https://github.com/Skomesh/pulsar.git
cd pulsar
./install.sh
# or, for a no-install run:
python3 src/main.py
```

## Running tests

Run the test suite from the project root:

```sh
make test
# or directly:
python3 -m pytest tests/
```

If you add a test that needs fixtures or sample `pactl` output, drop them in
`tests/fixtures/` and document them in the test's docstring.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Use type hints where reasonable, especially on public functions and
  module boundaries. Internal helpers can be lighter.
- Prefer f-strings, `pathlib.Path`, and `subprocess.run()` with explicit
  `check=` / `capture_output=`.
- Keep modules small and focused. Avoid circular imports between
  `src/ui`, `src/utils`, and `src/config`.
- No third-party runtime dependencies unless absolutely necessary;
  the goal is a lightweight tool that runs on a stock Python install.

## Pull request process

- Branch from `main`.
- Use a descriptive branch name with one of these prefixes:
  - `feat/...` for new features
  - `fix/...` for bug fixes
  - `refactor/...` for code changes with no behavior change
  - `docs/...` for documentation only
  - `test/...` for test-only changes
- One concern per PR. If your change touches two unrelated things, split it.
- Ensure `make test` passes locally before requesting review.
- Update the README or relevant docs if your change is user-visible.
- Fill in the PR description: what changed, why, and how you tested it.

## Commit messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

<body explaining the why, wrapped at ~72 chars>

<footer with references, e.g. Closes #12>
```

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`.
Write the subject in the imperative mood ("add", not "added").

## Reporting issues

When filing a bug, please include:

- Your OS and distro version
- Whether you are on PulseAudio or PipeWire (output of `pactl info` will
  show this, typically the `Server Name` line)
- Output of `pactl version`
- Output of `pactl list short sinks` and `pactl list short sources`
  (or the relevant subset)
- Steps to reproduce, expected vs. actual behavior, and any error output
- Pulsar version or commit hash

PipeWire compatibility is exercised, but PulseAudio quirks still
surface. The more context you provide, the faster a fix lands.

## Questions

For usage questions, open a discussion on the GitHub Discussions page
rather than an issue. For security issues, do not file a public issue;
contact the maintainer directly via the address listed in the repository
profile.
