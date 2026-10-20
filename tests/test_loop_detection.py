"""
Tests for routing-loop detection.

Routing a sink's monitor onto a device can close a feedback loop: the
audio ends up feeding back into its own input and the output screams.
Two shapes, both confirmed to load on a live PipeWire server with no
error and no warning:

  * self-loop  -- game.monitor -> game
  * mutual     -- game.monitor -> voice  AND  voice.monitor -> game

Neither is caught by pactl. Nothing in the codebase compared a proposed
target against the routes already in place, so the guard has to be built
from the loopback list.

The analysis is a pure function over the loopback dicts, so it is tested
here without touching a server. The tests that involve a real audio
server live in test_routing_loop_guard_live.py.
"""

import unittest
from contextlib import ExitStack
from unittest import mock

from pulsar.utils.pactl_runner import LoopbackCycleError, PactlRunner


def lb(source, sink):
    """One loopback in the shape list_loopbacks() returns."""
    return {
        "id": f"m{source}{sink}",
        "source": source,
        "sink": sink,
    }


def monitor_of(sink_name):
    return PactlRunner.monitor_source_for(sink_name)


class TestSelfLoop(unittest.TestCase):
    """The simplest loop, and the easiest to reach by accident."""

    def test_sink_routed_to_itself_is_a_cycle(self):
        chain = PactlRunner.loopback_cycle([], monitor_of("game"), "game")
        self.assertIsNotNone(chain, "a sink routed to its own monitor is a loop")

    def test_chain_names_the_sink_twice(self):
        chain = PactlRunner.loopback_cycle([], monitor_of("game"), "game")
        self.assertEqual(chain[0], chain[-1], "a cycle starts where it ends")
        self.assertEqual(set(chain), {"game"})


class TestNoFalsePositives(unittest.TestCase):
    """A guard that blocks valid routing is worse than no guard."""

    def test_empty_graph_allows_anything(self):
        self.assertIsNone(
            PactlRunner.loopback_cycle([], monitor_of("game"), "headphones")
        )

    def test_unrelated_route_does_not_block(self):
        existing = [lb(monitor_of("other"), "speakers")]
        self.assertIsNone(
            PactlRunner.loopback_cycle(existing, monitor_of("game"), "headphones")
        )

    def test_routing_a_to_c_then_b_is_fine(self):
        """a->c, then a->b: both fan out, neither feeds back."""
        existing = [lb(monitor_of("a"), "c")]
        self.assertIsNone(PactlRunner.loopback_cycle(existing, monitor_of("a"), "b"))

    def test_duplicate_route_is_not_a_loop(self):
        """Re-adding an existing route is idempotent, not a cycle."""
        existing = [lb(monitor_of("a"), "b")]
        self.assertIsNone(PactlRunner.loopback_cycle(existing, monitor_of("a"), "b"))

    def test_chain_deepening_is_not_a_loop(self):
        existing = [lb(monitor_of("a"), "b"), lb(monitor_of("b"), "c")]
        self.assertIsNone(PactlRunner.loopback_cycle(existing, monitor_of("a"), "c"))


class TestMutualCycle(unittest.TestCase):
    """Two individually valid routes that together loop."""

    def test_two_step_cycle_is_detected(self):
        # a.monitor -> b already exists; proposing b.monitor -> a closes it.
        existing = [lb(monitor_of("a"), "b")]
        chain = PactlRunner.loopback_cycle(existing, monitor_of("b"), "a")
        self.assertIsNotNone(chain)

    def test_three_step_cycle_is_detected(self):
        existing = [
            lb(monitor_of("a"), "b"),
            lb(monitor_of("b"), "c"),
        ]
        chain = PactlRunner.loopback_cycle(existing, monitor_of("c"), "a")
        self.assertIsNotNone(chain)

    def test_cycle_is_reported_in_order(self):
        """The user needs to see WHICH route closes the loop."""
        existing = [
            lb(monitor_of("game"), "voice"),
            lb(monitor_of("voice"), "music"),
        ]
        chain = PactlRunner.loopback_cycle(existing, monitor_of("music"), "game")
        self.assertEqual(chain, ["music", "game", "voice", "music"])

    def test_reverse_direction_is_not_a_loop(self):
        """a->b exists. Proposing a->b again or b->c is fine."""
        existing = [lb(monitor_of("a"), "b")]
        self.assertIsNone(
            PactlRunner.loopback_cycle(existing, monitor_of("b"), "c")
        )


class TestMalformedInput(unittest.TestCase):
    """Live data is not clean. A guard must not crash on it."""

    def test_loopback_without_source_is_ignored(self):
        existing = [{"id": "x", "sink": "speakers"}]
        self.assertIsNone(
            PactlRunner.loopback_cycle(existing, monitor_of("game"), "headphones")
        )

    def test_loopback_without_sink_is_ignored(self):
        existing = [{"id": "x", "source": monitor_of("game")}]
        self.assertIsNone(
            PactlRunner.loopback_cycle(existing, monitor_of("game"), "headphones")
        )

    def test_source_without_monitor_suffix_is_still_a_real_edge(self):
        """A loopback whose source is a bare sink name IS part of the graph.

        This used to assert the opposite, on the reasoning that such an
        entry "has no sink to treat as its origin". That reasoning was
        wrong: `module-loopback source=game sink=voice` loads with rc=0 and
        no warning, so the entry is real, and treating it as invisible let a
        live feedback loop through. See issue #15 -- reproduced against a
        running server, where the reverse route was accepted with no
        LoopbackCycleError.

        The origin is the source with any trailing `.monitor` removed, so a
        bare `game` and a `game.monitor` are the same node.
        """
        existing = [{"id": "x", "source": "game", "sink": "voice"}]
        self.assertIsNotNone(
            PactlRunner.loopback_cycle(existing, monitor_of("voice"), "game")
        )

    def test_empty_source_monitor_never_loops(self):
        self.assertIsNone(PactlRunner.loopback_cycle([], "", "speakers"))


class _PactlStub:
    """Stubs the two pactl calls create_loopback makes, recording argv."""

    def __init__(self, loopbacks, returncode=0, stdout=""):
        self.loopbacks = loopbacks
        self.returncode = returncode
        self.stdout = stdout
        self.calls = []
        self.stack = ExitStack()

    def __enter__(self):
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_loopbacks", return_value=self.loopbacks
        ))
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "run_command", side_effect=self._run
        ))
        return self

    def __exit__(self, *exc):
        self.stack.close()
        return False

    def _run(self, command, logger=None):
        self.calls.append(command)
        return self.stdout, self.returncode

    def loaded_a_loopback(self):
        return any("load-module" in c for c in self.calls)


class TestCreateLoopbackRefusesCycles(unittest.TestCase):
    """The guard must live in create_loopback, not in its five callers.

    Call sites today: apply_routing, _on_quick_route_apply,
    apply_multi_routing, _on_capture_route_clicked, and
    ProfileManager.apply_profile. A guard added to the UI handlers would
    leave the profile path unprotected.
    """

    def test_self_loop_never_reaches_pactl(self):
        with _PactlStub([]) as pactl:
            with self.assertRaises(LoopbackCycleError):
                PactlRunner.create_loopback("game.monitor", "game")
        self.assertFalse(
            pactl.loaded_a_loopback(), "pactl must not be asked to build a loop"
        )

    def test_two_step_cycle_never_reaches_pactl(self):
        with _PactlStub([lb("a.monitor", "b")]) as pactl:
            with self.assertRaises(LoopbackCycleError):
                PactlRunner.create_loopback("b.monitor", "a")
        self.assertFalse(pactl.loaded_a_loopback())

    def test_valid_route_still_loads(self):
        with _PactlStub([], stdout="42\n") as pactl:
            result = PactlRunner.create_loopback(
                "game.monitor", "headphones"
            )
        self.assertEqual(result, "42")
        self.assertTrue(pactl.loaded_a_loopback())

    def test_cycle_is_reported_with_its_chain(self):
        """Silently skipping is worse than refusing: the user asked for
        something and would otherwise get nothing."""
        with _PactlStub([lb("game.monitor", "voice")]) as pactl:
            with self.assertRaises(LoopbackCycleError) as ctx:
                PactlRunner.create_loopback("voice.monitor", "game")
        self.assertEqual(ctx.exception.chain, ["voice", "game", "voice"])
        self.assertFalse(pactl.loaded_a_loopback())


if __name__ == "__main__":
    unittest.main()
