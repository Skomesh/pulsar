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
from pulsar.cli import EXIT_ERROR
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

    def route(self, sink="game", target="speakers", monitor="game.monitor",
              module="42", side_effect=None):
        """Run `pulsar route <sink> <target>` with the runner stubbed.

        Returns (code, out, err, create_loopback_mock).
        """
        lb = mock.Mock(return_value=module, side_effect=side_effect)
        with self.patch_runner(
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
