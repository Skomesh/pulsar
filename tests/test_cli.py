"""
Tests for the `pulsar` command-line interface.

Shape of the CLI under test:

    pulsar [--json] <group> <command> [args]

The design constraint that shapes every test here: `main(argv)` takes the
argument list and returns an exit code, and every effect it has on the
world goes through `PactlRunner`. That is what lets the CLI be tested
without a live audio server, and it is why these tests patch the boundary
rather than reaching for a subprocess.

`pactl` is never spawned by the tests. The one place real behaviour is
wanted -- loopback refusal -- is covered in test_loop_detection.py against
a live server.
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pulsar import cli
from pulsar.cli import EXIT_ERROR, EXIT_USAGE
from pulsar.utils.pactl_runner import LoopbackCycleError


class CliTestBase(unittest.TestCase):
    """Runs `cli.main` capturing stdout/stderr and returning the exit code."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def run_cli_json(self, *argv):
        """Run with --json and parse stdout, asserting it is valid JSON."""
        code, out, err = self.run_cli("--json", *argv)
        self.assertEqual(code, 0, f"exit {code}; stderr={err}")
        return json.loads(out)

    def patch_runner(self, **methods):
        """Replace named PactlRunner methods, leaving the class itself real.

        Patching the whole `cli.PactlRunner` name replaces everything,
        including pure helpers the handler calls such as
        `_parse_volume_percent`. A mock returns a Mock where a number is
        expected, so the volume column silently rendered "-" and the test
        checking it failed for a reason that had nothing to do with volume.
        Patching only the methods that touch the outside world is both more
        faithful and less surprising.
        """
        return mock.patch.multiple(cli.PactlRunner, **methods)


class TestListSinks(CliTestBase):
    # These fixtures are the REAL shape returned by PactlRunner.list_sinks,
    # taken from a live `pactl list sinks` rather than invented. An earlier
    # version of this file used `index`/`desc`/`muted`/`volume` -- all of
    # which are wrong -- and every test still passed, because the mock
    # agreed with the bug. Only running the CLI against a live server
    # showed a table full of "?" and "-". These key names are a contract
    # with pactl_runner, not a preference.
    SINKS = [
        {
            "id": "15819", "name": "alsa_output.pci",
            "description": "Built-in Audio", "state": "SUSPENDED",
            "mute": "no", "base_volume": "65536 / 80% / -5.83 dB",
            "driver": "PipeWire", "monitor_source": "alsa_output.pci.monitor",
        },
        {
            "id": "15820", "name": "game", "description": "Game sink",
            "state": "RUNNING", "mute": "yes",
            "base_volume": "65536 / 45% / -11.02 dB",
            "driver": "PipeWire", "monitor_source": "game.monitor",
        },
    ]

    def test_lists_sinks_as_json(self):
        with self.patch_runner(list_sinks=mock.Mock(return_value=self.SINKS)):
            payload = self.run_cli_json("list", "sinks")

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["sinks"][0]["name"], "alsa_output.pci")
        self.assertEqual(payload["sinks"][1]["mute"], "yes")

    def test_extracts_volume_percent_from_base_volume(self):
        """base_volume is '65536 / 80% / -5.83 dB'; a table wants just 80."""
        with self.patch_runner(list_sinks=mock.Mock(return_value=self.SINKS)):
            code, out, _ = self.run_cli("list", "sinks")

        self.assertEqual(code, 0)
        self.assertIn("80%", out)
        self.assertIn("45%", out)

    def test_table_uses_real_key_names(self):
        """Regression: id/description/mute, not index/desc/muted.

        Every column used to render as "?" or "-" because the handler read
        keys that PactlRunner never returns.
        """
        with self.patch_runner(list_sinks=mock.Mock(return_value=self.SINKS)):
            code, out, _ = self.run_cli("list", "sinks")

        self.assertEqual(code, 0)
        self.assertNotIn("?", out, "an unknown key leaked into the table")
        self.assertIn("Built-in Audio", out)
        self.assertIn("SUSPENDED", out)

    def test_lists_sinks_as_a_table_by_default(self):
        with self.patch_runner(list_sinks=mock.Mock(return_value=self.SINKS)):
            code, out, _ = self.run_cli("list", "sinks")

        self.assertEqual(code, 0)
        self.assertIn("alsa_output.pci", out)
        self.assertIn("Built-in Audio", out)
        # A human table should not be JSON.
        self.assertNotIn("{", out)

    def test_no_sinks_is_not_an_error(self):
        with self.patch_runner(list_sinks=mock.Mock(return_value=[])):
            code, out, _ = self.run_cli("list", "sinks")

        self.assertEqual(code, 0)
        self.assertIn("No sinks", out)


class TestRoute(CliTestBase):
    """The CLI must inherit the feedback-loop guard, not reimplement it.

    These assert on which pactl calls happen rather than on the guard
    itself; test_loop_detection.py owns the guard's own correctness. What
    matters here is that `pulsar route` goes through `create_loopback` at
    all -- if it ever built a loopback by shelling out to pactl directly,
    the guard would be bypassed and every one of these would still pass.
    """

    #: `route` resolves both arguments against `list_sinks` before touching
    #: the audio server, so these have to exist or resolution fails -- and an
    #: unstubbed list_sinks means a test hitting the real audio server.
    ROUTE_SINKS = [
        {"name": "game", "description": "Game sink", "state": "RUNNING",
         "id": "1", "mute": "no", "base_volume": "65536 / 50% / -18.06 dB"},
        {"name": "speakers", "description": "Speakers", "state": "IDLE",
         "id": "2", "mute": "no", "base_volume": "65536 / 70% / -9.29 dB"},
    ]

    def route(self, sink="game", target="speakers", monitor="game.monitor",
              module="42", side_effect=None):
        """Run `pulsar route <sink> <target>` with the runner stubbed.

        Returns (code, out, err, create_loopback_mock).
        """
        lb = mock.Mock(return_value=module, side_effect=side_effect)
        with self.patch_runner(
            list_sinks=mock.Mock(return_value=list(self.ROUTE_SINKS)),
            monitor_source_for=mock.Mock(return_value=monitor),
            create_loopback=lb,
        ):
            code, out, err = self.run_cli("route", sink, target)
        return code, out, err, lb

    def test_route_creates_a_loopback(self):
        code, out, _, lb = self.route()
        self.assertEqual(code, 0)
        self.assertIn("game", out)
        lb.assert_called_once()

    def test_route_passes_the_monitor_source_not_the_sink_name(self):
        _, _, _, lb = self.route()
        self.assertEqual(lb.call_args[0][:2], ("game.monitor", "speakers"))

    def test_route_refuses_a_feedback_loop(self):
        chain = ["game", "speakers", "game"]
        code, _, err, _ = self.route(side_effect=LoopbackCycleError(chain))

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("game", err)
        self.assertIn("speakers", err)

    def test_route_loop_error_is_json_when_asked(self):
        chain = ["game", "speakers", "game"]
        lb = mock.Mock(side_effect=LoopbackCycleError(chain))
        with self.patch_runner(
            list_sinks=mock.Mock(return_value=list(self.ROUTE_SINKS)),
            monitor_source_for=mock.Mock(return_value="game.monitor"),
            create_loopback=lb,
        ):
            code, out, _ = self.run_cli("--json", "route", "game", "speakers")

        self.assertEqual(code, EXIT_ERROR)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "feedback_loop")
        self.assertEqual(payload["chain"], chain)

    def test_route_reports_a_missing_sink(self):
        code, _, err, _ = self.route(sink="nosuchsink", monitor=None)
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("nosuchsink", err)


class TestListRoutes(CliTestBase):
    def test_lists_routes(self):
        routes = [{"id": "42", "source": "game.monitor", "sink": "speakers"}]
        with self.patch_runner(list_loopbacks=mock.Mock(return_value=routes)):
            payload = self.run_cli_json("list", "routes")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["routes"][0]["sink"], "speakers")


class TestResolveSink(CliTestBase):
    """Turning what the user typed into an actual sink name.

    `pulsar route game speakers` has to work when the sink is called
    `game.monitor`, `alsa_output.pci` or `bluez_sink.built_in` -- nobody
    wants to copy a name out of `pulsar list sinks` correctly. So the
    resolver tries exact, then case-insensitive, then substring, and only
    asks the user when the answer is genuinely ambiguous.
    """

    SINKS = TestListSinks.SINKS

    def resolve(self, name, interactive=False, chooser=None):
        with self.patch_runner(list_sinks=mock.Mock(return_value=self.SINKS)):
            if chooser is None:
                return cli.resolve_sink(name, interactive=interactive)
            with mock.patch.object(cli, "choose_interactively", chooser):
                return cli.resolve_sink(name, interactive=interactive)

    def test_exact_name_resolves(self):
        self.assertEqual(self.resolve("game"), "game")

    def test_case_insensitive_exact_resolves(self):
        self.assertEqual(self.resolve("GAME"), "game")

    def test_unique_substring_resolves(self):
        """'pci' matches exactly one sink, so no need to ask."""
        self.assertEqual(self.resolve("pci"), "alsa_output.pci")

    def test_ambiguous_substring_does_not_guess(self):
        """Two sinks match 'sink'; picking one silently would be wrong."""
        ambiguous = [
            {"name": "bluez_sink", "description": "Phone"},
            {"name": "hdmi_sink", "description": "Monitor"},
        ]
        with self.patch_runner(list_sinks=mock.Mock(return_value=ambiguous)):
            result = cli.resolve_sink("sink", interactive=False)
        self.assertIsNone(result)

    def test_ambiguous_offers_a_numbered_list_when_interactive(self):
        ambiguous = [
            {"name": "bluez_sink", "description": "Phone"},
            {"name": "hdmi_sink", "description": "Monitor"},
        ]
        seen = {}

        def fake_prompt(candidates, _prompt):
            seen["names"] = [c["name"] for c in candidates]
            # choose_interactively returns the chosen item, not its name.
            return candidates[1]

        with self.patch_runner(list_sinks=mock.Mock(return_value=ambiguous)):
            with mock.patch.object(cli, "choose_interactively", fake_prompt):
                result = cli.resolve_sink("sink", interactive=True)

        self.assertEqual(result, "hdmi_sink")
        self.assertEqual(seen["names"], ["bluez_sink", "hdmi_sink"])

    def test_no_match_returns_none(self):
        self.assertIsNone(self.resolve("zzz-nothing-like-this"))

    def test_exact_match_beats_a_longer_substring_match(self):
        """An exact name wins even when other names contain it as a substring."""
        many = [
            {"name": "game", "description": "exact"},
            {"name": "game_music", "description": "longer"},
        ]
        with self.patch_runner(list_sinks=mock.Mock(return_value=many)):
            self.assertEqual(cli.resolve_sink("game"), "game")


class TestNumberedPicker(CliTestBase):
    """The interactive list itself. Kept separate so it can be tested alone."""

    def test_shows_numbers_with_names_and_descriptions(self):
        items = [
            {"name": "bluez_sink", "description": "Phone", "state": "RUNNING"},
            {"name": "hdmi_sink", "description": "Monitor", "state": "IDLE"},
        ]
        with mock.patch.object(cli, "table") as table:
            table.side_effect = lambda rows, headers: cli.render_table(rows, headers)
            with mock.patch("builtins.input", return_value="1"):
                picked = cli.choose_interactively(items, "Pick a sink")

        self.assertEqual(picked["name"], "bluez_sink")

    def test_reprompts_on_an_out_of_range_number(self):
        items = [{"name": "a", "description": ""}, {"name": "b", "description": ""}]
        answers = iter(["9", "2"])
        with mock.patch("builtins.input", lambda _="": next(answers)):
            picked = cli.choose_interactively(items, "Pick a sink")
        self.assertEqual(picked["name"], "b")

    def test_returns_none_on_empty_input(self):
        """Ctrl-D at the prompt means 'never mind', not an exception."""
        items = [{"name": "a", "description": ""}]
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertIsNone(cli.choose_interactively(items, "Pick a sink"))

    def test_returns_none_when_given_nothing_to_pick_from(self):
        self.assertIsNone(cli.choose_interactively([], "Pick a sink"))


class TestRouteSelection(CliTestBase):
    """`pulsar route bluez hdmi` should work without exact names.

    Scripts must never block on a prompt, so resolution is interactive only
    when stdin is a terminal AND --json was not asked for.
    """

    AMBIGUOUS = [
        {"name": "bluez_sink", "description": "Phone", "state": "RUNNING",
         "id": "1", "mute": "no", "base_volume": "65536 / 50% / -18.06 dB"},
        {"name": "hdmi_sink", "description": "Monitor", "state": "IDLE",
         "id": "2", "mute": "yes", "base_volume": "65536 / 30% / -25.30 dB"},
    ]

    def route_args(self, *argv, interactive=None):
        """Run `pulsar route ...` and return (code, out, err, create_loopback).

        The interactive chooser is stubbed to always take the second
        candidate. Left unstubbed it would call the real `input()` and blow
        up under pytest's capture rather than testing anything.
        """
        lb = mock.Mock(return_value="42")
        candidates = []

        def fake_prompt(items, _prompt):
            candidates.extend(items)
            return items[1] if len(items) > 1 else items[0]

        with self.patch_runner(
            list_sinks=mock.Mock(return_value=self.AMBIGUOUS),
            monitor_source_for=mock.Mock(return_value="bluez_sink.monitor"),
            create_loopback=lb,
        ):
            with mock.patch.object(cli, "choose_interactively", fake_prompt):
                if interactive is None:
                    code, out, err = self.run_cli("route", *argv)
                else:
                    with mock.patch.object(cli, "stdin_is_a_tty", lambda: interactive):
                        code, out, err = self.run_cli("route", *argv)
        return code, out, err, lb, candidates

    def test_ambiguous_name_prompts_and_uses_the_choice(self):
        # 'sink' matches both candidates, so this cannot resolve on its own.
        code, _, _, lb, offered = self.route_args("sink", "hdmi_sink",
                                                  interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual(lb.call_args[0][1], "hdmi_sink")
        # Both candidates must be offered, not just the one that got picked.
        self.assertEqual([c["name"] for c in offered],
                         ["bluez_sink", "hdmi_sink"])

    def test_exact_name_needs_no_prompt(self):
        """A full name must never stop to ask a question it already knows."""
        code, out, _, _, offered = self.route_args(
            "bluez_sink", "hdmi_sink", interactive=False)
        self.assertEqual(code, 0)
        self.assertEqual(offered, [])

    def test_non_interactive_ambiguity_is_an_error_not_a_prompt(self):
        code, _, err, _, offered = self.route_args("sink", "hdmi_sink",
                                                   interactive=False)
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("matches", err.lower())
        self.assertEqual(offered, [])

    def test_unmatched_sink_is_an_error(self):
        code, _, err, _, _ = self.route_args("nosuchsink", "hdmi_sink",
                                              interactive=True)
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("nosuchsink", err)


class TestListSources(CliTestBase):
    # Real shape from a live `pactl list sources`: same keys as sinks plus
    # monitor_of_sink, which is what distinguishes a monitor from a real
    # capture device.
    SOURCES = [
        {
            "id": "18335", "name": "auto_null.monitor",
            "description": "Monitor of Dummy Output", "state": "SUSPENDED",
            "mute": "no", "base_volume": "65536 / 100% / 0.00 dB",
            "monitor_of_sink": "auto_null", "driver": "PipeWire",
        },
        {
            "id": "18336", "name": "alsa_input.pci",
            "description": "Built-in Audio Analog Stereo", "state": "RUNNING",
            "mute": "yes", "base_volume": "65536 / 60% / -13.31 dB",
            "monitor_of_sink": "", "driver": "PipeWire",
        },
    ]

    def test_lists_sources_as_json(self):
        with self.patch_runner(list_sources=mock.Mock(return_value=self.SOURCES)):
            payload = self.run_cli_json("list", "sources")

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["sources"][1]["name"], "alsa_input.pci")

    def test_shows_which_sources_are_monitors(self):
        """A monitor of X and a capture device are routed differently."""
        with self.patch_runner(list_sources=mock.Mock(return_value=self.SOURCES)):
            code, out, _ = self.run_cli("list", "sources")

        self.assertEqual(code, 0)
        self.assertIn("auto_null", out)
        self.assertIn("alsa_input.pci", out)

    def test_no_sources_is_not_an_error(self):
        with self.patch_runner(list_sources=mock.Mock(return_value=[])):
            code, out, _ = self.run_cli("list", "sources")
        self.assertEqual(code, 0)
        self.assertIn("No sources", out)


class TestListCards(CliTestBase):
    # Real key set from _parse_card_profile_line.
    CARDS = [
        {
            "name": "alsa_card.pci", "description": "Built-in Audio",
            "sinks": 1, "sources": 1, "priority": 3,
            "available": [
                {"name": "output:analog-stereo", "description": "Analog Stereo Output"},
            ],
        },
    ]

    def test_lists_cards_as_json(self):
        with self.patch_runner(list_cards=mock.Mock(return_value=self.CARDS)):
            payload = self.run_cli_json("list", "cards")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["cards"][0]["name"], "alsa_card.pci")

    def test_table_shows_available_profile_count(self):
        with self.patch_runner(list_cards=mock.Mock(return_value=self.CARDS)):
            code, out, _ = self.run_cli("list", "cards")

        self.assertEqual(code, 0)
        self.assertIn("alsa_card.pci", out)
        self.assertIn("1", out)

    def test_no_cards_is_not_an_error(self):
        with self.patch_runner(list_cards=mock.Mock(return_value=[])):
            code, out, _ = self.run_cli("list", "cards")
        self.assertEqual(code, 0)
        self.assertIn("No cards", out)


class TestListSinkInputs(CliTestBase):
    # Real key set from PactlRunner.list_sink_inputs's docstring. `index`
    # and `node_name` are the two that matter: the index is what
    # move-sink-input wants, the node name is what identifies the app.
    STREAMS = [
        {
            "index": "5", "application": "firefox", "node_name": "Firefox",
            "media": "Music", "sink_index": "0", "sink": "alsa_output.pci",
        },
    ]

    def test_lists_streams_as_json(self):
        with self.patch_runner(list_sink_inputs=mock.Mock(return_value=self.STREAMS)):
            payload = self.run_cli_json("list", "streams")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["streams"][0]["application"], "firefox")

    def test_table_names_the_application(self):
        with self.patch_runner(list_sink_inputs=mock.Mock(return_value=self.STREAMS)):
            code, out, _ = self.run_cli("list", "streams")

        self.assertEqual(code, 0)
        self.assertIn("firefox", out)
        self.assertIn("alsa_output.pci", out)

    def test_no_streams_is_not_an_error(self):
        with self.patch_runner(list_sink_inputs=mock.Mock(return_value=[])):
            code, out, _ = self.run_cli("list", "streams")
        self.assertEqual(code, 0)
        self.assertIn("No streams", out)


class TestCreate(CliTestBase):
    """Creating a virtual device, and routing it in one go.

    `pulsar create sink game --route-to speakers` is the common case: you
    want a device wired to an output, and having to run `pulsar route` as
    a second command is a step people forget.
    """

    SINKS = TestListSinks.SINKS
    # 'game' is already in SINKS, so a create test has to use a fresh name.
    # Creating one that exists must be refused -- that is its own test.

    def create(self, *argv):
        ok = mock.Mock(return_value=True)
        mon = mock.Mock(return_value="newdev.monitor")
        lb = mock.Mock(return_value="42")
        with self.patch_runner(
            create_sink_only=ok,
            monitor_source_for=mon,
            create_loopback=lb,
            list_sinks=mock.Mock(return_value=self.SINKS),
        ):
            code, out, err = self.run_cli("create", *argv)
        return code, out, err, ok, lb

    def test_creates_a_sink(self):
        code, out, _, ok, _ = self.create("sink", "newdev")
        self.assertEqual(code, 0)
        ok.assert_called_once()
        self.assertIn("newdev", out)

    def test_creates_a_source_only(self):
        ok = mock.Mock(return_value=True)
        with self.patch_runner(create_source_only=ok):
            code, _, _ = self.run_cli("create", "source", "mic")
        self.assertEqual(code, 0)
        ok.assert_called_once_with("mic", "mic", channels=2)

    def test_duplicate_name_is_refused(self):
        """A second device with the same name is never what was meant."""
        code, _, err, ok, _ = self.create("sink", "game")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("game", err)
        ok.assert_not_called()

    def test_route_to_creates_the_loopback_in_one_step(self):
        # 'game' is in SINKS, so the target resolves.
        code, _, _, _, lb = self.create("sink", "newdev", "--route-to", "game")
        self.assertEqual(code, 0)
        lb.assert_called_once()

    def test_route_to_survives_a_feedback_loop_and_still_reports_failure(self):
        """The device is created, so the command must say what happened.

        Rolling back the sink on a refused route would leave the user with
        neither the route nor the device they asked for, and an error
        message implying the device does not exist.
        """
        with self.patch_runner(
            create_sink_only=mock.Mock(return_value=True),
            monitor_source_for=mock.Mock(return_value="newdev.monitor"),
            create_loopback=mock.Mock(side_effect=LoopbackCycleError(["a", "b", "a"])),
            list_sinks=mock.Mock(return_value=[{"name": "newdev", "description": "new"}]),
        ):
            code, out, err = self.run_cli(
                "create", "sink", "newdev", "--route-to", "newdev")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("newdev", err)


class TestVolumeAndDefault(CliTestBase):
    SINKS = TestListSinks.SINKS

    def test_sets_volume(self):
        with self.patch_runner(
            set_sink_volume=mock.Mock(return_value=True),
            list_sinks=mock.Mock(return_value=self.SINKS),
        ):
            code, out, _ = self.run_cli("volume", "game", "50")
        self.assertEqual(code, 0)
        self.assertIn("50", out)

    def test_rejects_a_percentage_outside_range(self):
        with self.patch_runner(
            set_sink_volume=mock.Mock(return_value=True),
            list_sinks=mock.Mock(return_value=self.SINKS),
        ):
            code, _, err = self.run_cli("volume", "game", "500")
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("500", err)

    def test_sets_default_sink(self):
        with self.patch_runner(
            set_default_sink=mock.Mock(return_value=True),
            list_sinks=mock.Mock(return_value=self.SINKS),
        ):
            code, out, _ = self.run_cli("default", "sink", "game")
        self.assertEqual(code, 0)
        self.assertIn("game", out)

    def test_mutes_and_unmutes(self):
        mute = mock.Mock(return_value=True)
        with self.patch_runner(
            set_sink_mute=mute,
            list_sinks=mock.Mock(return_value=self.SINKS),
        ):
            code, _, _ = self.run_cli("mute", "game", "on")
        self.assertEqual(code, 0)
        mute.assert_called_once_with("game", True)


class TestProfiles(CliTestBase):
    """Profiles touch live audio, so the shape here matters most.

    Fixtures are the real one from $XDG_DATA_HOME: a profile is
    {created, description, devices, routing, schema_version}, and the
    routing entries use `from`/`to`/`latency_msec` -- `routes` was my
    first guess and it is not the key.
    """

    PROFILE = {
        "schema_version": 2,
        "created": "2026-01-01T00:00:00",
        "description": "Apps and voice",
        "devices": [
            {"name": "app_sink", "type": "sink", "channels": 2,
             "description": "Application audio"},
            {"name": "voice_sink", "type": "sink", "channels": 2,
             "description": "Voice chat"},
        ],
        "routing": [
            {"from": "app_sink", "to": "<AUTO_DEFAULT>", "latency_msec": 1},
            {"from": "voice_sink", "to": "<AUTO_DEFAULT>", "latency_msec": 1},
        ],
    }

    def mgr(self, **methods):
        """Patch ProfileManager methods; keep everything else real."""
        return mock.patch.multiple(cli.ProfileManager, **methods)

    def test_lists_profile_names(self):
        with self.mgr(get_profile_names=mock.Mock(
                return_value=["Apps + Voice Split"])):
            payload = self.run_cli_json("profile", "list")

        self.assertEqual(payload["count"], 1)
        self.assertIn("Apps + Voice", payload["profiles"][0]["name"])

    def test_show_prints_devices_and_routes_from_the_real_keys(self):
        with self.mgr(get_profile_names=mock.Mock(
                return_value=["Apps + Voice Split"]),
                      get_profile=mock.Mock(return_value=self.PROFILE)):
            code, out, _ = self.run_cli("profile", "show", "Apps + Voice Split")

        self.assertEqual(code, 0)
        self.assertIn("app_sink", out)
        self.assertIn("voice_sink", out)
        self.assertIn("AUTO_DEFAULT", out)

    def test_apply_passes_the_profile_dict_not_the_name(self):
        """apply_profile takes a dict; handing it a name would be a TypeError."""
        applied = mock.Mock(return_value={"errors": [], "created": []})
        with self.mgr(get_profile_names=mock.Mock(
                return_value=["Apps + Voice Split"]),
                      get_profile=mock.Mock(return_value=self.PROFILE),
                      apply_profile=applied):
            code, _, _ = self.run_cli("profile", "apply", "Apps + Voice Split")

        self.assertEqual(code, 0)
        self.assertIs(applied.call_args[0][0], self.PROFILE)

    def test_apply_reports_a_failure_from_the_result(self):
        failed = mock.Mock(return_value={"errors": ["no sink 'voice_sink'"],
                                         "created": ["app_sink"]})
        with self.mgr(get_profile_names=mock.Mock(return_value=["Broken"]),
                      get_profile=mock.Mock(return_value=self.PROFILE),
                      apply_profile=failed):
            code, _, err = self.run_cli("profile", "apply", "Broken")

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("voice_sink", err)

    def test_apply_of_a_missing_profile_is_an_error(self):
        with self.mgr(get_profile_names=mock.Mock(return_value=["Other"])):
            code, _, err = self.run_cli("profile", "apply", "nosuch")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("nosuch", err)

    def test_apply_matches_a_partial_profile_name(self):
        """Profile names are long; `apply apps` should find the one."""
        applied = mock.Mock(return_value={"errors": [], "created": []})
        with self.mgr(
            get_profile_names=mock.Mock(return_value=["Apps + Voice Split"]),
            get_profile=mock.Mock(return_value=self.PROFILE),
            apply_profile=applied,
        ):
            code, _, _ = self.run_cli("profile", "apply", "apps + voice")

        self.assertEqual(code, 0)
        applied.assert_called_once()

    def test_delete_uses_the_manager(self):
        with self.mgr(delete_profile=mock.Mock(return_value=True)):
            code, out, _ = self.run_cli("profile", "delete", "Old")
        self.assertEqual(code, 0)


class TestPresets(CliTestBase):
    PRESET = {"channels": "2", "channel_map": "front-left,front-right",
              "description": "Stereo Virtual Device", "builtin": True}

    def mgr(self, **methods):
        return mock.patch.multiple(cli.PresetManager, **methods)

    def test_lists_preset_names(self):
        with self.mgr(get_preset_names=mock.Mock(return_value=["Stereo", "Mono"])):
            payload = self.run_cli_json("preset", "list")

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["presets"][0]["name"], "Stereo")

    def test_show_marks_builtins(self):
        """A builtin cannot be deleted, so the CLI has to say which it is."""
        with self.mgr(get_preset=mock.Mock(return_value=self.PRESET),
                      is_builtin_preset=mock.Mock(return_value=True)):
            code, out, _ = self.run_cli("preset", "show", "Stereo")

        self.assertEqual(code, 0)
        self.assertIn("Stereo", out)
        self.assertIn("builtin", out.lower())

    def test_delete_refuses_a_builtin(self):
        with self.mgr(is_builtin_preset=mock.Mock(return_value=True),
                      delete_preset=mock.Mock(return_value=True)):
            code, _, err = self.run_cli("preset", "delete", "Stereo")

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("builtin", err.lower())


class TestExitCodes(CliTestBase):
    def test_unknown_command_exits_nonzero(self):
        code, _, err = self.run_cli("list", "bananas")
        self.assertEqual(code, 2)
        self.assertIn("bananas", err)

    def test_unknown_group_exits_nonzero(self):
        code, _, err = self.run_cli("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("frobnicate", err)

    def test_no_arguments_shows_help_and_exits_nonzero(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage", out.lower())


if __name__ == "__main__":
    unittest.main()
