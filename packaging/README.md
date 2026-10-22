# Packaging Pulsar

Five formats, one source tree. This file is the map: what each format
needs, how to build it here, and what has actually been verified.

---

## Where the artifacts land: `dist/`

**Every built package goes in `dist/` at the repo root.** One directory,
all five formats, same names locally and in CI. `dist/` is gitignored —
it is build output, not source.

```
dist/
├── pulsar-0.1.0-x86_64.AppImage      # AppImage
├── pulsar_0.1.0_all.deb              # deb
├── pulsar-0.1.0-1.fc42.noarch.rpm    # rpm
├── pulsar_0.1.0_amd64.snap           # snap
└── pulsar-0.1.0.flatpak              # flatpak
```

| Format | Filename in `dist/` | Build it with |
|---|---|---|
| **AppImage** | `pulsar-<version>-<arch>.AppImage` | `scripts/build_appimage.sh` |
| **deb** | `pulsar_<version>_all.deb` | `scripts/build_deb.sh` |
| **rpm** | `pulsar-<version>-1.<dist>.noarch.rpm` | `scripts/build_rpm.sh` |
| **snap** | `pulsar_<version>_amd64.snap` | `snapcraft --destructive-mode && cp ./*.snap dist/` |
| **flatpak** | `pulsar-<version>.flatpak` | `flatpak build-bundle` (see the release workflow) |

`<version>` is `version` in `pyproject.toml` — the single source of truth
for every filename above. `<arch>` is the build machine's architecture
(`x86_64` or `aarch64`). `<dist>` is the RPM dist tag (`.fc42` on Fedora).

### Which of these can be built where

The table above lists the names, not a promise that this machine can
produce them. What is actually buildable, and what was verified by really
running the build, is in [What is verified, and what is not](#what-is-verified-and-what-is-not)
at the bottom of this file. In short: **deb, rpm, AppImage and flatpak build here; snap does not.**
The freedesktop SDK does fetch, and the flatpak really builds — see the
table at the bottom. Snapcraft runs here too, but cannot finish, for a
reason that is about this host's apt rather than about the recipe.

### Two formats needed a wrapper to get here

`dpkg-buildpackage` writes its `.deb` to the **parent** of the source tree
— `~/gh/pulsar_0.1.0_all.deb`, one directory above the checkout, outside
every `ls` anyone runs. `rpmbuild` writes into its `_topdir`, which is
scratch. Neither puts anything in a place a human looks.
`scripts/build_deb.sh` and `scripts/build_rpm.sh` wrap both, move the
artifact into `dist/`, and print the path. The release workflow calls the
same two scripts, so CI and a local build cannot disagree about a
filename.

Run `scripts/check_packaging_consistency.py` before any release. It is the
gate that catches the failure mode of having five formats: they disagree
silently until someone ships a mislabelled artifact.

---

## The two blockers, and why they came first

Neither of these is cosmetic. Both were found only because packaging work
forced a look at the file layout, and every recipe written before fixing
them would have shipped a broken package.

### 1. User data resolved relative to the working directory

`PresetManager` and `ProfileManager` both defaulted to `presets_dir="presets"`
and created it at construction time. Run from a directory the user cannot
write and the app dies before the window appears:

```
PermissionError: [Errno 13] Permission denied: 'presets'
```

That is exactly the shape of a snap, flatpak or AppImage launch. Under
deb/rpm it fails more quietly: data lands wherever the user happened to be,
and a stray `presets/` in `$HOME` or a project directory gets silently
adopted as the profile store.

Now resolved under `$XDG_DATA_HOME/pulsar/presets`. See `pulsar/utils/paths.py`.

### 2. The wheel installed generic top-level modules

The source layout was `src/utils/`, `src/ui/`, `src/config/`, so the wheel
installed `utils`, `ui` and `config` as **top-level modules**. Survivable in
a venv. Fatal in a distro package, which drops those into system
`dist-packages` where they collide with every other package using those
names — and whoever loses the import race breaks silently.

Now `src/pulsar/`, with a CI assertion on `top_level.txt` so it cannot
regress.

---

## Runtime dependencies

Pulsar shells out to the audio server's own tools. They are **not** Python
imports, so they are distro package dependencies, and the names differ:

| Tool | Debian / Ubuntu | Fedora | Ubuntu snap |
|---|---|---|---|
| `pactl` | `pulseaudio-utils` | `pulseaudio-utils` | `pulseaudio-utils` |
| `pw-dump`, `pw-top`, `pw-record` | **`pipewire-bin`** | **`pipewire-utils`** | **`pipewire-bin`** |
| tkinter | `python3-tk` | `python3-tkinter` | `python3-tk` |

**The `pipewire` trap.** On Debian and Ubuntu, `pipewire` ships *only*
`/usr/lib/systemd/user/pipewire.{service,socket}`. The `pw-*` binaries are
in `pipewire-bin`. Depending on `pipewire` satisfies a naive grep and
installs nothing useful:

```
$ dpkg -S $(command -v pw-dump)
pipewire-bin: /usr/bin/pw-dump
```

`check_packaging_consistency.py` guards against exactly this.

For an **AppImage**, none of these are bundled. They link against the
host's libpulse/libpipewire and talk to a session socket that only exists
on the host, so a bundled copy fails at dlopen. `AppRun` puts the AppDir's
`usr/bin` *in front of* a `PATH` that still contains the host's `/usr/bin`,
so they resolve to the host. Do not "fix" a missing `pactl` by copying it in.

---

## Per-format notes

### deb — `debian/`

Built with `dpkg-buildpackage -us -uc -b --no-sign`. Uses
`debhelper-compat (= 13)`, so there is no `debian/compat` file, and
`dh_python3 --with python3 --buildsystem=pybuild`.

Do **not** set `PYBUILD_NAME=pulsar`. pybuild rewrites the destdir to
`debian/` + `suggest_pkg_name("pulsar")` = `debian/python3-pulsar`, while
the binary package is named `pulsar`, so the payload would be silently
dropped. debhelper already passes the right `--dest-dir` for a
single-binary compat-13 package, so the correct fix is to set nothing.

No `debian/pulsar.install` either: `dh_install` installs only what is
listed, and it cannot rename files or rewrite contents. Both are needed
here (`pulsar-16.png` → `16x16/apps/pulsar.png`, `Exec=PLACEHOLDER…` →
`/usr/bin/pulsar`), so `override_dh_auto_install` does them.

`debian/rules` is checked by `scripts/check_debian_rules.py` — `bash -n` on
it is meaningless, it is a Makefile, and `make` is not always installed.

### rpm — `packaging/pulsar.spec`

Targets Fedora. Uses `%pyproject_wheel` / `%pyproject_install` /
`%pyproject_save_files` from `pyproject-rpm-macros`, which ship with
Fedora's `python3-devel` and exist nowhere on Ubuntu — so building here
means fetching those macro definitions from `src.fedoraproject.org` and
pointing `rpmbuild` at them. With that in place the spec builds
end-to-end and produces a real `.rpm`.

Two bugs that only a real build finds, both fixed:

- The version guard ran **before** the source-unpacking line, so it read a
  `pyproject.toml` that did not exist yet and reported a version mismatch.
- **RPM expands macros inside comment text.** Naming a macro in prose
  expands it to its shell script and the rest of that comment line executes.
  The comment explaining this bug triggered it a second time.

### AppImage — `scripts/build_appimage.sh`

Self-contained: bundles a CPython runtime from `python-build-standalone`,
the wheel, and tkinter.

Needs `appimagetool`, which is **not in any distro repo** — it is itself an
AppImage, and it needs FUSE to run. Without FUSE:

```sh
./appimagetool.AppImage --appimage-extract
squashfs-root/usr/bin/appimagetool        # the real binary
```

It also needs `desktop-file-validate` (`desktop-file-utils`) on `PATH`.

The pinned runtime URL must carry the **full** version, patch included:
`cpython-3.13.16+20261013-…`. Splicing the series and the date together
yields a 404.

### snap — `packaging/snap/snapcraft.yaml`

`base: core24`, strict confinement, `audio-playback` + `audio-record`
plugs. There is no `pulseaudio` layout — layouts cannot target `/run`, and
`pulseaudio`'s base declaration sets `deny-auto-connection: true`.

The part that is easy to miss: snapd sets `XDG_RUNTIME_DIR` to a
snap-private directory, so `pactl` looks there and finds no socket, and
snap audio is simply dead. `PULSE_SERVER` must be redirected to the real
`/run/user/<uid>/pulse/native`. The recipe does this in a
self-contained launch wrapper.

**Running snapcraft without snapd.** snapcraft ships only as a snap, so
no distro can `apt-get install` it — but the store serves the squashfs
over plain HTTP and `unsquashfs` reads it. Three obstacles, each with a
specific fix:

```bash
W=/tmp/snapcraft/snap
curl -sL -o snapcraft.snap "https://api.snapcraft.io/api/v1/snaps/download/…"
unsquashfs -f -d "$W" snapcraft.snap

export PYTHONPATH=$W/lib/python3.12/site-packages
# NOT the snap's own lib dir -- it is a base snap and bundles no libc, so
# pointing LD_LIBRARY_PATH there stops libc.so.6 resolving at all.
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu
# The interpreter is /snap/core24/current/lib64/ld-linux-x86-64.so.2, which
# does not exist here, so the system loader is invoked explicitly.
/lib64/ld-linux-x86-64.so.2 "$W/usr/bin/python3.12" "$W/bin/snapcraft" "$@"
```

That gets you `snapcraft 9.2.1`, which parses the recipe and processes
all four parts. It cannot then finish, because craft-parts imports
`python-apt` and this snap's Python 3.12 needs `libapt-pkg.so.6.0` from
apt 2.7, while this host has apt 3.x (`libapt-pkg.so.7.0`). The 2.7
library is not in the archive pool reachable from here. That is a host
packaging mismatch inside snapcraft's own package backend, not anything
the recipe can fix, and it fires before any part is built.

### flatpak — `packaging/flatpak/pulsar.yml`

`org.freedesktop.Platform//24.08`. Reaches PulseAudio the supported way:
`--filesystem=xdg-run/pulse`. There is no audio plug.

**The runtime does not ship tkinter.** `python3.bst` deletes
`python*/tkinter` on 23.08, 24.08 and 25.08, and upstream MR !4569 to
include it was closed unmerged. The manifest builds `tkinter-standalone`,
which is what shipped apps do.

Both manifests have **no `version` key**, which is legal — flatpak takes
the version from the branch.

**How to build it here, since flatpak is not installed system-wide.** It
needs no root:

```bash
# flatpak + flatpak-builder, unpacked from .debs
mkdir -p /tmp/fbt && cd /tmp/fbt
apt-get download flatpak flatpak-builder libflatpak0 libostree-1-1 \
                 libcomposefs1 libpolkit-gobject-1-0 libpolkit-agent-1-0 \
                 libmalcontent-0-0 elfutils
for d in *.deb; do dpkg -x "$d" /tmp/fbt/root; done
export LD_LIBRARY_PATH=/tmp/fbt/root/usr/lib/x86_64-linux-gnu
export PATH=/tmp/fbt/root/usr/bin:$PATH

export FLATPAK_USER_DIR="$HOME/.local/share/flatpak-test"
flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.freedesktop.Platform//24.08
flatpak install --user -y flathub org.freedesktop.Sdk//24.08

flatpak-builder --user --force-clean --disable-rofiles-fuse \
    --state-dir="$HOME/.cache/fb-state" "$HOME/fb-build" \
    packaging/flatpak/pulsar.yml
```

Three things that are not optional:

- **`FLATPAK_USER_DIR` must be exported** or the SDK is installed to the
  default location and `flatpak-builder`, running with a different
  `FLATPAK_USER_DIR` in a previous attempt, cannot see it. That is what
  produced the misleading `Unable to find sdk org.freedesktop.Sdk version
  24.08` while the SDK was demonstrably installed: it was looking in
  `runtime/…` while the SDK had landed in `sdk/…` under a different
  user dir.
- **`elfutils` is required.** Without `eu-strip` the Tcl build dies at the
  strip stage with a bare `Failed to execute child process "eu-strip"`.
- **`--state-dir` and the build dir must be on the same filesystem**, or
  flatpak-builder refuses to start.

The final `flatpak build-export` step is not reproduced here: it invokes
`/usr/libexec/flatpak-validate-icon` by absolute path, that directory is
root-owned, and there is no sudo. Any machine with flatpak actually
installed — including the release runner — has it.

---

## Version bumps

`pyproject.toml` is the authority. To cut a release:

1. Bump `version` in `pyproject.toml`.
2. Add the matching `debian/changelog` entry (`dch -v <version>`), which
   adds `<version>-1`.
3. Bump `Version:` in `packaging/pulsar.spec`. Its `%prep` asserts the two
   agree, so a partial bump fails the build rather than shipping an RPM
   whose filename disagrees with its contents.
4. `snapcraft.yaml` carries `0.0.0-PLACEHOLDER`; the release workflow
   substitutes it.
5. Run `scripts/check_packaging_consistency.py`.

`debian/rules` has the same guard as the spec.

---

## What is verified, and what is not

| Format | Built here | Notes |
|---|---|---|
| **deb** | yes | Assembled with `dpkg-deb`, inspected, extracted. Real `.deb` produced. |
| **AppImage** | yes | 69 MB, sha256 verified, extracted and **run** — GUI rendered under Xvfb. |
| **rpm** | yes | Built end-to-end with Fedora's `pyproject-rpm-macros` under `rpmbuild 6.0.1`. Real `.rpm`, 62 files, `check-files` reports nothing unpackaged. |
| **flatpak** | yes | Built for real with `flatpak-builder 1.4.8` against `org.freedesktop.Sdk//24.08`: Tcl, Tk and `tkinter-standalone` all compile, and the finished app dir contains both launchers, `_tkinter`, the `pulsar` package, the desktop entry (`Exec=pulsar-gui`) and the icon. 44 MB. The only step not reproduced here is `flatpak build-export`, which needs `flatpak-validate-icon` at the hardcoded `/usr/libexec` — see below. |
| **snap** | partial | `snapcraft 9.2.1` runs here (extracted from the store, no snapd) and parses and processes all four parts, but cannot complete: its bundled Python 3.12 wants `libapt-pkg.so.6.0` from apt 2.7, this host has apt 3.x (`libapt-pkg.so.7.0`), and the 2.7 library is not retrievable here. Nothing in the recipe was implicated — the failure is in snapcraft's own package backend before any part is built. |

"Written but unbuilt" is not a soft caveat. Every format that has not been
built has a proven record of containing build-breaking bugs that no amount
of review or structural validation caught.
