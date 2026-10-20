"""
Regression tests for the feedback-loop guard's coverage gap (issue #15).

`loopback_cycle` used to skip any existing loopback whose `source` did not
end in `.monitor`. But `module-null-sink` and `module-loopback` accept a
plain sink name as the source just as readily:

    pactl load-module module-loopback source=audA_sink sink=audB_sink

That loaded with rc=0, appeared in list_loopbacks() verbatim, and the
reverse route -- which closes audA -> audB -> audA -- was accepted with no
LoopbackCycleError. A device at full volume could still be made to feed
back into its own input.

The graph therefore has to normalise both ends: `audA_sink` and
`audA_sink.monitor` name the same origin sink.

Note the obvious fix is wrong. `str.split(".")[0]` mangles real device
names: `alsa_output.pci` would become `alsa_output`. Only a trailing
`.monitor` is stripped.

These are pure-function tests. The live-server proof lives in
tests/test_ownership.py's sibling checks and in the commit body.
"""

import unittest

from pulsar.utils.pactl_runner import PactlRunner


class TestBareSinkSourceIsAnEdge(unittest.TestCase):
    """An existing route written as sink -> sink must still be seen."""

    def test_bare_sink_source_blocks_the_reverse_route(self):
        # audA -> audB already exists, written the bare way.
        loopbacks = [{"source": "audA_sink", "sink": "audB_sink"}]
        chain = PactlRunner.loopback_cycle(loopbacks, "audB_sink.monitor", "audA_sink")
        self.assertIsNotNone(chain, "a real two-node loop was not detected")

    def test_same_graph_spelled_with_monitor_is_still_refused(self):
        loopbacks = [{"source": "audA_sink.monitor", "sink": "audB_sink"}]
        chain = PactlRunner.loopback_cycle(loopbacks, "audB_sink.monitor", "audA_sink")
        self.assertIsNotNone(chain)

    def test_the_two_spellings_agree(self):
        bare = PactlRunner.loopback_cycle(
            [{"source": "audA_sink", "sink": "audB_sink"}],
            "audB_sink.monitor", "audA_sink")
        dotted = PactlRunner.loopback_cycle(
            [{"source": "audA_sink.monitor", "sink": "audB_sink"}],
            "audB_sink.monitor", "audA_sink")
        self.assertEqual(bare, dotted)

    def test_a_dotted_device_name_is_not_mangled(self):
        """alsa_output.pci has a dot but is not a monitor suffix.

        Normalising with split('.')[0] would turn it into 'alsa_output'
        and silently change the graph.
        """
        loopbacks = [{"source": "alsa_output.pci", "sink": "headphones"}]
        # Routing headphones back onto alsa_output.pci closes a real loop.
        chain = PactlRunner.loopback_cycle(loopbacks, "headphones.monitor",
                                           "alsa_output.pci")
        self.assertIsNotNone(chain)
        self.assertIn("alsa_output.pci", chain)


class TestNoFalsePositives(unittest.TestCase):
    """Normalising must not invent cycles that are not there."""

    def test_unrelated_routes_are_still_allowed(self):
        loopbacks = [{"source": "audA_sink", "sink": "audB_sink"}]
        # audC has no path back to audA, so this is fine.
        self.assertIsNone(
            PactlRunner.loopback_cycle(loopbacks, "audC_sink.monitor", "audA_sink"))

    def test_a_self_route_is_a_cycle_even_with_no_existing_routes(self):
        """Routing a sink onto itself is the degenerate cycle.

        Worth pinning because "no existing routes" is not the same as "no
        cycle" -- the proposed edge is itself the loop.
        """
        self.assertEqual(
            PactlRunner.loopback_cycle([], "game.monitor", "game"), ["game", "game"])

    def test_a_three_node_cycle_written_bare_is_detected(self):
        loopbacks = [
            {"source": "a_sink", "sink": "b_sink"},
            {"source": "b_sink", "sink": "c_sink"},
        ]
        chain = PactlRunner.loopback_cycle(loopbacks, "c_sink.monitor", "a_sink")
        self.assertIsNotNone(chain)


class TestChainNamesOnlyRealEdges(unittest.TestCase):
    """The chain is shown to the user as 'the other half of your loop'.

    A chain containing an edge that does not exist names devices the user
    never wired together, which is worse than saying nothing.
    """

    def test_chain_contains_no_invented_edges(self):
        loopbacks = [
            {"source": "game.monitor", "sink": "voice"},
            {"source": "game.monitor", "sink": "chat"},
            {"source": "voice.monitor", "sink": "music"},
            {"source": "chat.monitor", "sink": "music"},
        ]
        chain = PactlRunner.loopback_cycle(loopbacks, "music.monitor", "game")
        self.assertIsNotNone(chain)

        # slice, not str.removesuffix: that is 3.9+ and CI runs 3.8
        def _origin(s):
            return s[:-len(".monitor")] if s.endswith(".monitor") else s

        real = {(_origin(e["source"]), e["sink"]) for e in loopbacks}
        # The chain is [source, target, ...path back..., ]. Its FIRST pair is
        # the route being proposed, which by definition does not exist yet.
        # Every other pair must be a route already in place.
        proposed = (chain[0], chain[1])
        for a, b in zip(chain, chain[1:]):
            if (a, b) == proposed:
                continue
            self.assertIn((a, b), real,
                          f"chain reports a nonexistent edge {a} -> {b}")


if __name__ == "__main__":
    unittest.main()
