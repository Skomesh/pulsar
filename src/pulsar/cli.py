"""
Command-line interface for Pulsar.

    pulsar [--json] <group> <command> [args]

The GUI (`pulsar-gui`, i.e. `pulsar.__main__`) drives the same
`PactlRunner`, `ProfileManager` and `PresetManager` this module does. A CLI
is only worth having if it is a peer of the GUI rather than a toy wrapped
around it, so every command here calls into those managers directly -- there
is no second implementation of any routing logic, which means the
feedback-loop guard and the XDG data paths apply identically in both.

Everything the CLI touches goes through module-level `PactlRunner`,
`ProfileManager` and `PresetManager` names so tests can patch the boundary.
Importing them as names at module scope, rather than reaching for them
through a class, is what makes `mock.patch.object(cli, "PactlRunner")`
work.

`--json` exists because the interesting use of a CLI here is scripting:
picking a sink name out of `pulsar list sinks --json` and feeding it to
`pulsar route`. Parsing a human table is a trap.
"""

import argparse
import json
import shutil
import sys
from typing import Any, Dict, List, Optional

from pulsar.utils.pactl_runner import LoopbackCycleError, PactlRunner

# Imported at module scope, not inside the handlers, so tests can patch
# `cli.ProfileManager`. Ruff strips these as unused until the first handler
# that needs them exists, which is why they have come and gone once.
from pulsar.utils.preset_manager import PresetManager
from pulsar.utils.profile_manager import ProfileManager

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def emit(payload: Dict[str, Any], text: str, as_json: bool) -> None:
    """Print either the JSON payload or the human text, never both."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(text)


def table(rows: List[List[str]], headers: List[str]) -> str:
    """Left-aligned fixed-width table. Widths come from the data itself."""
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(out)


#: Alias so tests can wrap `table` and still produce real output.
render_table = table


# --------------------------------------------------------------------------
# Picking a sink without typing its exact name
# --------------------------------------------------------------------------

def stdin_is_a_tty() -> bool:
    """True when there is a human to ask.

    Checked rather than assumed, because a CLI that prompts inside a
    pipeline hangs forever. `pulsar route game hdmi | tee log` has no
    terminal on stdin and must fail loudly instead of waiting.
    """
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def choose_interactively(items: List[Dict[str, Any]], prompt: str) -> Optional[Dict[str, Any]]:
    """Show a numbered list and return the chosen item, or None.

    Returns None rather than raising on EOF: Ctrl-D at the prompt means
    "never mind", and an unhandled EOFError from a terminal is a crash the
    user did nothing to deserve.
    """
    if not items:
        return None

    print(prompt)
    print(render_table(
        [[str(i + 1), it.get("name", "?"), it.get("description", ""),
          it.get("state", "")] for i, it in enumerate(items)],
        ["#", "NAME", "DESCRIPTION", "STATE"],
    ))

    while True:
        try:
            answer = input("Number (or blank to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not answer:
            return None
        try:
            index = int(answer)
        except ValueError:
            print(f"Not a number: {answer!r}")
            continue
        if 1 <= index <= len(items):
            return items[index - 1]
        print(f"Out of range -- pick 1 to {len(items)}.")


def resolve_sink(name: str, interactive: Optional[bool] = None,
                 sinks: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Resolve what the user typed to a real sink name, or None.

    Escalating strategies, cheapest first:

      1. exact name
      2. case-insensitive exact
      3. unique substring

    Only when more than one sink survives does it ask, and only if there is
    a terminal. Guessing between two candidates would be worse than asking:
    `pulsar route sink hdmi` picking `bluez_sink` because it came first
    would silently send audio to the wrong device.

    Returns None for "no match" and for "ambiguous and non-interactive" --
    the caller reports both, and the distinction only matters for the
    message, not the exit code.
    """
    if sinks is None:
        sinks = PactlRunner.list_sinks()

    names = [s.get("name", "") for s in sinks]

    if name in names:
        return name

    lowered = name.lower()
    for candidate in names:
        if candidate.lower() == lowered:
            return candidate

    matches = [s for s in sinks if lowered in s.get("name", "").lower()]
    if len(matches) == 1:
        return matches[0]["name"]
    if not matches:
        return None

    if interactive is None:
        interactive = stdin_is_a_tty()
    if not interactive:
        return None

    picked = choose_interactively(matches, f"'{name}' matches several sinks:")
    return picked["name"] if picked else None


def format_ambiguous(name: str, matches: List[Dict[str, Any]]) -> str:
    """The message for an ambiguous name that cannot be resolved."""
    lines = [f"'{name}' matches {len(matches)} sinks:"]
    for i, m in enumerate(matches, 1):
        lines.append(f"  {i}. {m.get('name', '?')}  {m.get('description', '')}")
    lines.append("Use the full name, or run from a terminal to pick from a list.")
    return "\n".join(lines)


def mute_word(muted: Optional[bool]) -> str:
    return "yes" if muted else "no" if muted is not None else "-"


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------

def volume_percent(base_volume: Optional[str]) -> str:
    """'65536 / 80% / -5.83 dB' -> '80%'.

    pactl reports volume as raw, percent and dB side by side. A table wants
    the percent; `PactlRunner._parse_volume_percent` already knows how to
    pull it out, so this does not re-implement the parsing.
    """
    if not base_volume:
        return "-"
    pct = PactlRunner._parse_volume_percent(base_volume)
    return f"{pct}%" if pct is not None else "-"


def cmd_list_sinks(args) -> int:
    sinks = PactlRunner.list_sinks()
    # Keys are PactlRunner's, not guesses: `id` not `index`, `description`
    # not `desc`, `mute` not `muted`, `base_volume` not `volume`.
    rows = [
        [s.get("id", "?"), s.get("name", "?"), s.get("description", ""),
         s.get("state", "?"), volume_percent(s.get("base_volume")),
         s.get("mute", "-")]
        for s in sinks
    ]
    text = table(rows, ["IDX", "NAME", "DESCRIPTION", "STATE", "VOL%", "MUTE"])
    emit(
        {"count": len(sinks), "sinks": sinks},
        text if sinks else "No sinks found.",
        args.json,
    )
    return EXIT_OK


def cmd_list_sources(args) -> int:
    sources = PactlRunner.list_sources()
    # `monitor_of_sink` is what separates a capture device from the monitor
    # of an output. They route differently and confusing the two is a
    # common mistake, so the column is worth showing rather than hiding.
    rows = [
        [s.get("id", "?"), s.get("name", "?"), s.get("description", ""),
         s.get("state", "?"), volume_percent(s.get("base_volume")),
         s.get("mute", "-"), s.get("monitor_of_sink", "") or "-"]
        for s in sources
    ]
    text = table(rows, ["IDX", "NAME", "DESCRIPTION", "STATE", "VOL%", "MUTE",
                        "MONITOR OF"])
    emit(
        {"count": len(sources), "sources": sources},
        text if sources else "No sources found.",
        args.json,
    )
    return EXIT_OK


def cmd_list_cards(args) -> int:
    cards = PactlRunner.list_cards()
    rows = [
        [c.get("name", "?"), c.get("description", ""),
         str(c.get("sinks", 0)), str(c.get("sources", 0)),
         str(len(c.get("available") or []))]
        for c in cards
    ]
    text = table(rows, ["NAME", "DESCRIPTION", "SINKS", "SOURCES", "PROFILES"])
    emit(
        {"count": len(cards), "cards": cards},
        text if cards else "No cards found.",
        args.json,
    )
    return EXIT_OK


def cmd_list_streams(args) -> int:
    streams = PactlRunner.list_sink_inputs()
    rows = [
        [s.get("index", "?"), s.get("application", "") or "-",
         s.get("node_name", "") or "-", s.get("media", "") or "-",
         s.get("sink", "") or "-"]
        for s in streams
    ]
    text = table(rows, ["INDEX", "APPLICATION", "NODE", "MEDIA", "SINK"])
    emit(
        {"count": len(streams), "streams": streams},
        text if streams else "No streams found.",
        args.json,
    )
    return EXIT_OK


def cmd_list_routes(args) -> int:
    routes = PactlRunner.list_loopbacks()
    rows = [[r.get("id", "?"), r.get("source", "?"), r.get("sink", "?")]
            for r in routes]
    text = table(rows, ["MODULE", "SOURCE", "SINK"])
    emit(
        {"count": len(routes), "routes": routes},
        text if routes else "No routes found.",
        args.json,
    )
    return EXIT_OK


def parse_percent(raw: str) -> Optional[int]:
    """'50' -> 50, '50%' -> 50. None if it is not a sane percentage.

    A value outside 0-100 is a usage error rather than something to clamp:
    `pulsar volume game 500` is a typo, and quietly turning it into 100%
    hides it.
    """
    text = raw.strip().rstrip("%")
    try:
        value = int(text)
    except ValueError:
        return None
    if not 0 <= value <= 100:
        return None
    return value


def cmd_create(args) -> int:
    """Create a virtual device, optionally routing it in the same breath.

    A refused --route-to leaves the device in place and reports failure.
    Deleting it again would leave the user with neither the device nor the
    route they asked for, and an error implying the device never existed.
    """
    kind, name = args.kind, args.name
    description = args.description or name

    existing = PactlRunner.list_sinks() + PactlRunner.list_sources()
    names = {s.get("name", "") for s in existing}
    if name in names:
        print(f"Error: '{name}' already exists. "
              f"Run `pulsar list sinks` to see current devices.",
              file=sys.stderr)
        return EXIT_ERROR

    makers = {
        "sink": PactlRunner.create_sink_only,
        "source": PactlRunner.create_source_only,
        "both": PactlRunner.create_duplex_sink,
    }
    created = makers[kind](name, description, channels=args.channels)
    if not created:
        print(f"Error: could not create the {kind} '{name}'.", file=sys.stderr)
        return EXIT_ERROR

    if not args.route_to:
        emit({"created": True, "kind": kind, "name": name},
             f"Created {kind} '{name}'.", args.json)
        return EXIT_OK

    target = resolve_or_report(args.route_to, "route target", args)
    if not target:
        print(f"Created {kind} '{name}', but the route was not made.", file=sys.stderr)
        return EXIT_ERROR

    monitor = PactlRunner.monitor_source_for(name)
    if not monitor:
        print(f"Created {kind} '{name}', but it has no monitor to route.",
              file=sys.stderr)
        return EXIT_ERROR

    try:
        module_id = PactlRunner.create_loopback(monitor, target)
    except LoopbackCycleError as exc:
        print(f"Created {kind} '{name}', but routing it to '{target}' would "
              f"create a feedback loop: {exc.render()}", file=sys.stderr)
        print(f"'{name}' is there; add the route once the other one is gone.",
              file=sys.stderr)
        emit({"created": True, "routed": False, "error": "feedback_loop",
              "chain": exc.chain, "name": name, "target": target},
             "", args.json)
        return EXIT_ERROR

    if not module_id:
        print(f"Created {kind} '{name}', but the route failed.", file=sys.stderr)
        return EXIT_ERROR

    # One line, creation first. Emitting as it goes put the route
    # confirmation ahead of the device it refers to, because the route
    # summary went to stderr and the creation line to stdout.
    emit({"created": True, "routed": True, "module": module_id,
          "kind": kind, "name": name, "target": target},
         f"Created {kind} '{name}' and routed it to {target}.", args.json)
    return EXIT_OK


def cmd_volume(args) -> int:
    percent = parse_percent(args.percent)
    if percent is None:
        print(f"Error: '{args.percent}' is not a percentage between 0 and 100.",
              file=sys.stderr)
        return EXIT_USAGE

    sink = resolve_or_report(args.sink, "sink", args)
    if not sink:
        return EXIT_ERROR

    if not PactlRunner.set_sink_volume(sink, percent):
        print(f"Error: could not set {sink} to {percent}%.", file=sys.stderr)
        return EXIT_ERROR

    emit({"sink": sink, "volume": percent},
         f"{sink} volume set to {percent}%", args.json)
    return EXIT_OK


def cmd_default(args) -> int:
    sink = resolve_or_report(args.name, "sink", args)
    if not sink:
        return EXIT_ERROR

    setter = (PactlRunner.set_default_sink if args.kind == "sink"
              else PactlRunner.set_default_source)
    if not setter(sink):
        print(f"Error: could not make '{sink}' the default {args.kind}.",
              file=sys.stderr)
        return EXIT_ERROR

    emit({"default": sink, "kind": args.kind},
         f"Default {args.kind} is now {sink}", args.json)
    return EXIT_OK


def cmd_mute(args) -> int:
    sink = resolve_or_report(args.sink, "sink", args)
    if not sink:
        return EXIT_ERROR

    wanted = args.state.lower()
    muted = wanted in ("on", "yes", "true", "1")

    if not PactlRunner.set_sink_mute(sink, muted):
        print(f"Error: could not {'mute' if muted else 'unmute'} {sink}.",
              file=sys.stderr)
        return EXIT_ERROR

    emit({"sink": sink, "muted": muted},
         f"{sink} {'muted' if muted else 'unmuted'}", args.json)
    return EXIT_OK


def resolve_profile(name: str) -> Optional[Dict[str, Any]]:
    """Look a profile up by name, case-insensitively, or return None.

    Profile names are long and human ("Apps + Voice Split"), so
    `profile apply apps` should find the one called "Apps + Voice
    Split" rather than insist on the full string.
    """
    manager = ProfileManager()
    if name in manager.get_profile_names():
        return manager.get_profile(name)

    lowered = name.lower()
    matches = [n for n in manager.get_profile_names() if n.lower() == lowered]
    if not matches:
        matches = [n for n in manager.get_profile_names()
                   if lowered in n.lower()]
    if len(matches) == 1:
        return manager.get_profile(matches[0])
    return None


def cmd_profile_list(args) -> int:
    names = ProfileManager().get_profile_names()
    profiles = [{"name": n} for n in names]
    text = table([[n] for n in names], ["NAME"])
    emit({"count": len(names), "profiles": profiles},
         text if names else "No profiles found.", args.json)
    return EXIT_OK


def cmd_profile_show(args) -> int:
    profile = resolve_profile(args.name)
    if profile is None:
        print(f"Error: no profile matching '{args.name}'.", file=sys.stderr)
        print("Run `pulsar profile list`.", file=sys.stderr)
        return EXIT_ERROR

    lines = [profile.get("description") or "(no description)", ""]
    devices = profile.get("devices") or []
    lines.append("Devices:")
    lines.append(table(
        [[d.get("name", "?"), d.get("type", "?"), str(d.get("channels", "-")),
          d.get("description", "")] for d in devices],
        ["NAME", "TYPE", "CH", "DESCRIPTION"],
    ) or "  (none)")

    # `routing`, not `routes` -- the key is the one ProfileManager writes.
    routes = profile.get("routing") or []
    lines.append("")
    lines.append("Routing:")
    lines.append(table(
        [[r.get("from", "?"), r.get("to", "?"), str(r.get("latency_msec", "-"))]
         for r in routes],
        ["FROM", "TO", "LATENCY MS"],
    ) or "  (none)")

    emit({"profile": profile}, "\n".join(lines), args.json)
    return EXIT_OK


def cmd_profile_apply(args) -> int:
    """Apply a profile to the live audio server.

    Goes through ProfileManager.apply_profile, which creates the devices,
    builds the loopbacks through create_loopback -- so the feedback-loop
    guard applies here too -- and rolls back on failure. Shelling out to
    pactl directly would bypass all of it.
    """
    profile = resolve_profile(args.name)
    if profile is None:
        print(f"Error: no profile matching '{args.name}'.", file=sys.stderr)
        print("Run `pulsar profile list`.", file=sys.stderr)
        return EXIT_ERROR

    result = ProfileManager().apply_profile(
        profile, logger=(print if not args.json else None))

    errors = result.get("errors") or []
    created = result.get("created") or []

    for message in errors:
        print(f"Error: {message}", file=sys.stderr)

    if errors:
        emit({"applied": False, "errors": errors, "created": created},
             "", args.json)
        return EXIT_ERROR

    text = f"Applied profile {args.name}."
    if created:
        text += "\n" + table([[c] for c in created], ["CREATED"])
    emit({"applied": True, "errors": [], "created": created}, text, args.json)
    return EXIT_OK


def cmd_profile_delete(args) -> int:
    if not ProfileManager().delete_profile(args.name):
        print(f"Error: could not delete profile '{args.name}'.", file=sys.stderr)
        return EXIT_ERROR
    emit({"deleted": args.name}, f"Deleted profile {args.name}.", args.json)
    return EXIT_OK


def cmd_preset_list(args) -> int:
    manager = PresetManager()
    names = manager.get_preset_names()
    presets = [{"name": n, "builtin": manager.is_builtin_preset(n)}
               for n in names]
    text = table([[n, "yes" if p["builtin"] else "no"] for n, p in
                  ((x["name"], x) for x in presets)], ["NAME", "BUILTIN"])
    emit({"count": len(names), "presets": presets},
         text if names else "No presets found.", args.json)
    return EXIT_OK


def cmd_preset_show(args) -> int:
    manager = PresetManager()
    preset = manager.get_preset(args.name)
    if preset is None:
        print(f"Error: no preset named '{args.name}'.", file=sys.stderr)
        return EXIT_ERROR

    builtin = manager.is_builtin_preset(args.name)
    lines = [f"{args.name}{' (builtin)' if builtin else ''}"]
    lines.append(table([[k, str(v)] for k, v in sorted(preset.items())],
                       ["KEY", "VALUE"]))
    emit({"preset": preset, "builtin": builtin}, "\n".join(lines), args.json)
    return EXIT_OK


def cmd_preset_delete(args) -> int:
    manager = PresetManager()
    # A builtin ships with the package; deleting it edits the install, and
    # on a read-only /usr that fails in a way the user cannot act on.
    if manager.is_builtin_preset(args.name):
        print(f"Error: '{args.name}' is a builtin preset and cannot be "
              f"deleted.", file=sys.stderr)
        return EXIT_ERROR
    if not manager.delete_preset(args.name):
        print(f"Error: could not delete preset '{args.name}'.", file=sys.stderr)
        return EXIT_ERROR
    emit({"deleted": args.name}, f"Deleted preset {args.name}.", args.json)
    return EXIT_OK


def _setup_named(p: argparse.ArgumentParser) -> None:
    p.add_argument("name")


def _setup_profile(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", help="profile name, or any unambiguous part of it")


def _setup_preset(p: argparse.ArgumentParser) -> None:
    p.add_argument("name")


def resolve_or_report(name: str, role: str, args) -> Optional[str]:
    """Resolve a user-supplied sink name, or print why it failed.

    Prints the ambiguous list when that is the problem, because "no such
    sink" for a name that matches three real ones is useless advice.
    """
    sinks = PactlRunner.list_sinks()
    resolved = resolve_sink(name, sinks=sinks)

    if resolved:
        return resolved

    lowered = name.lower()
    matches = [s for s in sinks if lowered in s.get("name", "").lower()]
    if matches:
        print(format_ambiguous(name, matches), file=sys.stderr)
    else:
        print(f"Error: no sink matching '{name}' (the {role}).", file=sys.stderr)
        print("Run `pulsar list sinks` to see what is available.",
              file=sys.stderr)
    return None


def cmd_route(args) -> int:
    """Route a sink's monitor into a target sink.

    Deliberately goes through `create_loopback`, not `pactl load-module`.
    That is what makes the feedback-loop guard apply here exactly as it
    does in the GUI -- a second code path that shelled out to pactl
    directly would be a hole straight past the thing that stops a device
    screaming at full volume.
    """
    sink = resolve_or_report(args.sink, "source", args)
    if not sink:
        return EXIT_ERROR
    target = resolve_or_report(args.target, "target", args)
    if not target:
        return EXIT_ERROR

    monitor = PactlRunner.monitor_source_for(sink)
    if not monitor:
        print(f"Error: no monitor source for sink '{sink}'.", file=sys.stderr)
        return EXIT_ERROR

    try:
        module_id = PactlRunner.create_loopback(monitor, target)
    except LoopbackCycleError as exc:
        # Printed to stderr even in --json mode: this is a diagnostic for a
        # human who just ran a command and got nothing. The JSON payload
        # carries the same information for scripts.
        print(
            f"Error: routing '{sink}' to '{target}' would create a feedback "
            f"loop: {exc.render()}",
            file=sys.stderr,
        )
        emit(
            {"error": "feedback_loop", "chain": exc.chain,
             "source": sink, "target": target},
            "", args.json,
        )
        return EXIT_ERROR

    if not module_id:
        print(f"Error: could not route '{sink}' to '{target}'.", file=sys.stderr)
        return EXIT_ERROR

    emit(
        {"routed": True, "module": module_id, "source": monitor, "target": target},
        f"Routed {sink} -> {target} (module {module_id})",
        args.json,
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# Command table
# --------------------------------------------------------------------------

def _setup_route(p: argparse.ArgumentParser) -> None:
    p.add_argument("sink", help="sink whose monitor feeds the route")
    p.add_argument("target", help="sink the monitor is played into")


def _setup_create(p: argparse.ArgumentParser) -> None:
    p.add_argument("kind", choices=["sink", "source", "both"],
                   help="what kind of virtual device to create")
    p.add_argument("name", help="device name, e.g. game or discord_mic")
    p.add_argument("-d", "--description", help="human description (defaults to name)")
    p.add_argument("-c", "--channels", type=int, default=2,
                   help="channel count (default: 2)")
    p.add_argument("--route-to",
                   help="also route the new device to this sink")


def _setup_volume(p: argparse.ArgumentParser) -> None:
    p.add_argument("sink")
    p.add_argument("percent", help="0-100, optionally with a %% sign")


def _setup_default(p: argparse.ArgumentParser) -> None:
    p.add_argument("kind", choices=["sink", "source"])
    p.add_argument("name")


def _setup_mute(p: argparse.ArgumentParser) -> None:
    p.add_argument("sink")
    p.add_argument("state", choices=["on", "off"], help="on or off")


#: Flat commands: invoked directly as `pulsar <name> ...`, not through a
#: subcommand group. `pulsar route game speakers` is the natural thing to
#: type; burying it under `pulsar route create game speakers` would be
#: ceremony for its own sake.
FLAT: Dict[str, Any] = {
    "route": {
        "handler": cmd_route,
        "help": "Route a sink's monitor into a target sink",
        "setup": _setup_route,
    },
    "create": {
        "handler": cmd_create,
        "help": "Create a virtual sink, source, or both",
        "setup": _setup_create,
    },
    "volume": {
        "handler": cmd_volume,
        "help": "Set a sink's volume",
        "setup": _setup_volume,
    },
    "default": {
        "handler": cmd_default,
        "help": "Set the default sink or source",
        "setup": _setup_default,
    },
    "mute": {
        "handler": cmd_mute,
        "help": "Mute or unmute a sink",
        "setup": _setup_mute,
    },
}

#: Two-level groups: `pulsar <group> <command> [args]`.
GROUPS: Dict[str, Dict[str, Any]] = {
    "list": {
        "sinks": {
            "handler": cmd_list_sinks,
            "help": "List audio sinks",
        },
        "routes": {
            "handler": cmd_list_routes,
            "help": "List loopback routes between sinks",
        },
        "sources": {
            "handler": cmd_list_sources,
            "help": "List audio sources",
        },
        "cards": {
            "handler": cmd_list_cards,
            "help": "List sound cards and their available profiles",
        },
        "streams": {
            "handler": cmd_list_streams,
            "help": "List playing application streams",
        },
    },
    "profile": {
        "list": {
            "handler": cmd_profile_list,
            "help": "List available profiles",
        },
        "show": {
            "handler": cmd_profile_show,
            "help": "Show a profile's devices and routing",
            "setup": _setup_profile,
        },
        "apply": {
            "handler": cmd_profile_apply,
            "help": "Apply a profile to the live audio server",
            "setup": _setup_profile,
        },
        "delete": {
            "handler": cmd_profile_delete,
            "help": "Delete a saved profile",
            "setup": _setup_named,
        },
    },
    "preset": {
        "list": {
            "handler": cmd_preset_list,
            "help": "List available presets",
        },
        "show": {
            "handler": cmd_preset_show,
            "help": "Show a preset's settings",
            "setup": _setup_preset,
        },
        "delete": {
            "handler": cmd_preset_delete,
            "help": "Delete a user preset (builtins are refused)",
            "setup": _setup_preset,
        },
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulsar",
        description="Control PulseAudio/PipeWire routing from the command line.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of a table",
    )
    sub = parser.add_subparsers(dest="group", metavar="<command>")

    for name, spec in FLAT.items():
        p = sub.add_parser(name, help=spec["help"])
        spec.get("setup", lambda p: None)(p)
        p.set_defaults(_handler=spec["handler"], _group=name, _command=None)

    for group_name, commands in GROUPS.items():
        group = sub.add_parser(group_name, help=f"{group_name} things")
        csub = group.add_subparsers(dest="command", metavar="<subcommand>")
        for cmd_name, spec in commands.items():
            c = csub.add_parser(cmd_name, help=spec["help"])
            spec.get("setup", lambda p: None)(c)
            c.set_defaults(_handler=spec["handler"])

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Parse argv, run the command, return an exit code.

    Never raises SystemExit. argparse exits with SystemExit(2) on a bad
    argument, which would tear down a caller that merely wanted a status
    code -- and makes this function awkward to drive from a test. The
    parse errors have already been printed to stderr by argparse at that
    point, so all that is left is to translate the exception into the
    same code argparse would have used.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or EXIT_USAGE)

    if not getattr(args, "_handler", None):
        parser.print_help()
        return EXIT_USAGE

    return args._handler(args)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def cli_main() -> None:
    """Console-script wrapper: checks for pactl, then hands off to main."""
    if shutil.which("pactl") is None:
        print("Error: PulseAudio command-line utility (pactl) not found!", file=sys.stderr)
        print("Install it with one of:", file=sys.stderr)
        print("  Debian/Ubuntu: sudo apt-get install pulseaudio-utils", file=sys.stderr)
        print("  Fedora:        sudo dnf install pulseaudio-utils", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    sys.exit(main())


if __name__ == "__main__":
    cli_main()
