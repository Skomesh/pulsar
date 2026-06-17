"""
App Audio Capture for Pulsar (Phase 5a).

Provides two things:
1. ``discover_app_audio_nodes()`` — list the currently-running app audio
   streams (Stream/Input/Audio and Stream/Output/Audio nodes) so the user
   can see what apps are making sound and their PipeWire node IDs.

2. ``PortalCapture`` — a thin wrapper around the xdg-desktop-portal
   ScreenCast API for per-application audio capture. When the user
   clicks "Capture App Audio", the portal shows a native picker dialog
   ("which app's audio do you want to capture?"), and on success we
   receive a PipeWire remote FD we can use to subscribe to the
   captured node.

Why two pieces:
- ``discover_app_audio_nodes()`` is trivially testable and useful on its
  own — it gives the user a way to copy a node ID into OBS or any other
  PipeWire-aware tool without going through the portal flow.
- The portal flow is the more user-friendly path: pick from a native
  app picker instead of eyeballing node IDs. We use ``gdbus`` as a
  subprocess to talk to the portal because the dbus-python stdlib
  binding has awkward signature handling for ``a{sv}`` arguments and
  for FD passing.

Both pieces share the same node-ID concept, so the same UI surface
("Copy node ID", "Show in OBS") works for both.
"""

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

# Portal ScreenCast source type bit flags (see xdg-desktop-portal docs).
SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2
SOURCE_TYPE_APPLICATION = 4

SCREENCAST_DEST = "org.freedesktop.portal.Desktop"
SCREENCAST_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"


def portal_available() -> bool:
    """Return True if the xdg-desktop-portal ScreenCast interface is reachable."""
    return shutil.which("gdbus") is not None


def portal_screencast_version() -> Optional[int]:
    """Return the portal ScreenCast interface version, or None if unreachable."""
    if not portal_available():
        return None
    try:
        out = subprocess.check_output(
            [
                "gdbus", "call",
                "--session",
                "--dest", SCREENCAST_DEST,
                "--object-path", SCREENCAST_PATH,
                "--method", "org.freedesktop.DBus.Properties.Get",
                SCREENCAST_IFACE, "version",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    # Output looks like "(<uint32 5>,)" — note the angle brackets in gdbus
    # format. We strip them before parsing.
    out = out.decode("utf-8", errors="replace").strip().replace("<", "").replace(">", "")
    # out is now "(uint32 5,)"
    if out.startswith("(uint32 ") and out.endswith(",)"):
        try:
            return int(out[len("(uint32 "):-len(",)")])
        except ValueError:
            return None
    return None


def portal_available_source_types() -> Optional[int]:
    """Return the bit flags of ScreenCast source types the portal supports.

    Returns None if the portal isn't reachable.
    """
    if not portal_available():
        return None
    try:
        out = subprocess.check_output(
            [
                "gdbus", "call",
                "--session",
                "--dest", SCREENCAST_DEST,
                "--object-path", SCREENCAST_PATH,
                "--method", "org.freedesktop.DBus.Properties.Get",
                SCREENCAST_IFACE, "AvailableSourceTypes",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = out.decode("utf-8", errors="replace").strip().replace("<", "").replace(">", "")
    # out is now "(uint32 7,)"
    if out.startswith("(uint32 ") and out.endswith(",)"):
        try:
            return int(out[len("(uint32 "):-len(",)")])
        except ValueError:
            return None
    return None


def supports_app_capture() -> bool:
    """Return True if the portal can do per-application audio capture.

    Requires the APPLICATION bit in AvailableSourceTypes. The MONITOR
    and WINDOW bits are not relevant for app audio.
    """
    flags = portal_available_source_types()
    if flags is None:
        return False
    return bool(flags & SOURCE_TYPE_APPLICATION)


# ----------------------------------------------------------------------
# Node discovery — pure introspection, no portal needed
# ----------------------------------------------------------------------


def _run_pw_dump(logger=None) -> Optional[List[Dict[str, Any]]]:
    """Run `pw-dump` and return the parsed JSON list, or None on failure."""
    if not shutil.which("pw-dump"):
        if logger:
            logger("pw-dump not found on PATH; install pipewire-tools")
        return None
    try:
        out = subprocess.check_output(
            ["pw-dump"], stderr=subprocess.DEVNULL, timeout=5
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        if logger:
            logger(f"pw-dump failed: {e}")
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        if logger:
            logger(f"pw-dump returned invalid JSON: {e}")
        return None


def discover_app_audio_nodes(logger=None) -> List[Dict[str, Any]]:
    """Return all currently-active app audio streams on the system.

    Each entry is a dict with:
      - id: PW node ID (int) — paste this into OBS's PipeWire audio source
      - node_name: PW node.name (string) — alternative OBS accepts
      - media_class: e.g. "Stream/Input/Audio" (mic, capture), "Stream/Output/Audio" (app playback)
      - application_name: e.g. "LibreWolf" (from props.application.name)
      - binary: e.g. "librewolf" (from props.application.process.binary)
      - pid: int or None (from props.application.process.id)
      - description: human-readable string ("LibreWolf - 1234 - Stream/Output/Audio")

    Returns an empty list on error. Logs via `logger` if provided.
    """
    data = _run_pw_dump(logger)
    if data is None:
        return []

    out: List[Dict[str, Any]] = []
    for d in data:
        if d.get("type") != "PipeWire:Interface:Node":
            continue
        info = d.get("info", {})
        props = info.get("props", {})
        mc = props.get("media.class", "")
        # App audio shows up as Stream/Input/Audio (capture streams) and
        # Stream/Output/Audio (playback streams). Hardware sinks/sources
        # have media.class Audio/Sink/Audio/Source and no app metadata —
        # we exclude those.
        if mc not in ("Stream/Input/Audio", "Stream/Output/Audio"):
            continue
        app_name = props.get("application.name", "") or props.get(
            "application.process.binary", ""
        )
        if not app_name:
            # No app metadata — skip (some streams don't have it)
            continue
        node_id = d.get("id")
        node_name = props.get("node.name", "")
        binary = props.get("application.process.binary", "")
        try:
            pid = int(props.get("application.process.id", "")) if \
                props.get("application.process.id") else None
        except ValueError:
            pid = None
        out.append({
            "id": node_id,
            "node_name": node_name,
            "media_class": mc,
            "application_name": app_name,
            "binary": binary,
            "pid": pid,
            "description": f"{app_name} ({pid or '?'}) - {mc}",
        })

    # Stable, useful ordering: input streams (capture) first, then output
    # streams (playback), both sorted by app name.
    out.sort(key=lambda n: (n["media_class"], n["application_name"].lower()))
    return out


# ----------------------------------------------------------------------
# Portal-based capture (best UX, requires xdg-desktop-portal + a desktop
# backend that supports APPLICATION source type)
# ----------------------------------------------------------------------


def _parse_object_path(s: str) -> Optional[str]:
    """Extract an object path from gdbus output like
    ``(objectpath '/org/freedesktop/portal/desktop/session/X/Y',)``
    or ``(objectpath '/foo',)``.
    """
    # Find "'/something'"
    start = s.find("'/")
    if start == -1:
        return None
    end = s.find("'", start + 1)
    if end == -1:
        return None
    return s[start + 1:end]


class PortalCaptureError(Exception):
    """Raised when a portal call fails or returns an error response."""


class PortalCapture:
    """Drive a per-app audio capture session via xdg-desktop-portal.

    Workflow (matches OBS Studio's PipeWire audio source implementation):
    1. CreateSession — open a session with the portal
    2. SelectSources — ask for APPLICATION source type
    3. Start — show the picker dialog ("which app's audio?")
    4. OpenPipeWireRemote — get an FD to a PipeWire remote representing
       the captured stream. The user can then read nodes from that
       remote to find the captured app's node ID.

    This class is a stateful controller — the caller drives it step by
    step, since each step can either block on a user dialog (Start) or
    fail with a portal response code. We surface the result code so the
    UI can decide what to do (e.g. "user cancelled" vs "portal error").
    """

    def __init__(self):
        if not portal_available():
            raise PortalCaptureError(
                "gdbus not found on PATH; install glib2 (or libglib2.0-bin)"
            )
        self._token = f"pulsar_{os.getpid()}_{int(time.time() * 1000)}"
        self._session_handle: Optional[str] = None

    @staticmethod
    def _gdbus_call(method: str, *args: str, timeout: int = 60) -> str:
        """Run `gdbus call ...` and return stdout as a string.

        `method` is the fully-qualified method name, e.g.
        ``org.freedesktop.portal.ScreenCast.CreateSession``. The remaining
        args are passed as method arguments to gdbus in their order.

        The portal's Start method can block until the user clicks OK in
        the dialog, so the default timeout is 60s.
        """
        cmd = [
            "gdbus", "call",
            "--session",
            "--dest", SCREENCAST_DEST,
            "--object-path", SCREENCAST_PATH,
            "--method", method,
            *args,
        ]
        out = subprocess.check_output(
            cmd, stderr=subprocess.PIPE, timeout=timeout
        )
        return out.decode("utf-8", errors="replace").strip()

    def create_session(self) -> str:
        """Open a session with the portal. Returns the session handle."""
        if not supports_app_capture():
            raise PortalCaptureError(
                "Portal does not support APPLICATION source type. "
                f"AvailableSourceTypes=0x{portal_available_source_types() or 0:x}. "
                "Make sure your desktop portal backend (e.g. xdg-desktop-portal-gtk, "
                "xdg-desktop-portal-kde, xdg-desktop-portal-wlr) supports it."
            )
        out = self._gdbus_call(
            f"{SCREENCAST_IFACE}.CreateSession",
            f"{{'handle_token': <'{self._token}_req'>, "
            f"'session_handle_token': <'{self._token}_sess'>}}",
        )
        handle = _parse_object_path(out)
        if not handle:
            raise PortalCaptureError(
                f"CreateSession: could not parse handle from output: {out!r}"
            )
        self._session_handle = handle
        return handle

    def select_sources(self, multiple: bool = False) -> None:
        """Ask the portal to enable APPLICATION source selection."""
        if not self._session_handle:
            raise PortalCaptureError("Call create_session() first")
        out = self._gdbus_call(
            f"{SCREENCAST_IFACE}.SelectSources",
            f"'{self._session_handle}'",
            f"{{'handle_token': <'{self._token}_sel'>, "
            f"'types': <uint32 {SOURCE_TYPE_APPLICATION}>, "
            f"'multiple': <{'true' if multiple else 'false'}>, "
            f"'persist_mode': <uint32 2>}}",
        )
        # Output is "(uint32 0,)" on success or an error code on failure
        if "(uint32 0," not in out:
            raise PortalCaptureError(f"SelectSources failed: {out}")

    def start(self) -> str:
        """Show the picker dialog. Returns the Start handle on success.

        Blocks until the user dismisses the dialog (clicks Share or
        Cancel). Caller should catch PortalCaptureError for cancellation
        by checking the response code.
        """
        if not self._session_handle:
            raise PortalCaptureError("Call create_session() first")
        # parent_window: empty string is allowed per spec for non-embedded
        # portals. We have no X11/Wayland window handle from this Python
        # process; passing "" is the documented fallback.
        out = self._gdbus_call(
            f"{SCREENCAST_IFACE}.Start",
            f"'{self._session_handle}'",
            "''",
            f"{{'handle_token': <'{self._token}_start'>}}",
            timeout=120,  # User can take a while to pick
        )
        handle = _parse_object_path(out)
        if not handle:
            raise PortalCaptureError(
                f"Start: could not parse handle from output: {out!r}"
            )
        return handle
