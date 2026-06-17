"""
PipeWire graph diagnostics for Pulsar (Phase 7).

Provides a structured snapshot of the current PipeWire graph for the
Diagnostics tab. Wraps `pw-dump` (the official introspection tool)
and parses the JSON output into Python data structures suitable for
direct UI display.

Why pw-dump (not wpctl or pactl):
- pw-dump returns the full graph: clients, nodes, devices, modules,
  ports, links, metadata. Other tools expose subsets.
- The output is JSON, which is much easier to parse than the
  human-readable format that pactl/wspctl use.
- pw-dump requires no daemon connection; it just talks to the
  daemon over the standard PipeWire socket.

Snapshot fields (one of each):
- core: dict with version, name, cookie, etc. (1 per daemon)
- clients: list of PipeWire:Interface:Client with id, name, props
- modules: list of PipeWire:Interface:Module with name, args, props
- nodes: list of PipeWire:Interface:Node with media class, rate, format
- devices: list of PipeWire:Interface:Device (sound cards, etc.)
- links: list of PipeWire:Interface:Link (input -> output port pairs)
- ports: list of PipeWire:Interface:Port (raw, mostly for debugging)

The sample_rate_audit() helper extracts just the rate info from
nodes, useful for "are all my audio devices using the same rate?"
diagnostics. Mismatched rates cause resampling which adds latency
and can introduce glitches.
"""

import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional


def pw_dump_available() -> bool:
    """True if pw-dump is installed (part of pipewire-tools)."""
    return shutil.which("pw-dump") is not None


class PwDumpError(Exception):
    """Raised when pw-dump can't be run or returns invalid JSON."""


def _run_pw_dump(timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Run `pw-dump` and return the parsed JSON list.

    Raises PwDumpError on missing tool, timeout, or invalid output.
    """
    if not pw_dump_available():
        raise PwDumpError("pw-dump not found on PATH. Install pipewire-tools.")
    try:
        proc = subprocess.run(
            ["pw-dump"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise PwDumpError(f"pw-dump timed out after {timeout}s") from e
    except OSError as e:
        raise PwDumpError(f"Failed to launch pw-dump: {e}") from e
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise PwDumpError(f"pw-dump failed (rc={proc.returncode}): {stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PwDumpError(f"pw-dump returned invalid JSON: {e}") from e


def _extract_rate(node_info: Dict[str, Any]) -> Optional[int]:
    """Extract the default sample rate (Hz) from a node's info.params.

    The EnumFormat param contains a list of supported formats; we pick
    the default rate from the first format's 'rate' field. The rate
    can be a plain int (fixed rate) or a dict like
    {"default": 48000, "min": 1, "max": 192000}.

    Returns None if no EnumFormat info is present (not an audio node).
    """
    params = node_info.get("params") or {}
    enum_format = params.get("EnumFormat")
    if not enum_format:
        return None
    # EnumFormat is a list of format dicts
    if not isinstance(enum_format, list) or not enum_format:
        return None
    first = enum_format[0]
    rate = first.get("rate")
    if rate is None:
        return None
    if isinstance(rate, (int, float)):
        return int(rate)
    if isinstance(rate, dict):
        default = rate.get("default")
        if default is not None:
            return int(default)
    return None


def get_graph_snapshot(timeout: float = 5.0) -> Dict[str, Any]:
    """Take a structured snapshot of the current PipeWire graph.

    Returns a dict with:
      - core: dict (or {} if no core object)
      - clients: list of dicts (id, name, app_name, binary, pid, props)
      - modules: list of dicts (id, name, args, props)
      - nodes: list of dicts (id, name, media_class, rate, app_name,
        state, props, params)
      - devices: list of dicts (id, name, media_class, props)
      - links: list of dicts (id, output_node, output_port,
        input_node, input_port, state, active)
      - ports: list of dicts (id, name, direction, node_id, channel)
      - raw_count: int — total objects in the raw dump (for diagnostics)

    Each entry is a slimmed-down view of the raw pw-dump data: we keep
    only the fields the UI displays, and we resolve the linked IDs
    (link.output_node from link.output_port) so the UI doesn't have
    to do a second pass.
    """
    data = _run_pw_dump(timeout=timeout)
    if not isinstance(data, list):
        raise PwDumpError(
            f"pw-dump returned unexpected type {type(data).__name__}"
        )

    # Build lookup tables
    nodes_by_id: Dict[int, Dict[str, Any]] = {}
    clients_by_id: Dict[int, Dict[str, Any]] = {}

    snapshot: Dict[str, Any] = {
        "core": {},
        "clients": [],
        "modules": [],
        "nodes": [],
        "devices": [],
        "links": [],
        "ports": [],
        "raw_count": len(data),
    }

    # First pass: collect everything in slim form
    for obj in data:
        otype = obj.get("type", "")
        oid = obj.get("id")
        info = obj.get("info", {})
        props = info.get("props", {})

        if otype == "PipeWire:Interface:Core":
            snapshot["core"] = {
                "name": info.get("name", "?"),
                "version": info.get("version", "?"),
                "cookie": info.get("cookie"),
                "user_name": info.get("user-name", "?"),
                "host_name": info.get("host-name", "?"),
                "clock_rate": props.get("default.clock.rate"),
                "clock_quantum": props.get("default.clock.quantum"),
            }

        elif otype == "PipeWire:Interface:Client":
            entry = {
                "id": oid,
                "name": info.get("name", "?"),
                "app_name": props.get("application.name", ""),
                "binary": props.get("application.process.binary", ""),
                "pid": _safe_int(props.get("application.process.id")),
                "props": props,
            }
            snapshot["clients"].append(entry)
            clients_by_id[oid] = entry

        elif otype == "PipeWire:Interface:Module":
            # Module args can be either an actual argument string (e.g.
            # "rt.prio = 88") or the module's documented usage (the
            # multiline "list of server Unix sockets..." text). We
            # normalize to a string either way, and flag whether it
            # looks like real args (short) or documentation (long).
            args_raw = info.get("args", "")
            if isinstance(args_raw, dict):
                # Rare — pw-dump sometimes returns args as a dict
                args_raw = "\n".join(f"{k} = {v}" for k, v in args_raw.items())
            elif args_raw is None:
                args_raw = ""
            entry = {
                "id": oid,
                "name": info.get("name", "?"),
                "args": args_raw,
                "args_is_documentation": (
                    len(str(args_raw)) > 100 or "\n" in str(args_raw)
                ),
                "filename": info.get("filename", ""),
                "props": props,
            }
            snapshot["modules"].append(entry)

        elif otype == "PipeWire:Interface:Node":
            entry = {
                "id": oid,
                "name": info.get("props", {}).get("node.name", "?"),
                "media_class": props.get("media.class", ""),
                "rate": _extract_rate(info),
                "app_name": props.get("application.name", ""),
                "binary": props.get("application.process.binary", ""),
                "state": info.get("state", ""),
                "client_id": _safe_int(props.get("client.id")),
                "device_id": _safe_int(props.get("device.id")),
                "props": props,
            }
            snapshot["nodes"].append(entry)
            nodes_by_id[oid] = entry

        elif otype == "PipeWire:Interface:Device":
            entry = {
                "id": oid,
                "name": info.get("props", {}).get("device.name", "?"),
                "media_class": props.get("media.class", ""),
                "description": props.get("device.description", ""),
                "nick": props.get("device.nick", ""),
                "props": props,
            }
            snapshot["devices"].append(entry)

        elif otype == "PipeWire:Interface:Link":
            # pw-dump reports links with fields named output-node-id,
            # output-port-id, input-node-id, input-port-id (not the
            # 'output-port' / 'input-port' names we initially assumed).
            entry = {
                "id": oid,
                "output_port": _safe_int(info.get("output-port-id")),
                "input_port": _safe_int(info.get("input-port-id")),
                "output_node": _safe_int(info.get("output-node-id")),
                "input_node": _safe_int(info.get("input-node-id")),
                "state": info.get("state", ""),
                "active": info.get("active", False),
                "props": info.get("props", {}),
            }
            snapshot["links"].append(entry)

        elif otype == "PipeWire:Interface:Port":
            # The node ID lives in props.node.id, not in info. PW's
            # port objects don't have a top-level info.node-id field.
            entry = {
                "id": oid,
                "name": info.get("props", {}).get("port.name", "?"),
                "direction": info.get("direction", ""),
                "node_id": _safe_int(props.get("node.id")),
                "channel": info.get("props", {}).get("audio.channel", ""),
                "props": info.get("props", {}),
            }
            snapshot["ports"].append(entry)

    return snapshot


def _safe_int(value: Any) -> Optional[int]:
    """Parse a value as int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def sample_rate_audit(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Analyse sample-rate consistency across audio nodes.

    Returns a dict:
      - rates_seen: dict {rate_hz: count} (e.g. {48000: 6, 44100: 2})
      - is_consistent: True if all audio nodes use the same rate
      - recommended_rate: most common rate, or None if no audio nodes
      - nodes_by_rate: dict {rate_hz: [node_names]} for display
      - mismatched_nodes: list of node names whose rate differs from
        the most common rate
      - audio_node_count: total number of nodes with a sample rate
    """
    nodes = snapshot.get("nodes", [])
    audio_nodes = [
        n for n in nodes
        if n.get("media_class", "").startswith("Audio/")
        and n.get("rate")
    ]
    if not audio_nodes:
        return {
            "rates_seen": {},
            "is_consistent": True,  # Vacuously true
            "recommended_rate": None,
            "nodes_by_rate": {},
            "mismatched_nodes": [],
            "audio_node_count": 0,
        }

    rates_seen: Dict[int, int] = {}
    nodes_by_rate: Dict[int, List[str]] = {}
    for n in audio_nodes:
        r = n["rate"]
        rates_seen[r] = rates_seen.get(r, 0) + 1
        nodes_by_rate.setdefault(r, []).append(
            f"{n['name']} ({n['media_class']})"
        )

    # Recommended rate = most common
    recommended = max(rates_seen.items(), key=lambda kv: kv[1])[0]
    is_consistent = len(rates_seen) == 1
    mismatched = []
    if not is_consistent:
        for n in audio_nodes:
            if n["rate"] != recommended:
                mismatched.append(
                    f"{n['name']} ({n['media_class']}): {n['rate']} Hz"
                )

    return {
        "rates_seen": rates_seen,
        "is_consistent": is_consistent,
        "recommended_rate": recommended,
        "nodes_by_rate": nodes_by_rate,
        "mismatched_nodes": mismatched,
        "audio_node_count": len(audio_nodes),
    }


def health_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a high-level health summary of the PipeWire graph.

    Returns a dict with:
      - counts: dict of object counts (clients, nodes, links, etc.)
      - warnings: list of human-readable warning strings
      - errors: list of human-readable error strings
      - status: one of "healthy", "warnings", "errors"
    """
    warnings: List[str] = []
    errors: List[str] = []

    nodes = snapshot.get("nodes", [])
    links = snapshot.get("links", [])

    # Check for failed links
    for link in links:
        if link.get("state") == "error":
            errors.append(
                f"Link #{link['id']} is in error state"
            )

    # Check for nodes in error state
    for node in nodes:
        if node.get("state") == "error":
            errors.append(
                f"Node '{node['name']}' is in error state"
            )

    # Check for suspended audio nodes (not necessarily wrong — could
    # be idle — but worth flagging if many)
    suspended_audio = [
        n for n in nodes
        if n.get("media_class", "").startswith("Audio/")
        and n.get("state") == "suspended"
    ]
    if len(suspended_audio) > len(nodes) / 2 and nodes:
        warnings.append(
            f"{len(suspended_audio)} of {len(nodes)} audio nodes are suspended "
            "(idle but available; not necessarily a problem)"
        )

    # Sample-rate consistency check
    audit = sample_rate_audit(snapshot)
    if audit["audio_node_count"] > 0 and not audit["is_consistent"]:
        warnings.append(
            f"Sample rate mismatch: nodes use "
            f"{', '.join(f'{r} Hz ({c} nodes)' for r, c in audit['rates_seen'].items())}. "
            f"Recommended: {audit['recommended_rate']} Hz. "
            "Mismatched rates cause resampling (added latency, potential glitches)."
        )

    # Determine status
    if errors:
        status = "errors"
    elif warnings:
        status = "warnings"
    else:
        status = "healthy"

    return {
        "counts": {
            "clients": len(snapshot.get("clients", [])),
            "modules": len(snapshot.get("modules", [])),
            "nodes": len(nodes),
            "devices": len(snapshot.get("devices", [])),
            "links": len(links),
            "ports": len(snapshot.get("ports", [])),
        },
        "warnings": warnings,
        "errors": errors,
        "status": status,
    }
