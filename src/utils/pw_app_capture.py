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
from typing import Any, Dict, List, Optional, Tuple

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
      - sink_name: For Stream/Output/Audio nodes, the name of the sink the
        stream is currently connected to (e.g. "easyeffects_sink"). For
        Stream/Input/Audio (capture), None. Computed via pw-dump Links
        rather than `pactl list sink-inputs` because PA doesn't always
        enumerate all PW stream nodes.

    Returns an empty list on error. Logs via `logger` if provided.
    """
    data = _run_pw_dump(logger)
    if data is None:
        return []

    # Build lookup tables:
    # - nodes_by_id: PW node id -> node name (for sink lookup)
    nodes_by_id: Dict[int, str] = {}
    # - sink_for_input_node: for each PW node id that's a Stream/Input/Audio
    #   (an app playing audio), the sink name it's connected to (via Links)
    sink_for_input_node: Dict[int, str] = {}

    for d in data:
        if d.get("type") != "PipeWire:Interface:Node":
            continue
        info = d.get("info", {})
        props = info.get("props", {})
        node_id_raw = d.get("id")
        if not isinstance(node_id_raw, int):
            continue
        node_name = props.get("node.name", "")
        nodes_by_id[node_id_raw] = node_name

    # Now walk Links: a link has output-node-id and input-node-id. The
    # naming is from the SPA graph perspective — output-port of one
    # node feeds into input-port of another. For an app playing audio,
    # the app's Stream/Output/Audio node is the OUTPUT side (its audio
    # comes out) and the sink is the INPUT side (audio goes in).
    #
    # So for each link: output_node = source of audio, input_node = sink.
    # We map source_node_id -> sink_name for Stream/Output/Audio nodes.
    sink_for_input_node: Dict[int, str] = {}
    for d in data:
        if d.get("type") != "PipeWire:Interface:Link":
            continue
        info = d.get("info", {})
        in_id = info.get("input-node-id")
        out_id = info.get("output-node-id")
        if not isinstance(in_id, int) or not isinstance(out_id, int):
            continue
        out_name = nodes_by_id.get(out_id, "")
        in_name = nodes_by_id.get(in_id, "")
        if not out_name or not in_name:
            continue
        # The link's output side is the audio SOURCE (e.g. an app's
        # Stream/Output/Audio node). The input side is the SINK
        # (where the audio is going). For our lookup, we want to
        # find the sink for each stream node: out_id (source) -> in_name (sink).
        if sink_for_input_node.get(out_id) is None:
            sink_for_input_node[out_id] = in_name

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
        sink_name = sink_for_input_node.get(node_id)
        out.append({
            "id": node_id,
            "node_name": node_name,
            "media_class": mc,
            "application_name": app_name,
            "binary": binary,
            "pid": pid,
            "description": f"{app_name} ({pid or '?'}) - {mc}",
            "sink_name": sink_name,
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


# ----------------------------------------------------------------------
# File capture — uses pw-record to record a specific PipeWire stream to
# a WAV file. This is the "Capture this app to a file" action. It works
# without any portal interaction and is the recommended path on systems
# whose portal backend doesn't support APPLICATION source type (e.g. the
# GTK fallback portal on non-GNOME desktops).
# ----------------------------------------------------------------------


def _which_pw_record() -> Optional[str]:
    """Return the path to pw-record, or None if not installed."""
    return shutil.which("pw-record")


def pw_record_available() -> bool:
    """True iff `pw-record` is installed (the pipewire-tools package)."""
    return _which_pw_record() is not None


class PwRecordError(Exception):
    """Raised when pw-record fails to launch or terminates with an error."""


def start_pw_record(
    target_node_id: int,
    output_path: str,
    *,
    sample_rate: int = 48000,
    channels: int = 2,
    extra_args: Optional[List[str]] = None,
    logger=None,
) -> "subprocess.Popen":
    """Launch pw-record targeting a specific node ID, writing to output_path.

    The function returns a Popen handle — the recording runs in the
    background. The caller is responsible for stopping it (call
    `stop_pw_record`) and for surfacing the output to the user.

    Args:
        target_node_id: PipeWire node ID to capture (from discover_app_audio_nodes).
        output_path: Absolute path for the output WAV file. Must be writable.
        sample_rate: Sample rate in Hz. Default 48000 (matches most audio devices).
        channels: Channel count. Default 2 (stereo). Set 1 for mono capture.
        extra_args: Additional pw-record flags. Use with care.
        logger: Optional callback for status messages.

    Returns:
        subprocess.Popen instance for the running pw-record process.
    """
    pw_record = _which_pw_record()
    if not pw_record:
        raise PwRecordError(
            "pw-record not found on PATH. Install pipewire-tools."
        )
    cmd = [
        pw_record,
        "--target", str(target_node_id),
        "--rate", str(sample_rate),
        "--channels", str(channels),
        output_path,
    ]
    if extra_args:
        cmd.extend(extra_args)
    if logger:
        logger(f"Launching: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Don't inherit — pw-record is a long-running CLI tool, not a
            # child we want to wait on. The caller manages its lifecycle.
            close_fds=True,
        )
    except OSError as e:
        raise PwRecordError(f"Failed to launch pw-record: {e}") from e
    return proc


def stop_pw_record(
    proc: "subprocess.Popen",
    *,
    timeout: float = 5.0,
    logger=None,
) -> Tuple[int, str, str]:
    """Stop a running pw-record process and return its exit info.

    Sends SIGINT first (pw-record's polite shutdown signal), waits up
    to `timeout` seconds, then SIGKILL if it didn't exit.

    Returns:
        (returncode, stdout_text, stderr_text)
    """
    if proc.poll() is not None:
        # Already exited
        out, err = proc.communicate()
        return proc.returncode, out.decode("utf-8", errors="replace"), \
            err.decode("utf-8", errors="replace")
    try:
        proc.send_signal(2)  # SIGINT
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if logger:
                logger("pw-record did not stop on SIGINT, killing")
            proc.kill()
            out, err = proc.communicate()
    except ProcessLookupError:
        # Already gone
        out, err = b"", b""
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace") if out else "",
        err.decode("utf-8", errors="replace") if err else "",
    )
