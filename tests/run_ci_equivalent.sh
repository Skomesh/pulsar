#!/usr/bin/env bash
# Run the test suite the way a GitHub ubuntu-latest runner does: with pactl,
# pw-dump, pw-record, pw-top and pw-link hidden, so the suite's own
# shutil.which() guards take the "not available" branch, AND made to fail
# if anything shells out to them, so a call to them fails exactly as it
# does on a runner that has neither.
#
# The sandbox has these tools installed but no audio daemon, so a plain
# pytest run reports failures that CI never sees. This script is the
# honest baseline.
#
# gdbus is deliberately NOT hidden. ubuntu-latest ships libglib2.0-bin, so
# the runner HAS it, and tests/test_pw_app_capture.py's portal tests need it
# present to reach the code under test. Hiding it produced 8 failures here
# that do not exist on CI -- the mirror image of the problem this script
# exists to solve.
#
# Verified against a simulated runner that has every system binary except
# the five audio tools: 362 passed, 175 skipped, 0 failed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHADOW_DIR="$(mktemp -d)"
trap 'rm -rf "$SHADOW_DIR"' EXIT

cat > "$SHADOW_DIR/sitecustomize.py" <<'PYEOF'
import shutil

_real = shutil.which
# pw-link is hidden too: it ships in the same package as pw-dump
# (pipewire-bin / pipewire-utils / pipewire), so a runner that has one
# has the other. Leaving it visible means a dev machine with pipewire
# installed takes a different branch from CI, which has it absent.
HIDE = {'pactl', 'pw-dump', 'pw-record', 'pw-top', 'pw-link'}


def which(cmd, *args, **kwargs):
    return None if cmd in HIDE else _real(cmd, *args, **kwargs)


shutil.which = which
PYEOF

# The stubs below make the hidden tools FAIL, not merely invisible.
#
# Hiding them from shutil.which() alone was not enough: the binaries
# were still on PATH, so a subprocess call to them still SUCCEEDED and
# returned real output. A dev machine with pipewire installed therefore
# took a different path through the code than a CI runner, which has
# neither tool. Seven routing tests passed here and would have failed
# on CI. These stubs exit 127 with the shell's own "command not found",
# so a call to them fails exactly as it does on a runner without them.
for tool in pactl pw-dump pw-record pw-top pw-link; do
    cat > "$SHADOW_DIR/$tool" <<EOF
#!/bin/sh
echo "$tool: command not found" >&2
exit 127
EOF
    chmod +x "$SHADOW_DIR/$tool"
done

cd "$REPO_ROOT"

# Extra args are forwarded to pytest verbatim. An arg that names a test
# target — an existing path, a .py file, or a node id like file::Class::test —
# is pulled out and REPLACES the default tests/, so
# `./tests/run_ci_equivalent.sh -q tests/test_pactl_runner.py` really does
# filter to one file. (pytest unions positional args, so passing tests/
# alongside a file would collect the whole suite anyway.) Requiring the
# target shape keeps flag *values* like `-p no:cacheprovider` from being
# mistaken for one; with no target given we fall back to tests/.
targets=()
rest=()
for arg in "$@"; do
    case "$arg" in
        -*) rest+=("$arg") ;;
        *)
            case "$arg" in
                *.py|*.py::*|tests|tests/*|src|src/*)
                    if [ "${#targets[@]}" -eq 0 ]; then
                        targets=("$arg")
                    else
                        rest+=("$arg")
                    fi
                    ;;
                *) rest+=("$arg") ;;
            esac
            ;;
    esac
done
if [ "${#targets[@]}" -eq 0 ]; then
    targets=(tests/)
fi

PYTHONPATH="$SHADOW_DIR" PATH="$SHADOW_DIR:$PATH" \
    python3 -m pytest "${targets[@]}" "${rest[@]}"