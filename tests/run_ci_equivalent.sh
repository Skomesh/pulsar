#!/usr/bin/env bash
# Run the test suite the way a GitHub ubuntu-latest runner does: with pactl,
# pw-dump, pw-record and pw-top hidden, so the suite's own shutil.which()
# guards take the "not available" branch.
#
# The sandbox has these tools installed but no audio daemon, so a plain
# pytest run reports 21 failures that CI never sees. This script is the
# honest baseline.
#
# gdbus is deliberately NOT hidden. ubuntu-latest ships libglib2.0-bin, so
# the runner HAS it, and tests/test_pw_app_capture.py's portal tests need it
# present to reach the code under test. Hiding it produced 8 failures here
# that do not exist on CI -- the mirror image of the problem this script
# exists to solve. Verified: hiding only pactl/pw-* gives 75 passed,
# 53 skipped, 0 failed, which matches the green run on this branch.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHADOW_DIR="$(mktemp -d)"
trap 'rm -rf "$SHADOW_DIR"' EXIT

cat > "$SHADOW_DIR/sitecustomize.py" <<'PYEOF'
import shutil

_real = shutil.which
HIDE = {'pactl', 'pw-dump', 'pw-record', 'pw-top'}


def which(cmd, *args, **kwargs):
    return None if cmd in HIDE else _real(cmd, *args, **kwargs)


shutil.which = which
PYEOF

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

PYTHONPATH="$SHADOW_DIR" python3 -m pytest "${targets[@]}" "${rest[@]}"