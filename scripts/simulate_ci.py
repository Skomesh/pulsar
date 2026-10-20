#!/usr/bin/env python3
"""Simulate CI's install-and-verify chain against a real wheel.

Why this exists: moving the app into a `pulsar` package left three separate
stale assertions in .github/workflows/ci.yml -- `main.py` in the wheel,
`import main` in the clean-venv smoke test, `from utils.profile_manager`
in the preset-data check. Each passed code review and each only failed when
CI actually ran, one push at a time.

This walks the same chain locally so the next one surfaces here instead.

Usage:
    python3 scripts/simulate_ci.py          # from the repo root

Exit code is 0 only if every step passes, so it can be wired into a
pre-push hook or a Makefile target.
"""
import ast
import re
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")

#: Top-level module names the app used before it moved into a `pulsar`
#: package. A bare import of any of these anywhere is a leftover.
OLD_LAYOUT = {"utils", "ui", "config", "main"}

failures = []


def fail(step, msg):
    failures.append(f"[{step}] {msg}")
    print(f"  ::error::{step}: {msg}")


def step(name):
    print(f"\n--- {name} ---")


def check_wheel(wheel):
    step(f"1. wheel contents ({wheel.name})")
    z = zipfile.ZipFile(wheel)
    names = z.namelist()

    entry = [n for n in names if n.endswith("entry_points.txt")]
    if not entry:
        fail("wheel", "declares no entry point")
    else:
        print("  entry_points:",
              z.read(entry[0]).decode().strip().replace("\n", " | "))

    if not any(n == "pulsar/__main__.py" for n in names):
        fail("wheel", "pulsar/__main__.py missing")
    if not any(n == "pulsar/cli.py" for n in names):
        fail("wheel", "pulsar/cli.py missing")

    tl = [n for n in names if n.endswith("top_level.txt")]
    if not tl:
        fail("wheel", "no top_level.txt")
    else:
        top = z.read(tl[0]).decode().split()
        print("  top_level:", top)
        if top != ["pulsar"]:
            fail(
                "wheel",
                f"top_level.txt is {top}, expected ['pulsar']; generic "
                "top-level modules collide in distro installs",
            )

    data = [n for n in names if n.endswith(".json")]
    if not data:
        fail("wheel", "no preset .json shipped")
    else:
        print("  preset data:", data)


def check_install(wheel):
    step("2. install into a clean venv")
    root = tempfile.mkdtemp(prefix="cisim-")
    venv = str(Path(root) / "smoke")
    subprocess.run([sys.executable, "-m", "venv", venv], check=True)
    subprocess.run(
        [f"{venv}/bin/pip", "install", "--quiet", str(wheel)],
        check=True, capture_output=True,
    )
    print("  installed into", venv)

    step("3. console entry point importable (outside the source tree)")
    r = subprocess.run(
        [f"{venv}/bin/python", "-c",
         "from pulsar.__main__ import main as gui_main\n"
         "from pulsar.cli import cli_main\n"
         "assert callable(gui_main), 'GUI entry point missing'\n"
         "assert callable(cli_main), 'CLI entry point missing'\n"
         "print('console entry point importable: OK')"],
        cwd="/tmp", capture_output=True, text=True,
    )
    print("  " + (r.stdout or "").strip())
    if r.returncode != 0:
        fail("entry point", (r.stderr or "").strip()[-400:])

    step("4. built-in profiles load from the installed package")
    r = subprocess.run(
        [f"{venv}/bin/python", "-c",
         "import tempfile\n"
         "from pulsar.utils.profile_manager import ProfileManager\n"
         "pm = ProfileManager(presets_dir=tempfile.mkdtemp())\n"
         "n = len(pm._load_builtin())\n"
         "assert n > 0, 'installed package loaded ZERO built-in profiles'\n"
         "print(f'built-in profiles load from installed wheel: {n}')"],
        cwd="/tmp", capture_output=True, text=True,
    )
    print("  " + (r.stdout or "").strip())
    if r.returncode != 0:
        fail("preset data", (r.stderr or "").strip()[-400:])

    step("5. no stale pre-move imports in ci.yml")
    stale = set()
    for block in re.findall(r'-c "\n(.*?)\n\s*"', WORKFLOW.read_text(), re.S):
        try:
            tree = ast.parse(textwrap.dedent(block))
        except SyntaxError:
            continue  # carries the workflow's own indentation
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in OLD_LAYOUT:
                        stale.add(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if not node.level and node.module:
                    if node.module.split(".")[0] in OLD_LAYOUT:
                        stale.add(f"from {node.module} import ...")
    if stale:
        fail("ci.yml", f"stale imports of the old layout: {sorted(stale)}")
    else:
        print("  none -- every import is namespaced under pulsar")

    step("6. bundled preset JSON parses")
    for path in sorted(Path("src/pulsar/utils/presets").glob("*.json")):
        try:
            import json
            json.loads(path.read_text())
            print(f"  valid: {path}")
        except ValueError as exc:
            fail("preset json", f"{path}: {exc}")


def main():
    print("=" * 62)
    print("CI simulation")
    print("=" * 62)
    if not WORKFLOW.exists():
        print(f"{WORKFLOW} not found -- run from the repo root")
        return 2

    build_dir = tempfile.mkdtemp(prefix="cibuild-")
    r = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", build_dir],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        fail("build", "python -m build failed")
        return 1

    wheels = sorted(Path(build_dir).glob("*.whl"))
    if not wheels:
        fail("build", "no wheel produced")
        return 1

    check_wheel(wheels[0])
    check_install(wheels[0])

    print("\n" + "=" * 62)
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("ALL CI SIMULATION STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
