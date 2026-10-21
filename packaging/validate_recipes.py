#!/usr/bin/env python3
"""Validate the packaging recipes.

Three levels of checking:
  1. YAML syntax round-trip (parse -> dump -> parse), plus duplicate-key
     detection, which plain yaml.safe_load silently tolerates.
  2. snapcraft.yaml against Canonical's published JSON schema.
  3. Cross-checks that every command / package / path referenced by the
     recipes exists in the packaging context.

Run: /usr/bin/python3 packaging/validate_recipes.py
"""
import json
import pathlib
import re
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
SNAP = REPO / "packaging" / "snap" / "snapcraft.yaml"
FLATPAK = REPO / "packaging" / "flatpak" / "pulsar.yml"
FLATPAK_NET = REPO / "packaging" / "flatpak" / "net" / "linux-unstable" / "pulsar.yml"
SCHEMA = pathlib.Path("/tmp/snapcraft.schema.json")

failures = []
notes = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        failures.append(msg)


def load_strict(path):
    """Parse YAML, rejecting duplicate mapping keys."""
    class Dup(yaml.SafeLoader):
        pass

    def no_dup(loader, node, deep=False):
        mapping = {}
        for k, v in node.value:
            key = loader.construct_object(k, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key {key!r}", k.start_mark)
            mapping[key] = loader.construct_object(v, deep=deep)
        return mapping

    Dup.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_dup)
    return yaml.load(path.read_text(), Loader=Dup)


def yaml_checks(path):
    print(f"\n== YAML: {path.relative_to(REPO)}")
    data = load_strict(path)
    check(isinstance(data, dict), "parses as a mapping")
    check(len(data) > 0, "non-empty")
    dumped = yaml.safe_dump(data, sort_keys=False)
    reparsed = yaml.safe_load(dumped)
    check(reparsed == data, "round-trips (parse -> dump -> parse)")
    check("!!python" not in dumped, "no python-specific tags leak")
    # duplicate-key check ran inside load_strict; a duplicate would raise
    print(f"  info {len(data)} top-level keys: {', '.join(sorted(map(str, data)))}")
    return data


def schema_checks(data):
    print("\n== snapcraft.json schema")
    if not SCHEMA.exists():
        notes.append("snapcraft schema not downloaded; schema validation skipped")
        print("  skip schema file missing")
        return
    import jsonschema
    schema = json.loads(SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        for e in errors[:20]:
            path = "/".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"  FAIL {path}: {e.message[:200]}")
        failures.append(f"{len(errors)} schema violation(s)")
    else:
        check(True, "validates against canonical snapcraft.json")

    c24 = schema["$defs"]["Core24Project"]
    props = set(c24["properties"])
    for k in data:
        check(k in props, f"top-level key {k!r} is in the schema")
    check(data.get("base") == "core24", "base is core24 (discriminator branch exists)")
    missing = [k for k in c24["required"] if k not in data]
    check(not missing, f"all schema-required top-level keys present (missing: {missing})")


def snap_semantic_checks(data):
    print("\n== snap: semantic cross-checks")
    apps = data.get("apps", {})
    # The GUI app is pulsar-gui because `pulsar` is the command-line
    # interface. A single app named `pulsar` pointing at bin/pulsar would
    # hand a terminal command to desktop-launch and open no window.
    check("pulsar-gui" in apps, "a pulsar-gui app is declared")
    if "pulsar-gui" in apps:
        check(apps["pulsar-gui"].get("command") == "bin/pulsar-gui",
              "pulsar-gui runs bin/pulsar-gui, not the CLI")
    check(data.get("confinement") == "strict", "strict confinement")

    plugs = data.get("plugs", {})
    for name in ("audio-playback", "audio-record"):
        check(plugs.get(name, {}).get("interface") == name,
              f"plug {name} declares interface: {name}")

    app_plugs = apps.get("pulsar-gui", {}).get("plugs", [])
    for name in ("audio-playback", "audio-record"):
        check(name in app_plugs, f"app plugs includes {name}")

    # stage-packages: the pw-* tools live in pipewire-bin, NOT pipewire
    parts = data.get("parts", {})
    pkgs = set()
    for p in parts.values():
        pkgs |= set(p.get("stage-packages", []) or [])
    check("pipewire-bin" in pkgs, "stage-packages has pipewire-bin (provides pw-*)")
    check("pulseaudio-utils" in pkgs, "stage-packages has pulseaudio-utils (provides pactl)")
    check("python3-tk" in pkgs, "stage-packages has python3-tk (provides tkinter)")
    check("libglib2.0-bin" in pkgs, "stage-packages has libglib2.0-bin (provides gdbus)")

    # no CWD-relative data writes: the recipe must not create ./presets anywhere
    body = SNAP.read_text()
    check("Path.cwd" not in body and "os.getcwd" not in body,
          "recipe does not reintroduce CWD-relative data paths")

    # referenced icons must exist on disk
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        p = REPO / f"assets/icons/pulsar-{size}.png"
        check(p.is_file(), f"icon referenced exists: assets/icons/pulsar-{size}.png")
    check((REPO / "assets/icons/pulsar.svg").is_file(), "icon referenced exists: assets/icons/pulsar.svg")

    # desktop entry authored in the recipe
    check("Exec=pulsar-gui" in body, "desktop entry execs pulsar-gui")
    check("Icon=pulsar" in body, "desktop entry uses Icon=pulsar")
    # The generated desktop entry (inside the heredoc) must not contain the
    # placeholder. Check the heredoc body only, so an explanatory comment
    # mentioning the placeholder does not trip this.
    heredoc = re.search(r"<<'DESKTOP'\n(.*?)\n\s*DESKTOP", body, re.S)
    check(heredoc is not None, "desktop entry is emitted via a quoted heredoc")
    if heredoc:
        check("PLACEHOLDER_EXEC_PATH" not in heredoc.group(1),
              "packaged desktop entry is not the PLACEHOLDER template")

    # version is a placeholder, not a silent hardcode
    v = str(data.get("version", ""))
    check("PLACEHOLDER" in v, f"version is an explicit placeholder ({v})")

    # Pulsar ships no default profiles, so there is no preset data to
    # reach via package-data. The package-data declaration that used to
    # ship builtin_profiles.json is gone too; a package that starts
    # shipping data again must declare it here or the wheel will not
    # contain it.
    pyproject = (REPO / "pyproject.toml").read_text()
    check("package-data" not in pyproject,
          "pyproject declares no package-data")
    check(not (REPO / "src/pulsar/utils/presets").exists()
          or not list((REPO / "src/pulsar/utils/presets").glob("*.json")),
          "no default profile JSON is bundled in the package")


def flatpak_semantic_checks(data, label, path):
    print(f"\n== flatpak ({label}): semantic cross-checks")
    check(data.get("runtime", "").startswith("org.freedesktop.Platform"), "runtime is org.freedesktop.Platform")
    check(data.get("sdk", "").startswith("org.freedesktop.Sdk"), "sdk is org.freedesktop.Sdk")
    rv = data.get("runtime-version")
    # The requirement is that runtime-version is a STRING. YAML would happily
    # read an unquoted 24.08 as a float and render it back as "24.08" only by
    # luck; 'unstable' is a legitimate non-dotted value.
    check(isinstance(rv, str), f"runtime-version is a string, not a YAML number ({rv!r})")

    fa = data.get("finish-args", [])
    fa_s = set(fa)

    def has(pred, msg):
        check(any(pred(x) for x in fa), msg)

    has(lambda x: x.startswith("--filesystem=xdg-run/pulse"), "grants --filesystem=xdg-run/pulse")
    has(lambda x: x == "--socket=fallback-x11" or x == "--socket=x11", "grants an X11 socket")
    has(lambda x: x == "--socket=wayland", "grants --socket=wayland")
    has(lambda x: x == "--device=dri", "grants --device=dri")
    has(lambda x: "org.freedesktop.portal.Desktop" in x, "talks to the xdg-desktop portal")

    # no audio plug exists in flatpak; filesystem socket is the only route
    check(not any("audio-playback" in x or "audio-record" in x for x in fa),
          "no snap-style audio plug referenced (none exists in flatpak)")

    # user data: XDG_DATA_HOME is sandbox-private, no extra permission needed
    check(not any("xdg-data/pulsar" in x for x in fa),
          "no extra permission requested for $XDG_DATA_HOME/pulsar (sandbox maps it)")

    mods = data.get("modules", [])
    names = [m.get("name") for m in mods]
    check(any(n and "pulsar" in n for n in names), "app module present")

    # tkinter is absent from the runtime -> a module must provide it.
    # `path` is the manifest actually being checked, so comments quoting the
    # other variant cannot satisfy this check.
    body = path.read_text()
    check(any(m.get("name") == "tkinter" for m in mods),
          "a tkinter module is declared (runtime deletes tkinter)")

    # command
    check(data.get("command") == "pulsar", f"command is pulsar (got {data.get('command')!r})")

    # desktop file + icons installed
    check("desktop" in body and "pulsar.svg" in body, "desktop entry and svg icon referenced")
    for size in (16, 48, 128, 256):
        check(f"{size}x{size}" in body, f"png icon {size}x{size} installed")


FA_NET = FLATPAK_NET


def main():
    print("=" * 70)
    print("Pulsar packaging recipe validation")
    print("=" * 70)

    snap = yaml_checks(SNAP)
    schema_checks(snap)
    snap_semantic_checks(snap)

    if FLATPAK.exists():
        fp = yaml_checks(FLATPAK)
        flatpak_semantic_checks(fp, "stable", FLATPAK)
    else:
        notes.append("packaging/flatpak/pulsar.yml not found")

    if FLATPAK_NET.exists():
        fpn = yaml_checks(FLATPAK_NET)
        flatpak_semantic_checks(fpn, "net/linux-unstable", FLATPAK_NET)
    else:
        notes.append("packaging/flatpak/net/linux-unstable/pulsar.yml not found")

    print("\n" + "=" * 70)
    for n in notes:
        print(f"note: {n}")
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())