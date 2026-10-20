"""Where Pulsar reads and writes data on disk.

Split out because three things need the same answer: the preset manager, the
profile manager, and packaging. Getting this wrong is not cosmetic — see
``user_data_dir`` for why.

The short version: user data goes under the XDG data dir, never relative to
the current working directory. A relative path works from a source checkout
and breaks in every packaged install, because there the working directory is
a read-only bundle.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

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


def write_json_atomic(path, data, indent=2) -> bool:
    """Serialise ``data`` to ``path`` without ever destroying the old file.

    Write to a sibling temp file, flush and fsync it, then ``os.replace``
    over the target. Rename within a directory is atomic, so a reader sees
    either the complete old file or the complete new one -- never a
    half-written one.

    Why this exists: the obvious ``open(path, 'w')`` TRUNCATES before
    ``json.dump`` writes a single byte. A write that fails partway --
    ENOSPC, EIO, a quota, an NFS remount -- leaves unparseable garbage
    where the user's saved presets were, with no backup. Since these files
    are the only record of work the user did by hand, that is silent,
    permanent data loss from something the user experienced as a failed
    save.

    Returns True on success, False if anything went wrong. Never raises:
    every caller here already handles failure by reporting it, and a
    raised exception from a save path would take down the GUI.

    A caller-supplied ``json.dump`` failure (the usual way this is
    exercised) lands in the same handler: the temp file is removed and the
    original is untouched, because it was never opened for writing.
    """
    target = Path(path)
    temp = target.with_name(target.name + f".tmp{os.getpid()}")
    try:
        with open(temp, "w") as handle:
            json.dump(data, handle, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return True
    except (OSError, TypeError, ValueError):
        # Leave the original exactly as it was and drop the partial temp.
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return False


def read_json_dict(path) -> Dict[str, Any]:
    """Load JSON, returning {} for anything unusable.

    Returns {} rather than raising when the file is missing, unreadable,
    not valid JSON, or valid JSON whose top level is not an object -- a
    list or a bare string where a dict of entries belongs. The callers
    merge this into their data, and a list would propagate an
    ``AttributeError`` on ``.update()`` straight into the GUI -- in one
    case far enough to stop the window opening at all.
    """
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data
