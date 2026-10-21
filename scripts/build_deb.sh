#!/usr/bin/env bash
#
# Build the Debian package and put it in dist/.
#
# WHY THIS EXISTS RATHER THAN A RECIPE LINE IN THE README
#
# `dpkg-buildpackage` writes its output to the PARENT of the source tree,
# not into it. Run from a checkout at ~/gh/pulsar, the .deb appears at
# ~/gh/pulsar_0.1.0_all.deb -- one directory above the repo, outside every
# `ls` anyone runs, which is exactly why "where is the deb?" is a question
# that gets asked. Nothing in the build tree records that it went there.
#
# This wrapper is the single deb build path for both CI and local builds:
# it moves the artifact into dist/ (the one place every format lands) and
# removes the stale copy from the parent directory so the tree cannot
# accumulate orphaned .debs that disagree with dist/ about the version.
#
# USAGE
#
#   scripts/build_deb.sh [options] [-- <extra dpkg-buildpackage args>]
#
# Options:
#   --no-sign            Same as the default today, kept so the intent is
#                        explicit at the call site.
#   --keep-parent-copy   Do not delete the .deb dpkg-buildpackage leaves in
#                        the parent directory.
#   --version VER        Assert the built .deb is VER before publishing it
#                        into dist/.
#   -h, --help           This message.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
DIST_DIR="$REPO_ROOT/dist"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,/# --- guarded defaults ---/p' "${BASH_SOURCE[0]}" \
        | sed -e 's/^# \{0,1\}//' -e '/^$/d' -e '1d' -e '$d'
}

# --- guarded defaults -----------------------------------------------------
SIGN_FLAG="--no-sign"
KEEP_PARENT_COPY=0
EXPECT_VERSION=""
EXTRA_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --no-sign)          SIGN_FLAG="--no-sign"; shift ;;
        --keep-parent-copy) KEEP_PARENT_COPY=1; shift ;;
        --version)          EXPECT_VERSION=${2:?--version needs a value}; shift 2 ;;
        -h|--help)          usage; exit 0 ;;
        --)                 shift; EXTRA_ARGS=("$@"); break ;;
        *)                  die "unknown argument: $1 (try --help)" ;;
    esac
done

command -v dpkg-buildpackage >/dev/null 2>&1 \
    || die "dpkg-buildpackage not found. Install the Debian packaging tools:
  sudo apt-get install -y debhelper dh-python python3-all \\
    python3-setuptools python3-wheel fakeroot lintian"

# `Architecture: all` in debian/control, so the filename is fixed at
# pulsar_<version>_all.deb and needs no architecture lookup.
PARENT_DIR=$(dirname -- "$REPO_ROOT")
PARENT_DEBS=()
if [ -d "$PARENT_DIR" ]; then
    while IFS= read -r f; do
        [ -n "$f" ] && PARENT_DEBS+=("$f")
    done < <(find "$PARENT_DIR" -maxdepth 1 -name 'pulsar_*.deb' -type f 2>/dev/null || true)
fi

log "building in $REPO_ROOT"
log "package version: $(dpkg-parsechangelog -SVersion) (from debian/changelog)"

dpkg-buildpackage -us -uc -b "$SIGN_FLAG" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

BUILT=()
while IFS= read -r f; do
    [ -n "$f" ] && BUILT+=("$f")
done < <(find "$PARENT_DIR" -maxdepth 1 -name 'pulsar_*.deb' -newer "$REPO_ROOT/debian/rules" -type f 2>/dev/null || true)

# `-newer debian/rules` is not reliable enough on its own (a rebuild inside
# the same second, or a clock skew), so fall back to the newest file when
# the freshness filter comes up empty.
if [ "${#BUILT[@]}" -eq 0 ] && [ "${#PARENT_DEBS[@]}" -gt 0 ]; then
    warn "no .deb newer than debian/rules; taking the most recent one in $PARENT_DIR"
    BUILT=("$(ls -1t "${PARENT_DEBS[@]}" | head -n1)")
fi

[ "${#BUILT[@]}" -gt 0 ] || die "dpkg-buildpackage reported success but no pulsar_*.deb appeared in $PARENT_DIR"

mkdir -p "$DIST_DIR"

for deb in "${BUILT[@]}"; do
    base=$(basename -- "$deb")
    if [ -n "$EXPECT_VERSION" ]; then
        case "$base" in
            "pulsar_${EXPECT_VERSION}_"*.deb) ;;
            *) die "expected pulsar_${EXPECT_VERSION}_*.deb, got $base" ;;
        esac
    fi
    # Copy, then remove the parent copy. The copy is the operation that can
    # fail; the cleanup is not allowed to abort a successful build.
    cp -f -- "$deb" "$DIST_DIR/$base"
    if [ "$KEEP_PARENT_COPY" -eq 0 ]; then
        rm -f -- "$deb"
    fi
    log "dist/$base  ($(du -h -- "$DIST_DIR/$base" | cut -f1))"
done

log "dist/ now contains:"
ls -la -- "$DIST_DIR"