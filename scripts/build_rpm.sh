#!/usr/bin/env bash
#
# Build the RPM and put it in dist/.
#
# rpmbuild writes into its own _topdir (RPMS/, SRPMS/, BUILD/), which is
# scratch, not an output location. This wrapper is the single rpm build
# path for both CI and local builds: it runs the build, copies the
# finished .rpm out of _topdir into dist/, and names what it produced.
#
# Local builds on a non-Fedora box need Fedora's macro definitions and a
# Fedora-shaped Python; --rpmbuild points at whatever wrapper supplies
# those. See the "What is verified" table in packaging/README.md.
#
# USAGE
#
#   scripts/build_rpm.sh [options] [-- <extra rpmbuild args>]
#
# Options:
#   --rpmbuild CMD      Command that invokes rpmbuild. May be a wrapper
#                       script (default: rpmbuild from $PATH).
#   --topdir DIR        rpmbuild _topdir (default: rpmbuild/ in the repo).
#   --define NAME=VAL   Passed through to rpmbuild. Repeatable. Both
#                       `NAME=VAL` and `NAME VAL` are accepted here, but
#                       only the space form is ever handed to rpmbuild:
#                       rpm 6.x reads `--define dist=.fc42` as the macro
#                       `%dist` with the body `=.fc42` and emits a warning
#                       while silently building the wrong dist tag.
#   --version VER       Override %version. Default: Version: from
#                       packaging/pulsar.spec, which must already agree
#                       with pyproject.toml (its %prep asserts this).
#   -h, --help          This message.
#
# Example, Fedora:
#   scripts/build_rpm.sh
#
# Example, non-Fedora host with a Fedora-shaped toolchain wrapper:
#   scripts/build_rpm.sh --rpmbuild /tmp/runbuild-fedora.sh \
#       --topdir /tmp/rtf --define dist=.fc42

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SPEC="$REPO_ROOT/packaging/pulsar.spec"
DIST_DIR="$REPO_ROOT/dist"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,/# --- guarded defaults ---/p' "${BASH_SOURCE[0]}" \
        | sed -e 's/^# \{0,1\}//' -e '/^$/d' -e '1d' -e '$d'
}

# --- guarded defaults -----------------------------------------------------
RPMBUILD_CMD="rpmbuild"
TOPDIR="$REPO_ROOT/rpmbuild"
VERSION=""
DEFINES=()      # flat pairs: --define "NAME VALUE" --define "NAME VALUE" ...
EXTRA_ARGS=()

normalise_define() {
    # rpmbuild only understands `--define "NAME VALUE"`. Accept both
    # spellings on our command line so a caller who writes NAME=VAL does
    # not get a silently-wrong macro (see the note in the header).
    case "$1" in
        *=*) printf '%s\n' "${1%%=*} ${1#*=}" ;;
        *)   printf '%s\n' "$1" ;;
    esac
}

while [ $# -gt 0 ]; do
    case "$1" in
        --rpmbuild) RPMBUILD_CMD=${2:?--rpmbuild needs a command}; shift 2 ;;
        --topdir)   TOPDIR=${2:?--topdir needs a directory}; shift 2 ;;
        --define)   DEFINES+=(--define "$(normalise_define "${2:?--define needs NAME=VAL}")"); shift 2 ;;
        --version)  VERSION=${2:?--version needs a value}; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        --)         shift; EXTRA_ARGS=("$@"); break ;;
        *)          die "unknown argument: $1 (try --help)" ;;
    esac
done

[ -f "$SPEC" ] || die "missing spec: $SPEC"
command -v "$RPMBUILD_CMD" >/dev/null 2>&1 \
    || die "$RPMBUILD_CMD not found. On a Fedora box, install it with:
  sudo dnf install -y rpm-build python3-devel python3-pip desktop-file-utils
Outside Fedora, pass --rpmbuild PATH pointing at a wrapper that supplies
Fedora's pyproject-rpm-macros and a Fedora-shaped Python."

if [ -z "$VERSION" ]; then
    VERSION=$(sed -n 's/^Version:[[:space:]]*//p' "$SPEC" | head -1)
fi
[ -n "$VERSION" ] || die "could not read Version: from $SPEC"

mkdir -p "$DIST_DIR" "$TOPDIR" "$TOPDIR/SOURCES"

log "building $SPEC (version $VERSION)"
log "rpmbuild: $RPMBUILD_CMD   _topdir: $TOPDIR"

# --- Source0 --------------------------------------------------------------
# The spec says `Source0: pulsar-<version>.tar.gz` and %prep only unpacks it.
# rpmbuild 6.x does NOT create that tarball from the working tree for us --
# it fails in %prep with "File /...SOURCES/pulsar-<ver>.tar.gz: No such file
# or directory". Worse, if an old tarball is lying around it is used
# silently: the build then succeeds against sources that predate the
# current tree, which is how a package ends up missing a console script.
# So: make the tarball ourselves, from tracked files at the working-tree
# state, whenever it is not already present and current.
SOURCE_TARBALL="$TOPDIR/SOURCES/pulsar-${VERSION}.tar.gz"

needs_tarball=1
if [ -f "$SOURCE_TARBALL" ]; then
    # Newer than the spec header means it was made from this version's tree.
    if [ "$SOURCE_TARBALL" -nt "$SPEC" ]; then
        needs_tarball=0
    else
        warn "$SOURCE_TARBALL is older than $SPEC; rebuilding it"
    fi
fi

if [ "$needs_tarball" -eq 1 ]; then
    command -v git >/dev/null 2>&1 \
        || die "no git and no $SOURCE_TARBALL. Install git, or place the
  source tarball there yourself."
    git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 \
        || die "$REPO_ROOT is not a git checkout and there is no $SOURCE_TARBALL"
    log "creating Source0 from the working tree: pulsar-${VERSION}.tar.gz"
    # Tracked files only: dist/, build/, .venv/ and every other gitignored
    # path are output, and shipping them inside the source package is how a
    # 70 MB AppImage ends up inside an RPM's %files list.
    # -C before -T: tar treats options after the file list as positional
    # and then silently ignores them.
    git -C "$REPO_ROOT" ls-files -z \
        | tar --null -czf "$SOURCE_TARBALL" -C "$REPO_ROOT" \
              --transform "s,^,pulsar-${VERSION}/," \
              -T -
    log "Source0: $(du -h -- "$SOURCE_TARBALL" | cut -f1), $(git -C "$REPO_ROOT" ls-files | wc -l) files"
fi

# --nodeps is NOT passed to rpmbuild: it would let the build succeed past a
# genuinely missing BuildRequires, which is the failure this spec's guard
# exists to catch.
#
# shellcheck disable=SC2086
( cd "$REPO_ROOT" && "$RPMBUILD_CMD" \
    --define "_topdir $TOPDIR" \
    --define "_version $VERSION" \
    ${DEFINES[@]+"${DEFINES[@]}"} \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" \
    "$SPEC" )

BUILT=()
while IFS= read -r f; do
    [ -n "$f" ] && BUILT+=("$f")
done < <(find "$TOPDIR/RPMS" -name '*.rpm' -type f 2>/dev/null || true)

[ "${#BUILT[@]}" -gt 0 ] || die "rpmbuild reported success but no .rpm found under $TOPDIR/RPMS"

for rpm in "${BUILT[@]}"; do
    base=$(basename -- "$rpm")
    cp -f -- "$rpm" "$DIST_DIR/$base"
    log "dist/$base  ($(du -h -- "$DIST_DIR/$base" | cut -f1))"
done

log "dist/ now contains:"
ls -la -- "$DIST_DIR"