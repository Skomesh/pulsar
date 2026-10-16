#!/usr/bin/env python3
"""Actually RUN the build-commands from both recipes in a scratch tree.

Syntax-checking the shell is weak: it proves `sh -n` is happy, not that the
heredoc un-indents correctly, that the desktop file lands where the manifest
says, or that Exec= survived. This harness executes the real fragments with
$CRAFT_PART_INSTALL / $FLATPAK_DEST / $CRAFT_PROJECT_DIR pointed at temp dirs
and then inspects the resulting tree.

Run: /usr/bin/python3 packaging/simulate_build.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
failures = []


def check(cond, msg, detail=""):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        if detail:
            print(f"       {detail}")
        failures.append(msg)


def run(script, env, cwd=None):
    proc = subprocess.run(["/bin/sh", "-c", script], env=env, cwd=cwd,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"       script exited {proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc


def indent_strip(text):
    """flatpak/yaml block scalars keep the block indent; shell heredocs inside
    them are written flush-left by convention. Detect if that is violated."""
    return text


# ---------------------------------------------------------------- snap
def simulate_snap():
    print("\n== snap: execute parts for real")
    manifest = yaml.safe_load((REPO / "packaging/snap/snapcraft.yaml").read_text())
    parts = manifest["parts"]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        install = tmp / "install"
        install.mkdir()
        env = dict(os.environ)
        env["CRAFT_PART_INSTALL"] = str(install)
        env["CRAFT_PROJECT_DIR"] = str(REPO / "packaging" / "snap")

        for part_name in ("desktop-launch", "desktop-entry"):
            part = parts[part_name]
            script = part.get("override-build", "")
            print(f"  -- part {part_name}")
            if not script.strip():
                continue
            # snapcraft runs the whole override-build block as ONE shell
            # script with the part's build directory as cwd. Splitting it
            # line-by-line would tear apart heredocs and for-loops, so run it
            # verbatim.
            part_cwd = tmp / f"cwd-{part_name}"
            part_cwd.mkdir()
            proc = subprocess.run(["/bin/sh", "-c", script], env=env,
                                  cwd=part_cwd, capture_output=True, text=True)
            check(proc.returncode == 0,
                  f"{part_name}: override-build block succeeded",
                  proc.stderr.strip()[:600])

        # --- inspect the primed tree ---
        # the launcher wrapper must actually redirect to the HOST pulse socket
        launcher = install / "snap/bin/desktop-launch"
        check(launcher.is_file() and launcher.stat().st_mode & 0o111,
              "primed snap/bin/desktop-launch exists and is executable")
        if launcher.is_file():
            # Build a fake host runtime dir with a unix socket at the
            # pulse/native path, and a snap-private runtime dir beside it.
            # This is the real-world snapd layout:
            #   /run/user/<uid>/pulse/native            <- host socket
            #   /run/user/<uid>/snap.pulsar/current/    <- snap's XDG_RUNTIME_DIR
            import socket
            # Mirror the REAL snapd layout observed on a live system:
            #   XDG_RUNTIME_DIR = /run/user/<uid>
            #   host socket      = /run/user/<uid>/pulse/native
            # For a snap, XDG_RUNTIME_DIR becomes
            #   /run/user/<uid>/snap.<name>/<instance>
            # so "$XDG_RUNTIME_DIR/../.." is the /run/user/<uid> level.
            # We build that shape under a temp root and point XDG_RUNTIME_DIR
            # at the snap-private dir.
            uid = os.getuid()
            host_run = tmp / f"run-user-{uid}"
            snap_rt = host_run / "snap.pulsar" / "current"
            snap_rt.mkdir(parents=True)
            sock_path = host_run / "pulse"
            sock_path.mkdir()
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(sock_path / "native"))
            # also a native pipewire socket inside the snap runtime dir
            pw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            pw.bind(str(snap_rt / "pipewire-0"))

            probe = tmp / "probe.sh"
            probe.write_text("#!/bin/sh\n"
                             'echo "PULSE_SERVER=$PULSE_SERVER"\n'
                             'echo "PIPEWIRE_REMOTE=$PIPEWIRE_REMOTE"\n'
                             'echo "XDG_DATA_DIRS=$XDG_DATA_DIRS"\n')
            probe.chmod(0o755)
            penv = dict(os.environ)
            penv["XDG_RUNTIME_DIR"] = str(snap_rt)
            penv["SNAP"] = str(install)
            pres = subprocess.run([str(launcher), str(probe)], env=penv,
                                  capture_output=True, text=True)
            out = pres.stdout
            # Accept the launcher resolving to ANY of the three candidate
            # paths, as long as it lands on a real pulse socket and NOT on the
            # snap-private runtime dir (which is the bug this guards against).
            import re as _re
            m = _re.search(r"^PULSE_SERVER=unix:(.+)$", out, _re.M)
            got = m.group(1).strip() if m else ""
            check(got.endswith("pulse/native"),
                  "launcher exports PULSE_SERVER pointing at a HOST pulse socket",
                  out.strip())
            check("snap.pulsar" not in got,
                  "PULSE_SERVER does NOT point into the snap-private runtime dir",
                  got)
            check("PIPEWIRE_REMOTE=" in out and "pipewire-0" in out,
                  "launcher exports PIPEWIRE_REMOTE for the native pipewire socket",
                  out.strip())
            check(f"XDG_DATA_DIRS={install}/usr/share" in out,
                  "launcher prepends $SNAP/usr/share to XDG_DATA_DIRS", out.strip())
            sock.close()
            pw.close()

        desktop = install / "usr/share/applications/pulsar.desktop"
        check(desktop.is_file(), "primed pulsar.desktop exists at the path apps.desktop names")
        if desktop.is_file():
            text = desktop.read_text()
            check("Exec=pulsar" in text, "primed desktop file has Exec=pulsar")
            check("Icon=pulsar" in text, "primed desktop file has Icon=pulsar")
            check("PLACEHOLDER_EXEC_PATH" not in text,
                  "primed desktop file has no PLACEHOLDER_EXEC_PATH")
            check(text.startswith("[Desktop Entry]"), "desktop file starts with [Desktop Entry]")
            # snapcraft's desktop_file parser uses configparser with strict
            # = signs; a duplicated key would abort a real build.
            keys = re.findall(r"^([A-Za-z0-9-]+)=", text, re.M)
            dupes = {k for k in keys if keys.count(k) > 1}
            check(not dupes, f"no duplicate keys in desktop file ({sorted(dupes)})")

        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            p = install / f"usr/share/icons/hicolor/{size}x{size}/apps/pulsar.png"
            check(p.is_file() and p.stat().st_size > 0,
                  f"primed icon {size}x{size}/apps/pulsar.png")
        svg = install / "usr/share/icons/hicolor/scalable/apps/pulsar.svg"
        check(svg.is_file(), "primed scalable icon pulsar.svg")

        # source paths referenced by the manifest must resolve from the
        # packaging/snap directory
        for part_name, part in parts.items():
            src = part.get("source")
            if part.get("source-type") == "local" and src:
                resolved = (REPO / "packaging" / "snap" / src).resolve()
                check(resolved.is_dir(),
                      f"part {part_name}: local source resolves to a directory",
                      str(resolved))
                check((resolved / "src" / "pulsar" / "__main__.py").is_file(),
                      f"part {part_name}: source contains src/pulsar/__main__.py")
                check((resolved / "pyproject.toml").is_file(),
                      f"part {part_name}: source contains pyproject.toml")
            if part.get("icon"):
                check((REPO / "packaging" / "snap" / part["icon"]).is_file(),
                      f"part {part_name}: icon path {part['icon']} resolves")

        # top-level `icon:` is relative to the project dir
        check((REPO / "packaging" / "snap" / manifest["icon"]).is_file(),
              f"top-level icon {manifest['icon']} resolves from project dir")


# ---------------------------------------------------------------- flatpak
def simulate_flatpak(rel, label):
    print(f"\n== flatpak ({label}): execute build-commands for real")
    path = REPO / rel
    manifest = yaml.safe_load(path.read_text())

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        dest = tmp / "app"
        dest.mkdir()
        env = dict(os.environ)
        env["FLATPAK_DEST"] = str(dest)

        for module in manifest["modules"]:
            name = module["name"]
            if name != "pulsar":
                continue
            print(f"  -- module {name}")
            for cmd in module["build-commands"]:
                # The manifest hardcodes /app, which is what flatpak-builder
                # mounts as FLATPAK_DEST. We cannot create /app here, so
                # substitute the temp dir for the simulation only. This is a
                # harness concern, not a recipe one.
                #
                # Only rewrite path PREFIXES (a token starting a path), never
                # a bare "/app" substring, or we would corrupt the app-id and
                # the PYTHONPATH value inside the generated launcher.
                simulated = re.sub(r"(?<![\w./-])/app(?=[/\s\"']|$)", str(dest), cmd)
                proc = run(simulated, env, cwd=REPO)
                check(proc.returncode == 0,
                      f"`{cmd.strip().splitlines()[0][:60]}` succeeded",
                      proc.stderr)

        app_id = manifest["app-id"]
        desktop = dest / f"share/applications/{app_id}.desktop"
        check(desktop.is_file(), f"primed {app_id}.desktop exists")
        if desktop.is_file():
            text = desktop.read_text()
            check("Exec=pulsar" in text, "primed desktop file has Exec=pulsar")
            check(f"Icon={app_id}" in text, f"primed desktop file has Icon={app_id}")
            check("PLACEHOLDER_EXEC_PATH" not in text,
                  "primed desktop file has no PLACEHOLDER_EXEC_PATH")

        # the launcher
        launcher = dest / "bin/pulsar"
        check(launcher.is_file(), "primed /app/bin/pulsar launcher exists")
        if launcher.is_file():
            check(launcher.stat().st_mode & 0o111, "launcher is executable")
            body = launcher.read_text()
            check("PYTHONPATH" in body and "/app" in body,
                  "launcher sets PYTHONPATH to /app (not CWD-dependent)")
            check("python3 -m pulsar" in body, "launcher execs python3 -m pulsar")
            # run it for real: it must fail on the missing module, not on a
            # shell error, which proves PYTHONPATH/export ordering is right.
            proc = subprocess.run(["/bin/sh", str(launcher)],
                                  env={**env, "PYTHONPATH": ""},
                                  capture_output=True, text=True, cwd="/")
            combined = proc.stdout + proc.stderr
            check("No module named pulsar" in combined,
                  "launcher runs and fails on the (expected) missing module, not a shell error",
                  combined.strip()[:200])

        # source tree copied
        check((dest / "pulsar" / "__main__.py").is_file(),
              "app package copied to /app/pulsar")
        check((dest / "pulsar" / "utils" / "presets" / "builtin_profiles.json").is_file(),
              "builtin_profiles.json shipped next to the code (package-data)")
        check((dest / "pulsar" / "ui" / "main_window.py").is_file(),
              "subpackage ui copied")
        check((dest / "pulsar" / "config" / "__init__.py").is_file(),
              "subpackage config copied")

        # icons
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            p = dest / f"share/icons/hicolor/{size}x{size}/apps/{app_id}.png"
            check(p.is_file() and p.stat().st_size > 0, f"primed icon {size}x{size}")
        check((dest / f"share/icons/hicolor/scalable/apps/{app_id}.svg").is_file(),
              "primed scalable svg icon")

        # source dir path resolves relative to the manifest
        for module in manifest["modules"]:
            for source in module.get("sources", []):
                if source.get("type") == "dir":
                    resolved = (path.parent / source["path"]).resolve()
                    check(resolved.is_dir(),
                          f"module {module['name']}: source dir {source['path']} resolves",
                          str(resolved))
                    check((resolved / "src" / "pulsar" / "__main__.py").is_file(),
                          f"module {module['name']}: source dir contains src/pulsar")


def main():
    print("=" * 70)
    print("Pulsar packaging recipes: simulated build")
    print("=" * 70)
    simulate_snap()
    simulate_flatpak("packaging/flatpak/pulsar.yml", "stable")
    simulate_flatpak("packaging/flatpak/net/linux-unstable/pulsar.yml", "net/linux-unstable")

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SIMULATED BUILD OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())