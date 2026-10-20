"""Where Pulsar reads and writes data on disk.

Split out because three things need the same answer: the preset manager, the
profile manager, and packaging. Getting this wrong is not cosmetic — see
``user_data_dir`` for why.

The short version: user data goes under the XDG data dir, never relative to
the current working directory. A relative path works from a source checkout
and breaks in every packaged install, because there the working directory is
a read-only bundle.
"""

import os
from pathlib import Path
from typing import Optional

#: Fallback when $XDG_DATA_HOME is unset, per the XDG Base Directory spec.
XDG_DATA_HOME_FALLBACK = "~/.local/share"


def _xdg_data_home() -> Path:
    """Return the user's XDG data dir, honouring $XDG_DATA_HOME."""
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(XDG_DATA_HOME_FALLBACK).expanduser()


def user_data_dir(app: str = "pulsar") -> Path:
    """Return the writable per-user data directory, creating it if needed.

    Defaults to ``$XDG_DATA_HOME/<app>``, falling back to
    ``~/.local/share/<app>``.

    Creating it here rather than at each call site means the directory exists
    before anything tries to write into it, and that a read-only or missing
    parent fails loudly at one known place instead of somewhere inside a
    save routine.

    Args:
        app: Directory name under the XDG data home.

    Returns:
        The resolved Path. The directory is guaranteed to exist unless
        creation failed, in which case the caller gets the path anyway and
        will fail on write with a real OSError.
    """
    path = _xdg_data_home() / app
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Deliberately not fatal. A caller that only reads (e.g. listing
        # built-in presets) should still work on a machine where the data
        # home is not writable; writes will raise with a clear path.
        pass
    return path


def user_presets_dir(app: str = "pulsar") -> Path:
    """Return the directory holding user-saved presets and profiles."""
    return user_data_dir(app) / "presets"


def legacy_presets_dir() -> Optional[Path]:
    """Return the pre-XDG location, if it exists, for migration.

    Older Pulsar resolved ``presets/`` relative to the working directory.
    Two places actually produced it in practice:

    - a source checkout, where it sat next to ``src/``
    - ``install.sh``, which copies the whole tree to
      ``~/.local/share/pulsar`` and so left it at
      ``~/.local/share/pulsar/presets``

    Returns the first that exists, else None.
    """
    candidates = [
        Path.cwd() / "presets",
        Path(XDG_DATA_HOME_FALLBACK).expanduser() / "pulsar" / "presets",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None
