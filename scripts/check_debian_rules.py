#!/usr/bin/env python3
"""Validate debian/rules without make installed.

`bash -n debian/rules` is meaningless: the file is a Makefile, so its
targets and tab-indented recipes are not bash and the check fails on
valid input. `make -n` is the right check but make is not installed here.

So this does three things instead:

1. Structural makefile validation -- targets, tabs, continuations,
   variable references.
2. Extracts every recipe body and runs `bash -n` on it, which IS a real
   check: the shell inside each recipe must be valid.
3. Confirms the shebang and that the file is executable, which debhelper
   requires.
"""
import os
import re
import subprocess
import sys
import tempfile

RULES = "debian/rules"


def logical_lines(text):
    """Join backslash continuations, keep (lineno, text)."""
    out = []
    buf = ""
    start = 0
    for i, raw in enumerate(text.splitlines(), 1):
        if not buf:
            start = i
        if raw.rstrip().endswith("\\"):
            buf += raw.rstrip()[:-1]
            continue
        buf += raw
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def main():
    problems = []
    text = open(RULES).read()
    lines = logical_lines(text)

    # --- structural make checks -------------------------------------
    targets = []
    recipes = []
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("\t"):
            recipes.append((lineno, line))
            continue
        if re.match(r"^[A-Za-z0-9_.\-/]+:", line):
            name = line.split(":", 1)[0]
            # Not a variable assignment; a real target.
            if not re.match(r"^\s*=", line):
                targets.append((lineno, name))
        elif re.match(r"^\s*export\s+[A-Za-z0-9_]+", line):
            pass  # GNU make export directive, e.g. export VAR = value
        elif re.match(r"^\s*%(\.[A-Za-z0-9_%]+)*\s*:", line):
            pass  # pattern rule / debhelper catch-all, e.g. %:
        elif "=" in line and not line.startswith("\t"):
            if not re.match(r"^[A-Za-z0-9_.\-/]+\s*[:?+]?=", line):
                problems.append(
                    f"line {lineno}: ambiguous make line: {stripped[:60]}"
                )
        else:
            problems.append(
                f"line {lineno}: unrecognised make line: {stripped[:60]}"
            )

    if not targets:
        problems.append("no make targets found")
    if not recipes:
        problems.append("no recipe lines found")

    # shebang + mode
    if not text.startswith("#!/usr/bin/make -f\n"):
        problems.append(
            f"bad or missing debhelper shebang (got {text.splitlines()[0]!r})"
        )
    if not os.access(RULES, os.X_OK):
        problems.append("debian/rules is not executable (debhelper requires 0755)")

    # --- shell inside each recipe ------------------------------------
    bodies = []
    for _lineno, line in recipes:
        body = line[1:].replace("$$", "$")
        bodies.append(body)
    script = "\n".join(bodies)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        res = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        if res.returncode != 0:
            problems.append(
                "recipe shell is not valid bash:\n" + res.stderr[:600]
            )
    finally:
        os.unlink(path)

    # --- unbalanced constructs ---------------------------------------
    for name, opener, closer in (
        ("shell if", "if ", "fi"),
        ("shell for", "for ", "done"),
        ("case", "case ", "esac"),
    ):
        opens = sum(
            1 for _, line in recipes
            if re.match(r"\s*@?\s*" + opener, line.lstrip("\t"))
        )
        closes = sum(
            1 for _, line in recipes
            if re.match(r"\s*@?\s*" + closer, line.lstrip("\t"))
        )
        if opens != closes and closes:
            problems.append(f"{name}: {opens} open vs {closes} close")

    print(f"targets found ({len(targets)}):")
    for lineno, name in targets:
        print(f"  {lineno:>4}  {name}")
    print(f"\nrecipe lines: {len(recipes)}")
    print(f"file mode:    {oct(os.stat(RULES).st_mode)[-3:]}")

    print()
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("debian/rules: structurally valid, recipe shell parses as bash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
