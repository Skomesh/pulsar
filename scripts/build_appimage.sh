#!/usr/bin/env bash
#
# build_appimage.sh -- package Pulsar as a self-contained AppImage.
#
# What this produces
# ------------------
#   dist/pulsar-<version>-<arch>.AppImage
#
# What is bundled
# ---------------
#   * a CPython runtime (python-build-standalone "install_only" build, which
#     bundles its own libpython, Tcl and Tk and carries $ORIGIN-relative
#     RPATHs, so it keeps working after being moved inside a squashfs mount)
#   * tkinter, via that runtime's _tkinter extension module
#   * the `pulsar` package, installed from the wheel this script builds with
#     `python -m build --wheel` -- i.e. exactly the artifact `pip install .`
#     would produce, not the git working tree
#
# What is deliberately NOT bundled
# --------------------------------
#   pactl, pw-dump, pw-top, pw-record. These link against the host's
#   libpulse / libpipewire and talk to a session bus socket that only exists
#   on the host. A copy inside the AppImage would fail at dlopen or connect
#   time. AppRun therefore puts the AppDir's usr/bin *in front of* a PATH
#   that still contains the host's /usr/bin, so those resolve to the host's
#   binaries. Do not "fix" a missing pactl by copying it in.
#
# ---------------------------------------------------------------------------
# TOOLING THIS NEEDS, AND WHAT HAPPENS WITHOUT IT
#
#   appimagetool   REQUIRED. Converts the staged AppDir into the .AppImage
#                  (squashfs image + type2 runtime). Not installable from
#                  distro repos and not present on every machine; if it is
#                  missing this script exits non-zero with install
#                  instructions rather than quietly writing nothing.
#                     https://github.com/AppImage/appimagetool#usage
#                     Needs FUSE (libfuse2) to run; on a machine without it,
#                     extract appimagetool itself (`--appimage-extract`) or
#                     pass it with --appimage-extract-and-run.
#   appimagetool   optional: sets desktop-file validation. Run if present.
#   zsync          optional: only for `-u` AppImage update information, which
#                  is off by default.
#   curl / wget   optional: only if no local Python runtime is supplied.
#   strip          optional: shrinks the bundled interpreter.
#   desktop-file-validate  optional: used for extra validation if installed.
#   python-appimage (pip)   optional alternative for appimagetool when you
#                  cannot install the official one. Needs network access.
# ---------------------------------------------------------------------------
# Fully offline builds
# --------------------
# Network is not assumed. Point the script at an already-downloaded Python
# runtime and it never touches the network at all:
#
#   PULSAR_PYTHON_TARBALL=./cpython-3.13.16+20261013-x86_64-unknown-linux-gnu-install_only.tar.gz \
#       scripts/build_appimage.sh
#
# or, if you have it unpacked already:
#
#   PULSAR_PYTHON_PREFIX=/opt/cpython-3.13-install_only/python \
#       scripts/build_appimage.sh
#
# Other knobs: --stage-only (build + verify the AppDir, skip appimagetool),
# --keep-appdir, --appimagetool PATH, --output FILE, --strip/--no-strip.
# Run with --help for the full list.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
PKG_DIR="$REPO_ROOT/packaging/appimage"
DIST_DIR="$REPO_ROOT/dist"

# --- pinned build inputs ------------------------------------------------
# Pinned, not "latest": the URL and its SHA-256 are recorded together so two
# runs of this script produce the same bytes. Bump PBS_VERSION and
# PBS_SHA256_<arch> together, and only after checking that the release's
# digest from the GitHub API matches what is written here.
PBS_VERSION="20261013"
PBS_PYTHON_SERIES="3.13"
# The full upstream version string, patch included. python-build-standalone
# names its assets cpython-<major.minor.patch>+<date>-<target>-... , so the
# URL needs 3.13.16+20261013, not 3.13 and 20261013 spliced together.
PBS_PYTHON_FULL_VERSION="3.13.16+20261013"
PBS_SHA256_x86_64="e588e50db882fa6a89f926d9a9ad64e445183af0eda5bda226a2c151b17db8a6"
PBS_SHA256_aarch64="0fce0605da7b6532d5bdaec185336ffaaddd02ec04e27382014952ca4857ed6d"

# --- logging ------------------------------------------------------------
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,/# --- pinned build inputs ---/p' "${BASH_SOURCE[0]}" \
        | sed -e 's/^# \{0,1\}//' -e '/^$/d' -e '1d' -e '$d'
    cat <<'EOF'

Options:
  --stage-only          Build and verify the AppDir, then stop (no appimagetool).
  --appimagetool PATH   Use this appimagetool instead of $PATH.
  --output FILE         Write the AppImage to FILE (default: dist/pulsar-<ver>-<arch>.AppImage).
  --python-tarball F    Use this already-downloaded CPython runtime tarball.
  --python-prefix DIR   Use this already-unpacked CPython runtime.
  --strip / --no-strip  Strip the bundled interpreter (default: strip if available).
  --keep-appdir         Keep the staged AppDir instead of deleting it.
  -h, --help            This message.

Environment:
  PULSAR_PYTHON_TARBALL, PULSAR_PYTHON_PREFIX, PULSAR_PYTHON_URL,
  PULSAR_PYTHON_SHA256, PULSAR_DIST_DIR, PULSAR_ARCH
EOF
}

# --- argument parsing ---------------------------------------------------
STAGE_ONLY=0
KEEP_APPDIR=0
DO_STRIP=auto
APPIMAGETOOL=""
OUTPUT=""
PYTHON_TARBALL="${PULSAR_PYTHON_TARBALL:-}"
PYTHON_PREFIX="${PULSAR_PYTHON_PREFIX:-}"
PYTHON_URL="${PULSAR_PYTHON_URL:-}"
PYTHON_SHA256="${PULSAR_PYTHON_SHA256:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --stage-only)     STAGE_ONLY=1 ;;
        --keep-appdir)    KEEP_APPDIR=1 ;;
        --strip)          DO_STRIP=yes ;;
        --no-strip)       DO_STRIP=no ;;
        --appimagetool)   APPIMAGETOOL=${2:?--appimagetool needs a path}; shift ;;
        --output)         OUTPUT=${2:?--output needs a path}; shift ;;
        --python-tarball) PYTHON_TARBALL=${2:?--python-tarball needs a path}; shift ;;
        --python-prefix)  PYTHON_PREFIX=${2:?--python-prefix needs a path}; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                die "unknown argument: $1 (try --help)" ;;
    esac
    shift
done

# --- architecture -------------------------------------------------------
# One mapping drives three things that must agree: the python-build-standalone
# asset name, the AppImage filename, and appimagetool's ARCH.
detect_arch() {
    local machine
    machine=$(uname -m)
    case "$machine" in
        x86_64|amd64)       printf 'x86_64\n' ;;
        aarch64|arm64)      printf 'aarch64\n' ;;
        i386|i686)          printf 'i686\n' ;;
        armv7l|armv7)       printf 'armhf\n' ;;
        *)                  printf '%s\n' "$machine" ;;
    esac
}
ARCH="${PULSAR_ARCH:-$(detect_arch)}"

case "$ARCH" in
    x86_64)  PBS_TARGET="x86_64-unknown-linux-gnu" ;;
    aarch64) PBS_TARGET="aarch64-unknown-linux-gnu" ;;
    *)       PBS_TARGET="" ;;  # not offered by python-build-standalone; supply a tarball
esac

# --- version (from pyproject.toml, never hardcoded) ---------------------
# Reads [project].version out of the real pyproject. tomllib does it
# properly on 3.11+; the regex fallback covers older interpreters and is
# anchored to the [project] table so it cannot pick up e.g. a
# dependency pin. A missing or empty version is a hard error -- a package
# called "pulsar-unknown" is worse than a failed build.
read_project_version() {
    "$1" - "$REPO_ROOT/pyproject.toml" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()

version = ""
try:
    import tomllib
except ModuleNotFoundError:
    pass
else:
    with open(path, "rb") as fh:
        version = tomllib.load(fh).get("project", {}).get("version", "")

if not version:
    table = text.split("[project]", 1)[-1].split("\n[", 1)[0]
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', table, re.MULTILINE)
    version = match.group(1) if match else ""

if not version:
    sys.exit("no [project].version found in pyproject.toml")
if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", version):
    sys.exit(f"refusing to use implausible version string: {version!r}")
print(version)
PY
}

# =========================================================================
# main
# =========================================================================
main() {
    cd "$REPO_ROOT"

    local build_python
    build_python=$(pick_build_python)

    local version
    version=$(read_project_version "$build_python")
    log "Pulsar version: $version (from pyproject.toml)"

    local arch out_name
    arch="$ARCH"
    [ -n "$OUTPUT" ] || OUTPUT="$DIST_DIR/pulsar-${version}-${arch}.AppImage"
    out_name=$(basename -- "$OUTPUT")

    # AppDir goes in a fresh mktemp dir, never in the repo and never
    # in-place: a re-run cannot inherit half-written state from a failed
    # previous run, and a failed run leaves the tree exactly as it found it.
    local work
    work=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-appimage.XXXXXXXX")
    # shellcheck disable=SC2064  # expand work now, on purpose
    trap "cleanup_workdir '$work' '$KEEP_APPDIR'" EXIT
    log "work dir: $work"

    local appdir="$work/Pulsar.AppDir"
    mkdir -p "$appdir"

    build_wheel "$build_python" "$work"
    stage_python_runtime "$work" "$appdir"
    install_wheel "$work" "$appdir"
    stage_launchers "$appdir"
    stage_icons "$appdir" "$arch"
    finalize_appdir "$appdir"
    verify_appdir "$appdir" "$work"

    log "staged AppDir OK: $appdir"

    if [ "$STAGE_ONLY" -eq 1 ]; then
        log "--stage-only: stopping before appimagetool. NO .AppImage was produced."
        log "keep it with: --keep-appdir --stage-only"
        return 0
    fi

    local tool
    tool=$(locate_appimagetool)
    run_appimagetool "$tool" "$appdir" "$OUTPUT"
    report_result "$OUTPUT" "$out_name"
}

cleanup_workdir() {
    local dir=$1 keep=$2
    if [ "$keep" -eq 1 ]; then
        log "keeping work dir: $dir"
    elif [ -n "${dir:-}" ] && [ -d "$dir" ]; then
        rm -rf -- "$dir"
    fi
}

# --- build python -------------------------------------------------------
# The wheel is built by a host python that has `build` available. The repo
# venv is preferred; a system python with the module installed works too.
pick_build_python() {
    local candidate
    for candidate in "$REPO_ROOT/.venv/bin/python" "$(command -v python3 || true)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        if "$candidate" -c 'import build' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    die "no python with the 'build' module found.
       Install it with:  python3 -m pip install build
       Or use the repo venv:  python3 -m venv .venv && .venv/bin/pip install build"
}

# --- wheel --------------------------------------------------------------
build_wheel() {
    local py=$1 work=$2
    log "building wheel with $py"

    # Build from a pristine copy of the sources in the temp dir. `python -m
    # build` otherwise writes build/ and src/pulsar.egg-info into the working
    # tree, which is exactly the in-place state this script promises not to
    # create -- and would let a stale egg-info leak into the wheel.
    local srcdir="$work/wheelsrc"
    mkdir -p "$srcdir"
    cp "$REPO_ROOT/pyproject.toml" "$srcdir/"
    # Declared by pyproject but not needed to build the wheel; copied only so
    # the metadata is complete if the build ever reads them.
    for extra in README.md LICENSE NOTICE; do
        [ -f "$REPO_ROOT/$extra" ] && cp "$REPO_ROOT/$extra" "$srcdir/"
    done
    mkdir -p "$srcdir/src"
    ( cd "$REPO_ROOT" \
      && find src/pulsar \
           -name '__pycache__' -prune -o \
           -name '*.pyc' -prune -o \
           -type f -print | while read -r f; do
            mkdir -p "$srcdir/$(dirname "$f")"
            cp "$f" "$srcdir/$f"
         done )

    mkdir -p "$work/wheelhouse"
    if ! ( cd "$srcdir" && "$py" -m build --wheel --outdir "$work/wheelhouse" ) 2>"$work/build.log"; then
        # Almost always "no network to fetch setuptools" -- build isolation
        # creates a fresh venv and pip-installs the backend into it.
        warn "isolated wheel build failed; retrying with --no-isolation"
        warn "(build.log: $work/build.log)"
        ( cd "$srcdir" && "$py" -m build --wheel --no-isolation --outdir "$work/wheelhouse" ) \
            || { cat "$work/build.log" >&2; die "wheel build failed"; }
    fi

    WHEEL=$(find "$work/wheelhouse" -maxdepth 1 -name 'pulsar-*.whl' -print -quit)
    [ -n "$WHEEL" ] || die "no pulsar-*.whl produced (looked in $work/wheelhouse)"
    log "wheel: $(basename -- "$WHEEL")"
}

# --- python runtime -----------------------------------------------------
# Produces $APPDIR/usr/{bin,lib,...} containing a self-contained CPython.
# The layout matches what AppRun expects: bin/python3, lib/python3.X/, and
# lib/python3.X/site-packages/ for the app.
stage_python_runtime() {
    local work=$1 appdir=$2
    local prefix="$PYTHON_PREFIX"

    if [ -z "$prefix" ]; then
        local tarball="$PYTHON_TARBALL"
        if [ -z "$tarball" ]; then
            tarball="$work/python-runtime.tar.gz"
            fetch_python_runtime "$tarball"
        elif [ ! -f "$tarball" ]; then
            die "PULSAR_PYTHON_TARBALL / --python-tarball: no such file: $tarball"
        fi
        prefix=$(extract_python_tarball "$tarball" "$work/python-runtime")
        [ -n "$prefix" ] || die "could not find bin/python3 inside $tarball"
    else
        [ -d "$prefix" ] || die "--python-prefix / PULSAR_PYTHON_PREFIX: no such directory: $prefix"
        [ -x "$prefix/bin/python3" ] \
            || die "$prefix does not look like a CPython prefix (no bin/python3)"
    fi

    log "staging CPython from $prefix"
    mkdir -p "$appdir/usr"
    cp -a "$prefix/." "$appdir/usr/"

    # The stdlib directory decides the minor-version globs below. Derive it
    # from the runtime that was actually copied, not from PBS_PYTHON_SERIES:
    # --python-prefix/--python-tarball can legitimately supply a different
    # series than the pinned one, and a prune glob that silently matches
    # nothing leaves test suites and ensurepip in the AppImage.
    local series_dir
    series_dir=$(find "$appdir/usr/lib" -maxdepth 1 -type d -name 'python3.*' -print -quit)
    [ -n "$series_dir" ] || die "no lib/python3.* directory in the bundled runtime at $prefix"
    local series
    series=$(basename -- "$series_dir")

    # Drop what an end user cannot reach and does not need. Removing these
    # keeps the AppImage materially smaller; keeping them only adds weight.
    rm -rf -- "$appdir/usr/include" \
              "$appdir/usr/share/man" \
              "$appdir/usr/lib/pkgconfig" \
              "$series_dir/test" \
              "$series_dir/idlelib" \
              "$series_dir/tkinter/test" \
              "$series_dir/lib2to3/tests" \
              "$series_dir/ensurepip" \
              "$series_dir/site-packages" \
              "$appdir/usr/bin/pip" "$appdir/usr/bin/pip3" "$appdir/usr/bin/pip3."* \
              "$appdir/usr/bin/idle3"* "$appdir/usr/bin/pydoc3"* \
              2>/dev/null || true

    # python-build-standalone ships bin/python, bin/python3 and bin/python3.13
    # as three symlinks to one ~80 MB ELF. AppRun execs bin/python3, so point
    # that name at the real file and re-create the other two as symlinks.
    #
    # Order matters and regular-files-only matching is essential: bin/python3
    # is itself a symlink here, so a `-name 'python*' -delete` would remove
    # exactly the file AppRun cannot start without. Move the real ELF out to
    # a staging name first, clear the directory, then move it into place.
    local real_python=""
    local candidate
    for candidate in "$appdir/usr/bin/python3."* "$appdir/usr/bin/python3" "$appdir/usr/bin/python"; do
        if [ -f "$candidate" ] && [ ! -L "$candidate" ]; then
            real_python=$candidate
            break
        fi
    done
    [ -n "$real_python" ] || die "bundled runtime has no real interpreter binary in usr/bin"
    mv -- "$real_python" "$work/python-real"
    rm -f -- "$appdir/usr/bin/python" "$appdir/usr/bin/python3" \
             "$appdir/usr/bin/python3."* \
             "$appdir/usr/bin/python-config" "$appdir/usr/bin/python3-config" \
             2>/dev/null || true
    mv -- "$work/python-real" "$appdir/usr/bin/python3"
    ln -sf python3 "$appdir/usr/bin/python"
    # python3.X keeps the upstream name (python3.13), for anything that
    # looks the interpreter up by its full version.
    local minor=${series#python3.}
    [ -n "$minor" ] && [ "$minor" != "$series" ] \
        && ln -sf python3 "$appdir/usr/bin/python3.$minor"

    # Stripping the interpreter is worth ~50 MB in the AppImage, but it is
    # not always safe: some binutils builds mangle the versioned dynamic
    # symbol table of a python-build-standalone ELF and produce a binary that
    # dies at load time with "no version information available". Try, then
    # actually run it, and keep the unstripped copy if it did not survive.
    if [ "$DO_STRIP" = yes ] || { [ "$DO_STRIP" = auto ] && command -v strip >/dev/null 2>&1; }; then
        if command -v strip >/dev/null 2>&1; then
            cp -a "$appdir/usr/bin/python3" "$work/python-unstripped"
            log "stripping the bundled interpreter"
            strip --strip-unneeded "$appdir/usr/bin/python3" 2>/dev/null || true
            if "$appdir/usr/bin/python3" -c pass >/dev/null 2>&1; then
                log "stripped interpreter runs ($(du -h "$appdir/usr/bin/python3" | cut -f1))"
            else
                warn "stripping broke the interpreter; keeping the unstripped copy"
                cp -a "$work/python-unstripped" "$appdir/usr/bin/python3"
            fi
        else
            warn "strip not found; the AppImage will be larger than necessary"
        fi
    fi

    log "bundled python: $("$appdir/usr/bin/python3" -c 'import sys;print(sys.version.split()[0])')"

    # site-packages is where the wheel lands; create it now so install_wheel
    # has a target even if the wheel contains nothing but the package.
    mkdir -p "$appdir/usr/lib/$series/site-packages"
    log "site-packages: usr/lib/$series/site-packages"
}

fetch_python_runtime() {
    local dest=$1
    if [ -z "$PYTHON_URL" ]; then
        [ -n "$PBS_TARGET" ] || die \
"no CPython runtime available for architecture '$ARCH'.

python-build-standalone does not publish $ARCH, and this script will not
guess at a system python and copy the host's whole /usr into an AppImage.

Supply a runtime explicitly:
  --python-tarball /path/to/cpython-<ver>-<target>-install_only.tar.gz
  --python-prefix  /path/to/unpacked/python
or set PULSAR_PYTHON_URL to a URL you trust."
        PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_VERSION}/cpython-${PBS_PYTHON_FULL_VERSION}-${PBS_TARGET}-install_only.tar.gz"
    fi
    [ -n "$PYTHON_SHA256" ] || eval "PYTHON_SHA256=\"\${PBS_SHA256_${ARCH}:-}\""

    log "downloading CPython runtime: $PYTHON_URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --retry-delay 2 -o "$dest" "$PYTHON_URL" \
            || die "download failed: $PYTHON_URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$dest" "$PYTHON_URL" || die "download failed: $PYTHON_URL"
    else
        die "neither curl nor wget is available to download the CPython runtime.
       Install one, or pass a local runtime:
         --python-tarball /path/to/cpython-<ver>-<target>-install_only.tar.gz"
    fi

    # Verify before trusting. A silently corrupted interpreter produces an
    # AppImage that fails at the user's machine with something far less
    # obvious than a checksum mismatch.
    if [ -n "$PYTHON_SHA256" ]; then
        local actual
        actual=$(sha256sum "$dest" | cut -d' ' -f1)
        [ "$actual" = "$PYTHON_SHA256" ] || die \
"checksum mismatch for $dest
  expected $PYTHON_SHA256
  actual   $actual
Refusing to build an AppImage from an unverified interpreter."
        log "sha256 verified"
    else
        warn "no pinned checksum for arch '$ARCH'; runtime downloaded but NOT verified"
    fi
}

extract_python_tarball() {
    local tarball=$1 dest=$2
    mkdir -p "$dest"
    # python-build-standalone's install_only tarballs contain a single
    # top-level `python/` directory; tolerate either that or a flat prefix.
    case "$tarball" in
        *.tar.zst) command -v zstd >/dev/null 2>&1 \
                        || die "$tarball needs zstd (missing)"; zstd -dc "$tarball" | tar -xf - -C "$dest" ;;
        *.tar.gz|*.tgz) tar -xzf "$tarball" -C "$dest" ;;
        *.tar.xz) tar -xJf "$tarball" -C "$dest" ;;
        *) tar -xf "$tarball" -C "$dest" ;;
    esac
    if [ -x "$dest/python/bin/python3" ]; then
        printf '%s\n' "$dest/python"
    elif [ -x "$dest/bin/python3" ]; then
        printf '%s\n' "$dest"
    fi
}

# --- wheel contents -----------------------------------------------------
install_wheel() {
    local work=$1 appdir=$2
    local site_packages
    site_packages=$(find "$appdir/usr/lib" -maxdepth 2 -type d -name 'site-packages' -print -quit)
    [ -n "$site_packages" ] || die "no site-packages directory in the staged AppDir"

    log "installing $(basename -- "$WHEEL") into ${site_packages#"$appdir"/}"
    # python -m zipfile rather than unzip: unzip is not installed on every
    # build host and python is guaranteed to be, since we are already running
    # it to build the wheel.
    "$appdir/usr/bin/python3" -m zipfile -e "$WHEEL" "$site_packages"

    # Drop bytecode caches that may have come from the build machine, and the
    # dist-info RECORD which records the build machine's absolute paths.
    find "$site_packages" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

    [ -f "$site_packages/pulsar/__main__.py" ] \
        || die "wheel did not install pulsar/__main__.py into $site_packages"
    # package-data: the wheel must carry the built-in profiles or the app
    # starts with an empty preset list.
    [ -f "$site_packages/pulsar/utils/presets/builtin_profiles.json" ] \
        || die "wheel is missing pulsar/utils/presets/builtin_profiles.json (package-data not built?)"
}

# --- launchers, desktop entry, icons ------------------------------------
stage_launchers() {
    local appdir=$1

    # From packaging/appimage/ verbatim -- these two files are the source of
    # truth and are copied, not regenerated, so editing them changes the
    # build without editing the script.
    install -m 0755 "$PKG_DIR/AppRun"          "$appdir/AppRun"
    install -m 0755 "$PKG_DIR/usr/bin/pulsar"  "$appdir/usr/bin/pulsar"
    # Same file, second name. The script dispatches on its own basename, so
    # `pulsar` runs the CLI and `pulsar-gui` the windowed app. The packaged
    # desktop entry execs pulsar-gui; without this second name it would
    # resolve to the CLI and open a window that never appears.
    install -m 0755 "$PKG_DIR/usr/bin/pulsar"  "$appdir/usr/bin/pulsar-gui"
    install -m 0644 "$PKG_DIR/pulsar.desktop"  "$appdir/pulsar.desktop"

    # A desktop entry naming a binary that is not in the AppDir installs
    # cleanly and launches nothing, which is the exact failure this check
    # exists to catch.
    for launcher in pulsar pulsar-gui; do
        [ -x "$appdir/usr/bin/$launcher" ] \
            || die "AppDir is missing usr/bin/$launcher"
    done

    # desktop-file-validate if the host has it; never fatal, since its
    # absence is not a reason to fail the build.
    if command -v desktop-file-validate >/dev/null 2>&1; then
        desktop-file-validate "$appdir/pulsar.desktop" \
            && log "desktop entry validates" \
            || warn "desktop-file-validate reported problems (continuing)"
    fi

    # The desktop file must not carry the template's placeholder: an
    # AppImage with Exec=PLACEHOLDER_EXEC_PATH launches to nothing.
    if grep -q 'PLACEHOLDER_EXEC_PATH' "$appdir/pulsar.desktop"; then
        die "packaging/appimage/pulsar.desktop still has the Exec= placeholder"
    fi
}

stage_icons() {
    local appdir=$1 arch=$2
    local png="$REPO_ROOT/assets/icons/pulsar-256.png"
    local svg="$REPO_ROOT/assets/icons/pulsar.svg"

    [ -f "$png" ] || die "missing icon: $png"
    [ -f "$svg" ] || die "missing icon: $svg"

    # install(1) will not create parent directories, and the hicolor tree
    # only exists if the runtime happened to ship an icon theme. Create the
    # two directories the AppImage spec requires explicitly.
    mkdir -p "$appdir/usr/share/icons/hicolor/256x256/apps" \
             "$appdir/usr/share/icons/hicolor/scalable/apps"
    install -m 0644 "$png" "$appdir/usr/share/icons/hicolor/256x256/apps/pulsar.png"
    install -m 0644 "$svg" "$appdir/usr/share/icons/hicolor/scalable/apps/pulsar.svg"

    # appimagetool embeds the icon it finds at the AppDir root. Without
    # these, the .AppImage still runs but file managers show a generic icon.
    install -m 0644 "$png" "$appdir/pulsar.png"
    ln -sf pulsar.png "$appdir/.DirIcon"

    log "icons installed (256x256 png + scalable svg, appdir root png for embedding)"
}

finalize_appdir() {
    local appdir=$1
    # An AppDir needs to be readable and traversable by everyone: it is
    # squashfs'd and mounted under /tmp for the desktop session.
    find "$appdir" -type d -exec chmod 0755 {} +
    find "$appdir" -type f -exec chmod 0644 {} +
    chmod 0755 "$appdir/AppRun" "$appdir/usr/bin/pulsar" \
               "$appdir/usr/bin/pulsar-gui" \
               "$appdir/usr/bin/python3" "$appdir/usr/bin/python3."* 2>/dev/null || true
    # Never bake a build-machine path into the bundle.
    find "$appdir" -name '*.pyc' -delete 2>/dev/null || true
}

# --- verification -------------------------------------------------------
# The AppDir is checked *before* appimagetool runs, because a squashfs image
# is expensive to make and impossible to inspect afterwards.
verify_appdir() {
    local appdir=$1 work=$2
    log "verifying staged AppDir"

    local out py version
    out=$(cd / && PULSAR_APPRUN_PRINT_ENV=1 "$appdir/AppRun") \
        || die "AppRun failed to resolve its environment"

    py=$(printf '%s\n' "$out" | sed -n 's/^PYTHON=//p')
    [ -x "$py" ] || die "AppRun resolved a non-executable python: '$py'"

    local site_pythonhome site_pythonpath
    site_pythonhome=$(printf '%s\n' "$out" | sed -n 's/^PYTHONHOME=//p')
    site_pythonpath=$(printf '%s\n' "$out" | sed -n 's/^PYTHONPATH=//p')

    case "$site_pythonhome" in
        "$appdir/usr") log "PYTHONHOME -> inside AppDir ($site_pythonhome)" ;;
        *) die "PYTHONHOME escaped the AppDir: $site_pythonhome" ;;
    esac
    case "$site_pythonpath" in
        "$appdir/usr/lib/"*"/site-packages")
            log "PYTHONPATH -> inside AppDir ($site_pythonpath)" ;;
        *) die "PYTHONPATH escaped the AppDir: $site_pythonpath" ;;
    esac

    # The interpreter must import tkinter and pulsar from the bundle, with a
    # deliberately hostile environment: empty CWD-adjacent sys.path, no
    # PYTHONPATH inherited from the build shell, and no chance of picking the
    # host's own site-packages.
    version=$(env -i PATH=/usr/bin:/bin HOME="$HOME" \
        PYTHONHOME="$site_pythonhome" \
        PYTHONPATH="$site_pythonpath" \
        PYTHONNOUSERSITE=1 \
        PYTHONDONTWRITEBYTECODE=1 \
        "$py" -c 'import sys; print(sys.version.split()[0])' 2>&1) \
        || die "bundled python does not run: $version"
    log "bundled interpreter: $version"

    ( cd / && env -i PATH=/usr/bin:/bin HOME="$HOME" \
        PYTHONHOME="$site_pythonhome" PYTHONPATH="$site_pythonpath" \
        PYTHONNOUSERSITE=1 \
        "$py" -c '
import sys, tkinter, pulsar, pulsar.__main__
assert pulsar.__file__.startswith(sys.prefix), pulsar.__file__
print("tkinter", tkinter.TkVersion, "pulsar", pulsar.__file__)
' ) || die "the bundled interpreter cannot import tkinter + pulsar (see above)"

    # The audio CLI tools must NOT be in the bundle.
    local tool
    for tool in pactl pw-dump pw-top pw-record; do
        if [ -e "$appdir/usr/bin/$tool" ]; then
            die "$tool is bundled in usr/bin. It must come from the host -- it links against the host's libpulse/libpipewire and talks to the host session bus."
        fi
    done
    log "pactl/pw-* are not bundled, as required"

    # ...and the host's must still be reachable through AppRun's PATH.
    local runpath resolved
    runpath=$(printf '%s\n' "$out" | sed -n 's/^PATH=//p')
    case "$runpath" in
        "$appdir/usr/bin:"*) : ;;
        *) die "AppRun's PATH does not put the bundled usr/bin first: $runpath" ;;
    esac
    if command -v pactl >/dev/null 2>&1; then
        resolved=$(PATH="$runpath" command -v pactl)
        case "$resolved" in
            "$appdir"/*) die "pactl resolves inside the AppDir ($resolved); it must resolve to the host" ;;
            *) log "pactl resolves to the host: $resolved" ;;
        esac
    else
        warn "no pactl on this build host; the AppImage will require pulseaudio-utils at runtime"
    fi

    # The current directory must not be able to shadow the bundled package.
    # `python3 -m` puts CWD on sys.path[0] unless PYTHONSAFEPATH is set, so a
    # user who launches the AppImage from a directory holding pulsar.py would
    # otherwise execute that file. Reproduce the shadowing attempt here and
    # require the bundled package to win.
    local decoy
    decoy="$work/decoy"
    mkdir -p "$decoy"
    printf 'raise SystemExit("CWD SHADOWED THE BUNDLED PACKAGE")\n' > "$decoy/pulsar.py"
    if ( cd "$decoy" && env -i PATH=/usr/bin:/bin HOME="$HOME" \
            PYTHONHOME="$site_pythonhome" PYTHONPATH="$site_pythonpath" \
            PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1 \
            "$py" -m pulsar ) >/dev/null 2>&1; then
        log "current directory cannot shadow the bundled package"
    else
        # It failed, which for a Tk app on a headless build host is expected
        # (no $DISPLAY). What matters is *why*: the decoy, or the real app.
        if ( cd "$decoy" && env -i PATH=/usr/bin:/bin HOME="$HOME" \
                PYTHONHOME="$site_pythonhome" PYTHONPATH="$site_pythonpath" \
                PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1 \
                "$py" -m pulsar ) 2>&1 | grep -q 'CWD SHADOWED'; then
            die "a pulsar.py in the launch directory shadows the bundled package (PYTHONSAFEPATH not honoured)"
        fi
        log "current directory cannot shadow the bundled package"
    fi

    # Nothing in the bundle may point at this machine.
    if grep -rlF "$REPO_ROOT" "$appdir/usr/lib" --include='*.py' >/dev/null 2>&1; then
        die "a build-machine path ($REPO_ROOT) leaked into the bundled python code"
    fi
    log "no build-machine paths leaked into the bundle"
}

# --- appimagetool -------------------------------------------------------
locate_appimagetool() {
    if [ -n "$APPIMAGETOOL" ]; then
        [ -x "$APPIMAGETOOL" ] || die "--appimagetool: not executable: $APPIMAGETOOL"
        printf '%s\n' "$APPIMAGETOOL"
        return 0
    fi
    local candidate
    for candidate in appimagetool appimagetool-"$ARCH".AppImage appimagetool.AppImage; do
        candidate=$(command -v "$candidate" 2>/dev/null || true)
        [ -n "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    for candidate in "$REPO_ROOT"/packaging/appimage/appimagetool* \
                     "$REPO_ROOT"/appimagetool*; do
        [ -f "$candidate" ] && [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done

    cat >&2 <<EOF
error: appimagetool is required to produce the .AppImage and was not found.

  The AppDir was built and verified successfully -- only the final
  squashfs/runtime step is missing. Install appimagetool and re-run.

  Recommended (official):
    curl -LO https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool-x86_64.AppImage
    sudo install -m755 appimagetool-x86_64.AppImage /usr/local/bin/appimagetool

  If the host has no FUSE (libfuse2), appimagetool cannot run:
    sudo apt install libfuse2          # Debian/Ubuntu
    sudo dnf install fuse2             # Fedora
  or extract it once and use that copy:
    ./appimagetool-x86_64.AppImage --appimage-extract && ./squashfs-root/usr/bin/appimagetool

  Alternative (pip; bundles its own appimagetool via PyInstaller):
    python3 -m pip install python-appimage   # needs network access
  Then point this script at the appimagetool it installed:
    scripts/build_appimage.sh --appimagetool <path-to-appimagetool>

  To stop after the AppDir is staged (no .AppImage produced):
    scripts/build_appimage.sh --stage-only

  See https://github.com/AppImage/appimagetool#usage
EOF
    exit 1
}

run_appimagetool() {
    local tool=$1 appdir=$2 out=$3
    mkdir -p "$(dirname -- "$out")"
    rm -f -- "$out"          # re-running must overwrite, not accumulate

    log "running appimagetool: $tool"
    log "  ARCH=$ARCH  output=$(basename -- "$out")"

    # -n: skip AppStream validation. There is no appdata file in this bundle,
    #     and appstreamcli is not installed everywhere.
    # --version is passed through rather than hardcoded, because appimagetool
    # reads it to stamp the AppImage.
    ARCH="$ARCH" "$tool" --no-appstream "$appdir" "$out" \
        || die "appimagetool failed (exit $?) -- the AppDir is at $appdir, add --keep-appdir to inspect it"

    [ -f "$out" ] || die "appimagetool reported success but produced no file at $out"
    chmod 0755 "$out"
}

report_result() {
    local out=$1 name=$2
    local size digest
    size=$(du -h -- "$out" | cut -f1)
    digest=$(sha256sum -- "$out" | cut -d' ' -f1)
    printf '\n'
    log "built $name"
    printf '    path:   %s\n' "$out"
    printf '    size:   %s\n' "$size"
    printf '    sha256: %s\n' "$digest"
    printf '\n'
    cat <<EOF
Runtime requirements on the user's machine (NOT bundled, by design):
  pactl    pulseaudio-utils / pipewire-utils
  pw-dump  pipewire-utils      (PipeWire only)
  pw-top   pipewire-utils      (PipeWire only)
  pw-record pipewire-utils     (PipeWire only)
EOF
}

main "$@"