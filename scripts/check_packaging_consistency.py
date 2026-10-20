#!/usr/bin/env python3
"""Check that every packaging format agrees on the shared metadata.

Packaging metadata is duplicated by necessity: each format has its own file
with its own syntax. The cost is drift. The version lives in three places,
the summary in eight, and the maintainer address is a guess in two -- and
nothing notices until someone builds a 0.2.0 artifact labelled 0.3.0.

This is the release gate. It reads the real files and fails loudly on
disagreement, rather than trusting a human to keep them in step.

Usage:
    python3 scripts/check_packaging_consistency.py
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent

problems = []
notes = []


def fail(msg):
    problems.append(msg)
    print(f"  ::error::{msg}")


def read(rel):
    path = ROOT / rel
    if not path.exists():
        fail(f"{rel} is missing")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def project_version():
    text = read("pyproject.toml")
    if tomllib:
        try:
            return tomllib.loads(text)["project"]["version"]
        except Exception as exc:  # noqa: BLE001
            fail(f"could not parse pyproject.toml: {exc}")
            return None
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    return m.group(1) if m else None


def spec_version():
    text = read("packaging/pulsar.spec")
    m = re.search(r"^Version:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def debian_version():
    text = read("debian/changelog")
    m = re.match(r"^pulsar \(([^)]+)\)", text.strip())
    return m.group(1) if m else None


def debian_upstream_version():
    v = debian_version()
    if not v:
        return None
    # Debian versions are <upstream>-<revision>; drop a leading epoch too.
    v = re.sub(r"^\d+:", "", v)
    return v.rsplit("-", 1)[0]


def snap_version():
    text = read("packaging/snap/snapcraft.yaml")
    m = re.search(r'^version:\s*"?([^"\s]+)"?', text, re.M)
    return m.group(1) if m else None


def flatpak_versions():
    out = {}
    for rel in ("packaging/flatpak/pulsar.yml",
                "packaging/flatpak/net/linux-unstable/pulsar.yml"):
        text = read(rel)
        m = re.search(r"^\s*version:\s*['\"]?([^'\"\s]+)", text, re.M)
        out[rel] = m.group(1) if m else None
    return out


#: Placeholders the recipes deliberately carry, substituted at release time
#: by .github/workflows/release.yml. They are correct while they hold these
#: values and wrong the moment a release bumps them.
PLACEHOLDER_VERSION = "0.0.0-PLACEHOLDER"


def check_versions():
    print("== version agreement ==")
    v = project_version()
    print(f"  pyproject.toml: {v}")
    if not v:
        return

    spec = spec_version()
    print(f"  pulsar.spec:    {spec}")
    if spec != v:
        fail(f"pulsar.spec Version is {spec!r}, pyproject says {v!r}")

    deb = debian_upstream_version()
    print(f"  debian/changelog: {debian_version()} (upstream {deb})")
    if deb != v:
        fail(f"debian/changelog upstream is {deb!r}, pyproject says {v!r}")

    snap = snap_version()
    print(f"  snapcraft.yaml: {snap}")
    if snap == PLACEHOLDER_VERSION:
        notes.append(
            "snap version is still the release-time placeholder; "
            ".github/workflows/release.yml must substitute it"
        )
    elif snap != v:
        fail(f"snapcraft.yaml version is {snap!r}, pyproject says {v!r}")

    for rel, fv in flatpak_versions().items():
        if fv is None:
            # A flatpak manifest has no REQUIRED version key -- the version
            # is carried by the branch (stable/unstable) and set at export
            # time by `flatpak build-bundle`. Both manifests here omit it
            # entirely, which is legal. Note it rather than fail, but if one
            # ever gains the field the others must agree with pyproject.
            print(f"  {rel}: (no version field -- legal for flatpak)")
            notes.append(
                f"{rel} carries no version key; flatpak takes the version "
                f"from the branch. Add one only if all manifests get one."
            )
        elif fv == PLACEHOLDER_VERSION:
            print(f"  {rel}: {fv}")
            notes.append(f"{rel} version is still the release-time placeholder")
        else:
            print(f"  {rel}: {fv}")
            if fv != v:
                fail(f"{rel} version is {fv!r}, pyproject says {v!r}")


def check_summary():
    """The summary is retyped in eight files; they had already drifted."""
    print("\n== summary agreement ==")
    text = read("pyproject.toml")
    m = re.search(r'^description = "([^"]+)"', text, re.M)
    canonical = m.group(1) if m else None
    print(f"  pyproject: {canonical!r}")
    if not canonical:
        return

    # Each format writes the summary slightly differently (Slash instead of
    # "or", wrapped, prefixed), so compare on the distinctive words rather
    # than demanding byte equality.
    key = ("graphical", "audio", "routing", "pulseaudio", "pipewire")
    targets = {
        "pulsar.spec": read("packaging/pulsar.spec"),
        "packaging/snap/snapcraft.yaml": read("packaging/snap/snapcraft.yaml"),
        "packaging/flatpak/pulsar.yml": read("packaging/flatpak/pulsar.yml"),
        "pulsar.desktop": read("pulsar.desktop"),
    }
    for rel, body in targets.items():
        low = body.lower()
        missing = [w for w in key if w not in low]
        if missing:
            fail(f"{rel} summary is missing {missing}; "
                 f"keep it in step with pyproject's description")
        else:
            print(f"  {rel}: ok")


def check_maintainer():
    print("\n== maintainer address ==")
    addresses = set()
    for rel in ("debian/control", "packaging/pulsar.spec"):
        body = read(rel)
        for m in re.finditer(r"[\w.+-]+@[\w.-]+", body):
            addresses.add(m.group(0))
    if not addresses:
        fail("no maintainer address found in debian/control or pulsar.spec")
        return
    for a in sorted(addresses):
        print(f"  {a}")
    if len(addresses) > 1:
        fail(f"maintainer addresses disagree across formats: {sorted(addresses)}")
    if any(a.endswith("@users.noreply.github.com") for a in addresses):
        notes.append(
            "maintainer address is still the users.noreply.github.com "
            "placeholder -- replace it before publishing to any archive"
        )


def check_runtime_deps():
    """Runtime dependency NAMES differ per distro. Getting them wrong is
    silent: the package installs, then the feature is missing at runtime.

    Concretely: pw-dump/pw-top/pw-record are in `pipewire-bin` on
    Debian/Ubuntu, `pipewire-utils` on Fedora, and `pipewire-bin` on Ubuntu
    snaps. Depend on `pipewire` and you get systemd units, not binaries.
    """
    print("\n== runtime dependency names ==")

    deb = read("debian/control")
    m = re.search(r"^Depends:\n((?: .*\n)+)", deb, re.M)
    deb_deps = m.group(1) if m else ""
    # strip the ${...} substitution fields debhelper expands at build time
    deb_deps = "\n".join(
        line for line in deb_deps.splitlines()
        if "${" not in line
    )

    expected = {
        "debian/control": (deb_deps, {
            "python3": "interpreter",
            "python3-tk": "tkinter",
            "pulseaudio-utils": "pactl",
            "pipewire-bin": "pw-dump/pw-top/pw-record",
        }),
        "packaging/pulsar.spec": (read("packaging/pulsar.spec"), {
            "python3": "interpreter",
            "python3-tkinter": "tkinter (Fedora's name)",
            "pulseaudio-utils": "pactl",
            "pipewire-utils": "pw-* (Fedora's name)",
        }),
    }

    for rel, (body, required) in expected.items():
        print(f"  {rel}")
        for pkg, why in required.items():
            if not re.search(rf"\b{re.escape(pkg)}\b", body):
                fail(f"{rel} does not depend on {pkg} ({why})")
            else:
                print(f"    ok  {pkg:<18} {why}")

    # The bare `pipewire` metapackage is the specific mistake worth guarding:
    # it satisfies a naive grep but ships no binaries.
    if re.search(r"^\s*pipewire\s*,?\s*$", deb, re.M):
        fail("debian/control depends on `pipewire`, which ships only systemd "
             "units; the pw-* binaries are in `pipewire-bin`")

    snap = read("packaging/snap/snapcraft.yaml")
    print("  packaging/snap/snapcraft.yaml")
    for pkg, why in (("pulseaudio-utils", "pactl"),
                     ("pipewire-bin", "pw-* on Ubuntu"),
                     ("python3-tk", "tkinter")):
        if pkg in snap:
            print(f"    ok  {pkg:<18} {why}")
        else:
            fail(f"snapcraft.yaml does not stage {pkg} ({why})")


def check_desktop_files():
    """Four copies of the .desktop file; Exec must never be a placeholder in
    anything that gets shipped."""
    print("\n== .desktop files ==")
    shipped = [
        "packaging/appimage/pulsar.desktop",
    ]
    for rel in shipped:
        body = read(rel)
        if "PLACEHOLDER_EXEC_PATH" in body:
            fail(f"{rel} still has Exec=PLACEHOLDER_EXEC_PATH; only the "
                 f"repo-root pulsar.desktop may keep that")
        else:
            print(f"  {rel}: Exec resolved")
    template = read("pulsar.desktop")
    if "PLACEHOLDER_EXEC_PATH" not in template:
        notes.append(
            "pulsar.desktop is the install.sh template and is expected to "
            "keep PLACEHOLDER_EXEC_PATH"
        )
    for rel in shipped:
        body = read(rel)
        if "Icon=pulsar" not in body:
            fail(f"{rel} does not use Icon=pulsar; it must match the "
                 f"installed icon basename")


def main():
    print("=" * 62)
    print("Packaging consistency check")
    print("=" * 62)

    check_versions()
    check_summary()
    check_maintainer()
    check_runtime_deps()
    check_desktop_files()

    print()
    if notes:
        print("Notes (not failures):")
        for n in notes:
            print(f"  - {n}")
        print()

    print("=" * 62)
    if problems:
        print(f"FAILURES ({len(problems)})")
        return 1
    print("All packaging metadata agrees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
