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


def looks_like_profile_entry(_name, entry) -> bool:
    """Does a flat legacy entry look like a profile rather than a preset?

    A flat legacy file -- no wrapper key, every top-level name an entry --
    has been seen holding both kinds at once, so each manager has to pick
    its own out rather than claiming the lot.

    `description` is deliberately NOT in this list. Every builtin preset
    carries one, so including it would classify presets as profiles and
    both managers would claim the same entry.
    """
    if not isinstance(entry, dict):
        return False
    return any(k in entry for k in ("devices", "routing", "schema_version"))


def looks_like_preset_entry(name, entry) -> bool:
    """The mirror of the above.

    Anything not profile-shaped is ours, which is the safe direction: a
    preset claimed by nobody is merely left behind, while a profile
    mistaken for a preset would be written to the wrong file and fail to
    load.
    """
    return not looks_like_profile_entry(name, entry)


def take_legacy_section(old_file, dest_file, my_key, other_keys=(),
                        whole_file_is_mine=False, entry_filter=None) -> bool:
    """Move ONE section out of the legacy combined presets file.

    The pre-split `user_presets.json` held presets and profiles together
    under two top-level keys. Both managers used to copy the whole file
    into their own new file and then rename the source away -- so whichever
    constructed first consumed everything and the other found nothing,
    silently losing half the user's data. See issue #14, reproduced in both
    orders.

    This takes only ``my_key``'s contents, so each manager ends up with its
    own data and nothing else -- in particular the legacy file's wrapper
    keys do not become entries called "presets" and "profiles".

    TWO legacy shapes exist, and both are in the wild:

      * sectioned -- ``{"presets": {...}, "profiles": {...}}``, written
        once presets and profiles had diverged in shape.
      * flat -- ``{"MyGame": {"channels": "2"}}`` or ``{"OldProfile":
        {"devices": []}}``, the original format, where every top-level key
        is an entry and there is no wrapper. ``whole_file_is_mine`` takes
        that shape; ``entry_filter`` picks out the entries that belong to
        this manager, because a flat file has been seen holding both
        presets and profiles and each must claim only its own.

    The source is retired only once no other manager has anything left to
    take from it, so the second manager still finds its section. Until then
    it is left byte-for-byte alone.

    Returns True if a section was migrated.
    """
    try:
        with open(old_file) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    section = data.get(my_key)
    if isinstance(section, dict) and section:
        # Sectioned legacy: the other managers' sections are still here.
        unclaimed = [k for k in other_keys
                     if isinstance(data.get(k), dict) and data.get(k)]
    elif whole_file_is_mine and data:
        # Flat legacy: every top-level key is an entry, and a flat file has
        # been seen holding both kinds. Filter to what is ours.
        section = {k: v for k, v in data.items()
                   if entry_filter is None or entry_filter(k, v)}
        unclaimed = [k for k in data if k not in section]
    else:
        return False

    if not section:
        return False
    if not write_json_atomic(dest_file, section):
        return False

    # Retire the source only when this manager demonstrably took all of it.
    # If anything is left another manager could still want, the file stays
    # byte-for-byte -- a leftover legacy file is a harmless backup, whereas
    # deleting one the other manager still needs is issue #14.
    if not unclaimed:
        try:
            os.replace(old_file, old_file + ".migrated")
        except OSError:
            pass
    return True
