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


def cmd_route(args) -> int:
    """Route a sink's monitor into a target sink.

    Deliberately goes through `create_loopback`, not `pactl load-module`.
    That is what makes the feedback-loop guard apply here exactly as it
    does in the GUI -- a second code path that shelled out to pactl
    directly would be a hole straight past the thing that stops a device
    screaming at full volume.
    """
    sink, target = args.sink, args.target

    monitor = PactlRunner.monitor_source_for(sink)
    if not monitor:
        print(f"Error: no sink named '{sink}'.", file=sys.stderr)
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
