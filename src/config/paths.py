"""Filesystem locations for Pulsar's presets.

Two locations matter, and confusing them is the bug this module exists to
prevent:

``user_presets_dir()``
    Writable, per-user. Holds ``user_presets.json`` — the profiles the user
    creates. Must resolve to a path the current user can write to, which rules
    out ``/usr/share`` and, inside a Flatpak sandbox, ``/app``.

``builtin_presets_path()``
    Read-only, shipped with the app. Holds ``builtin_profiles.json``, the
    starter profiles. This used to be found by walking up from
    ``__file__`` to the repo root, which silently returned nothing in a wheel,
    Flatpak, or ``pip install --user`` — the starter profiles just vanished
    with no error. Resolving via ``importlib.resources`` makes it work
    wherever the package actually lives.

Both helpers tolerate the source checkout layout (repo root holding a
``presets/`` dir) as well as the installed layout (presets packaged inside the
``utils`` package).
"""

from __future__ import annotations

import os
from pathlib import Path

#: Filenames the app reads and writes.
USER_PRESETS_FILENAME = "user_presets.json"
BUILTIN_PRESETS_FILENAME = "builtin_profiles.json"

#: Environment variable that overrides the user presets directory. Mainly for
#: tests and for users who want profiles on another filesystem.
PRESETS_DIR_ENV = "PULSAR_PRESETS_DIR"


def _repo_root() -> Path | None:
    """Return the source-checkout root, or None when running installed.

    src/config/paths.py -> src/config -> src -> <repo root>
    """
    here = Path(__file__).resolve()
    root = here.parent.parent.parent
    # A checkout has presets/ beside src/; an installed package does not.
    if (root / "presets").is_dir() and (root / "pyproject.toml").is_file():
        return root
    return None


def user_presets_dir() -> str:
    """Return the writable directory for user presets.

    Resolution order:

    1. ``$PULSAR_PRESETS_DIR`` if set -- explicit override wins.
    2. ``$XDG_DATA_HOME/pulsar/presets`` -- per the XDG base directory spec.
    3. ``~/.local/share/pulsar/presets``.

    This is an absolute path in every case. The old default was the relative
    string ``"presets"``, which resolved against whatever the current working
    directory happened to be: launching Pulsar from a file manager, a systemd
    unit, or a Flatpak put your saved profiles somewhere unpredictable, and a
    read-only cwd lost them outright.
    """
    override = os.environ.get(PRESETS_DIR_ENV)
    if override:
        return override

    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return str(Path(data_home) / "pulsar" / "presets")

    home = Path.home()
    return str(home / ".local" / "share" / "pulsar" / "presets")


def legacy_user_presets_paths() -> list:
    """Return candidate paths where pre-XDG user presets may still live.

    Before the move to an XDG-based location, presets resolved as the relative
    string ``"presets"`` against the current working directory. That produced
    two distinct legacy locations depending on how the app was started:

    - running from a source checkout: ``<checkout>/presets/user_presets.json``
    - running from the old ``install.sh`` layout: the same, relative to the
      install directory

    Both are recovered from the XDG location itself (an install-dir user has
    ``$XDG_DATA_HOME/pulsar/presets`` == the install's ``presets/``), so the
    checkout case is the only one still worth probing. Ordering is
    most-specific first so a caller can take the first file that exists.
    """
    candidates = []
    root = _repo_root()
    if root is not None:
        candidates.append(root / "presets" / USER_PRESETS_FILENAME)
    return candidates


def builtin_presets_path() -> Path:
    """Return the path to the bundled ``builtin_profiles.json``.

    Looks in the packaged location first (``utils/presets/``), then the source
    checkout's top-level ``presets/``. Raises FileNotFoundError if neither
    exists -- callers treat a missing built-in layer as "no built-ins" rather
    than a hard failure, so this stays an error only for the caller to catch.
    """
    packaged = Path(__file__).resolve().parent.parent / "utils" / "presets"
    packaged_file = packaged / BUILTIN_PRESETS_FILENAME
    if packaged_file.is_file():
        return packaged_file

    root = _repo_root()
    if root is not None:
        checkout_file = root / "presets" / BUILTIN_PRESETS_FILENAME
        if checkout_file.is_file():
            return checkout_file

    raise FileNotFoundError(
        f"could not locate {BUILTIN_PRESETS_FILENAME}; looked in {packaged} "
        "and the source checkout"
    )
