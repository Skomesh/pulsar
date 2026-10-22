#!/usr/bin/env python3
"""Extract every shell script embedded in the packaging recipes and run
`/bin/sh -n` on it.

A YAML file can be perfectly valid while the shell fragments inside it are
not. This catches heredoc quoting mistakes, which are the most likely
hand-authoring error in these recipes.

Run: /usr/bin/python3 packaging/check_shell_fragments.py
"""
import pathlib
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
FILES = [
    "packaging/snap/snapcraft.yaml",
    "packaging/flatpak/pulsar.yml",
    "packaging/flatpak/net/linux-unstable/pulsar.yml",
]

failures = []


def collect(node, out, trail=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("override-build", "override-pull") and isinstance(v, str):
                out.append((f"{trail}.{k}", v))
            elif k == "build-commands":
                if isinstance(v, str):
                    out.append((f"{trail}.build-commands", v))
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        if isinstance(item, str):
                            out.append((f"{trail}.build-commands[{i}]", item))
            else:
                collect(v, out, f"{trail}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            collect(item, out, f"{trail}[{i}]")


def main():
    for rel in FILES:
        path = REPO / rel
        print(f"\n== {rel}")
        scripts = []
        collect(yaml.safe_load(path.read_text()), scripts)
        print(f"  {len(scripts)} shell fragment(s)")
        for where, script in scripts:
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
                fh.write(script)
                tmp = fh.name
            proc = subprocess.run(["/bin/sh", "-n", tmp],
                                  capture_output=True, text=True)
            os_status = "ok  " if proc.returncode == 0 else "FAIL"
            print(f"  {os_status} sh -n {where}")
            if proc.returncode != 0:
                failures.append(f"{rel} {where}: {proc.stderr.strip()}")
                print(f"       {proc.stderr.strip()}")
            pathlib.Path(tmp).unlink(missing_ok=True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} shell fragment(s) with syntax errors")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL SHELL FRAGMENTS PARSE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
