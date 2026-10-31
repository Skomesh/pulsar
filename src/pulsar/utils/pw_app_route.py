"""
Native per-app routing for PipeWire streams.

WHY THIS EXISTS
---------------
The Capture tab used to route an app by taking the sink it was playing
to and looping that SINK's monitor to the target. A monitor carries
every stream on that sink, so the route was a property of the device,
not of the app:

  * Routing app B to vsink2 removed app A's route to vsink1, because
    both apps shared the same monitor source and the reroute step
    deletes loopbacks with a matching source.
  * An app could never be heard on two outputs at once.
  * The app's own stream was never addressed.

PipeWire links PORTS, and a stream's output port belongs to that one
app. Linking ``paplay:output_FL -> vsink_b:playback_FL`` addresses
exactly that app, so two apps of the same binary route independently.

MEASURED on this host, native links are additive -- the app keeps its
session-manager link to its original sink and gains the new one. To
route EXCLUSIVELY, the session manager's own link is disconnected with
``pw-link -d``. That disconnect sticks: it survived a default-sink
change, a new sink appearing, and 5s of waiting, because the session
manager only re-links when a stream is (re)created.

Channel pairing is by NAME (FL->FL, FR->FR). Linking every out-port to
every in-port makes a 2x2 cross-link, which swaps channels.

``pw-link`` and ``pw-dump`` live in the SAME package (pipewire-bin on
Debian/Ubuntu, pipewire-utils on Fedora, pipewire on Arch), which this
app already hard-depends on for app discovery, so this adds no new
dependency.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Port-name suffix -> channel. Used to pair a stream's output ports
#: with a sink's input ports without crossing them.
CHANNEL_SUFFIXES = ("FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR")


@dataclass(frozen=True)
class StreamPort:
    """One port of one node."""

    id: int
    node_id: int
    name: str
    direction: str  # "in" or "out"

    @property
    def channel(self) -> str:
        """The channel this port carries, e.g. "FL".

        Returns "" when the name does not end in a known channel, so a
        caller can refuse to pair it rather than pair it wrongly.
        """
        for suffix in CHANNEL_SUFFIXES:
            if self.name.endswith(suffix):
                return suffix
        return ""


@dataclass
class RoutePlan:
    """Which links to create, which to break, and where the primary is.

    ``primary`` is the sink the session manager should own at the end.
    The order matters and is fixed by ``apply_plan``:

        1. break the links that are wrong
        2. MOVE the stream to the primary  (the session manager's job)
        3. create the native links to the additional targets

    The move has to sit in the middle. A native link to the primary
    would be duplicated by the move, so it is broken first; and a
    native link to the sink the session manager is vacating must be
    created AFTER the move, or it would double the audio there.
    """

    create: List[Tuple[int, int]] = field(default_factory=list)
    remove: List[Tuple[int, int]] = field(default_factory=list)
    primary: Optional[str] = None
    #: False when the app is already on the primary, so no move is
    #: needed. Moving a stream to the sink it is already on is a no-op
    #: that can still fail for unrelated reasons, so it is skipped.
    needs_move: bool = True

    @property
    def is_empty(self) -> bool:
        return not self.create and not self.remove


def _run_pw_dump(logger=None) -> str:
    """Run pw-dump and return its stdout."""
    try:
        out = subprocess.run(
            ["pw-dump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise PwLinkUnavailable(f"pw-dump failed: {e}") from e
    return out


def _run_pw_link(args: Sequence[str], logger=None) -> subprocess.CompletedProcess:
    """Run pw-link with the given arguments."""
    try:
        return subprocess.run(
            ["pw-link", *args],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise PwLinkUnavailable(f"pw-link failed: {e}") from e


def parse_ports(pw_dump_output: str) -> Dict[int, StreamPort]:
    """Parse pw-dump output into {port_id: StreamPort}."""
    try:
        data = json.loads(pw_dump_output)
    except json.JSONDecodeError:
        return {}
    ports: Dict[int, StreamPort] = {}
    for obj in data:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "PipeWire:Interface:Port":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        port_id = obj.get("id")
        node_id = props.get("node.id")
        if not isinstance(port_id, int) or not isinstance(node_id, int):
            continue
        ports[port_id] = StreamPort(
            id=port_id,
            node_id=node_id,
            name=props.get("port.name", ""),
            direction=props.get("port.direction", ""),
        )
    return ports


def parse_node_names(pw_dump_output: str) -> Dict[int, str]:
    """Parse pw-dump output into {node_id: node.name}."""
    try:
        data = json.loads(pw_dump_output)
    except json.JSONDecodeError:
        return {}
    names: Dict[int, str] = {}
    for obj in data:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        node_id = obj.get("id")
        if isinstance(node_id, int):
            names[node_id] = props.get("node.name", "")
    return names


def parse_links(pw_link_output: str) -> Set[Tuple[int, int]]:
    """Parse `pw-link -l` output into {(out_port_id, in_port_id)}.

    The listing is grouped by port: a header line naming the port, then
    its links, each as ``<link-id>  |->  <other-port-id> <other-name>``
    or ``|<-`` for the other direction.

    Both ends of every link appear, so the same edge is seen twice. A
    link is one edge, so only the direction that goes from an output
    port to an input port is kept -- which is what the caller needs to
    tell "this stream reaches that sink".
    """
    links: Set[Tuple[int, int]] = set()
    current: Optional[int] = None
    for raw in pw_link_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|" not in line:
            # A port header: "94 vsink_a:playback_FL"
            parts = line.split(None, 1)
            if parts and parts[0].isdigit():
                current = int(parts[0])
            continue
        # A link line. The FIRST number is the link's own id, not a
        # port id; the port id is the one after the arrow.
        parts = line.split()
        arrow_at = None
        for i, token in enumerate(parts):
            if token in ("|->", "|<-"):
                arrow_at = i
                break
        if arrow_at is None or current is None:
            continue
        arrow = parts[arrow_at]
        rest = [p for p in parts[arrow_at + 1:] if p.isdigit()]
        if not rest:
            continue
        other = int(rest[0])
        if arrow == "|->":
            links.add((current, other))
        else:
            links.add((other, current))
    return links


def channels_for(
    ports: Dict[int, StreamPort],
    out_ports: Iterable[int],
    in_ports: Iterable[int],
) -> List[Tuple[int, int]]:
    """Pair output ports to input ports BY CHANNEL.

    A cartesian product would link FL->FR as well as FL->FL, which
    swaps the channels. The rules:

    * A port whose name carries no known channel is never paired. A
      wrong pairing is worse than no route: it plays the app's audio in
      the wrong place.
    * An exact channel match always pairs (FL->FL, FR->FR).
    * A mismatched channel pairs ONLY when the source is MONO -- one
      output port. A mono stream is upmixed by the sink, so its single
      port feeds every channel of the target.
    """
    outs = [ports[p] for p in out_ports if p in ports]
    ins = [ports[p] for p in in_ports if p in ports]
    if not outs or not ins:
        return []

    mono_source = len(outs) == 1

    pairs: List[Tuple[int, int]] = []
    for out in outs:
        out_ch = out.channel
        if not out_ch:
            continue
        for inp in ins:
            in_ch = inp.channel
            if not in_ch:
                continue
            if out_ch == in_ch or mono_source:
                pairs.append((out.id, inp.id))
    return pairs


def link_plan(
    ports: Dict[int, StreamPort],
    node_names: Dict[int, str],
    stream_node_id: int,
    target_sinks: Sequence[str],
    existing_links: Set[Tuple[int, int]],
) -> RoutePlan:
    """Work out which links to create and which to break.

    A link to a sink that is NOT a target is broken, which is what makes
    routing exclusive -- the app stops being heard on the sink the
    session manager put it on.

    Links belonging to OTHER streams are never touched: only links whose
    output port belongs to ``stream_node_id`` are considered.
    """
    plan = RoutePlan()

    stream_out_ports = [
        p.id for p in ports.values()
        if p.node_id == stream_node_id and p.direction == "out"
    ]

    # Nodes that receive audio: their input ports are the link targets.
    sink_node_ids = {
        node_id for node_id, name in node_names.items()
        if name in target_sinks
    }
    target_in_ports = [
        p.id for p in ports.values()
        if p.node_id in sink_node_ids and p.direction == "in"
    ]

    for out_id, in_id in channels_for(ports, stream_out_ports, target_in_ports):
        if (out_id, in_id) not in existing_links:
            plan.create.append((out_id, in_id))

    # Break this stream's links to sinks that are not targets.
    target_port_ids = {
        p.id for p in ports.values()
        if p.node_id in sink_node_ids and p.direction == "in"
    }
    for out_id, in_id in sorted(existing_links):
        if out_id not in stream_out_ports:
            continue  # belongs to a different app -- leave it alone
        if in_id in target_port_ids:
            continue  # already going where we want it
        plan.remove.append((out_id, in_id))

    return plan


def plan_route(
    ports: Dict[int, StreamPort],
    node_names: Dict[int, str],
    stream_node_id: int,
    target_sinks: Sequence[str],
    existing_links: Set[Tuple[int, int]],
    session_manager_target: Optional[str] = None,
) -> RoutePlan:
    """Plan the links for routing one app to one or more sinks.

    WHO OWNS WHICH LINK
    -------------------
    The session manager owns the link to the PRIMARY target, reached by
    `pactl move-sink-input`. The PulseAudio compat layer then reports
    the app's real sink, so other tools (and this app's own Manage tab)
    see the truth.

    Native links own the ADDITIONAL targets, because the compat layer
    cannot express "one stream, two sinks" at all.

    WHICH TARGET IS PRIMARY
    -----------------------
    The one the session manager is ALREADY on, if it is among the
    targets. Otherwise the first target.

    That rule matters. The session manager drops its own link when the
    stream is moved, so the sink it vacates ends up with NO link at
    all -- and if that sink is also a target, a native link has to be
    created for it afterwards. Choosing the session manager's current
    sink as primary avoids the whole situation: it is never one of the
    additional targets, so it is never left linkless.

    THE ORDER OF OPERATIONS
    -----------------------
    1. break the links that are wrong
    2. move the stream to the primary   (the session manager's job)
    3. create the native links to the additional targets

    A native link to the primary would be duplicated by the move, so it
    is broken first -- but the session manager's OWN link to the sink it
    holds must be left for the move to replace. Cutting it by hand
    leaves the session manager believing the stream is still there, and
    a later move to that same sink becomes a no-op, so the route is
    silently lost.

    Links belonging to OTHER streams are never touched: only links
    whose output port belongs to ``stream_node_id`` are considered.
    That is what stops routing one app from destroying another app's
    route -- the defect this replaces.
    """
    plan = RoutePlan()

    stream_out = _stream_out_ports(ports, stream_node_id)

    if not target_sinks:
        # Nothing selected: the app goes back to the default sink, which
        # is the session manager's business. We only drop native links.
        for out_id, in_id in sorted(existing_links):
            if out_id not in stream_out:
                continue
            sink = _sink_of_port(ports, node_names, in_id)
            if sink is None or sink == session_manager_target:
                continue
            plan.remove.append((out_id, in_id))
        return plan

    sm_sink = session_manager_target

    if sm_sink and sm_sink in target_sinks:
        primary = sm_sink
    else:
        primary = target_sinks[0]
    extra = [s for s in target_sinks if s != primary]

    # Break the links that are wrong.
    for out_id, in_id in sorted(existing_links):
        if out_id not in stream_out:
            continue  # another app's link -- leave it alone
        sink = _sink_of_port(ports, node_names, in_id)
        if sink is None:
            continue
        if sink == sm_sink:
            # The session manager's own link. Leave it: the move
            # replaces it, and cutting it by hand makes a later move to
            # the same sink a no-op, silently losing the route.
            continue
        if sink in target_sinks:
            # A native link to the primary. The move would duplicate
            # it, so it must go first.
            if sink == primary:
                plan.remove.append((out_id, in_id))
            continue
        plan.remove.append((out_id, in_id))

    # Create the native links for the additional targets.
    already_linked = set(existing_links)
    sink_node_ids = {
        node_id for node_id, name in node_names.items()
        if name in extra
    }
    target_in_ports = [
        p.id for p in ports.values()
        if p.node_id in sink_node_ids and p.direction == "in"
    ]
    for out_id, in_id in channels_for(ports, stream_out, target_in_ports):
        if (out_id, in_id) in already_linked:
            continue
        plan.create.append((out_id, in_id))

    plan.primary = primary
    plan.needs_move = primary != sm_sink
    return plan


def _stream_out_ports(
    ports: Dict[int, StreamPort], stream_node_id: int
) -> Set[int]:
    """The output port ids of one stream node."""
    return {
        p.id for p in ports.values()
        if p.node_id == stream_node_id and p.direction == "out"
    }


def _sink_of_port(
    ports: Dict[int, StreamPort],
    node_names: Dict[int, str],
    port_id: int,
) -> Optional[str]:
    """The sink NAME a port belongs to, or None if it is not a sink."""
    port = ports.get(port_id)
    if port is None or port.direction != "in":
        return None
    name = node_names.get(port.node_id)
    return name or None


class PwLinkUnavailable(RuntimeError):
    """The PipeWire link tools are not available on this host."""


def pw_link_available() -> bool:
    """True if pw-link and pw-dump are both installed."""
    return shutil.which("pw-link") is not None and shutil.which("pw-dump") is not None


def apply_plan(plan: RoutePlan, logger=None, move=None) -> Tuple[int, int]:
    """Apply a plan. Returns (created, removed) counts.

    ``move`` is called with the primary sink name BETWEEN the removals
    and the creations. It is the caller's job to actually move the
    stream -- that is a `pactl move-sink-input`, which the session
    manager owns, and it has to happen at that exact point:

      * after the removals, so a stale native link to the primary is
        gone before the session manager adds its own;
      * before the creations, so a native link to the sink the session
        manager is vacating is not doubled with the link it is dropping.

    If ``move`` is None, only the link changes are made.
    """
    created = 0
    removed = 0
    for out_id, in_id in plan.remove:
        result = _run_pw_link(["-d", str(out_id), str(in_id)], logger)
        if result.returncode == 0:
            removed += 1
        elif logger:
            logger(f"pw-link -d {out_id} {in_id} failed: {result.stderr.strip()}")
    if move is not None and plan.primary and plan.needs_move:
        move(plan.primary)
    for out_id, in_id in plan.create:
        result = _run_pw_link([str(out_id), str(in_id)], logger)
        if result.returncode == 0:
            created += 1
        elif logger:
            logger(f"pw-link {out_id} {in_id} failed: {result.stderr.strip()}")
    return created, removed
