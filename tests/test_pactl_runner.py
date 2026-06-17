"""
Unit tests for the PactlRunner wrapper.

These tests call real pactl commands and require a working PulseAudio or
PipeWire setup. They clean up after themselves by unloading any null-sink
modules they create. Tests are skipped if pactl is not available.

Run with: make test  (or: python3 -m pytest tests/)
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

# Make src/ importable when running from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils.pactl_runner import PactlRunner  # noqa: E402


def pactl_available():
    return shutil.which("pactl") is not None


def get_pw_node(name_substring):
    """Return pw-dump node dicts whose node.name contains the substring."""
    result = subprocess.run(
        ["pw-dump"], capture_output=True, text=True, check=True
    )
    nodes = json.loads(result.stdout)
    return [
        n
        for n in nodes
        if name_substring in n.get("info", {}).get("props", {}).get("node.name", "")
    ]


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestCreateNullSink(unittest.TestCase):
    """End-to-end tests for create_sink_only / create_source_only / create_duplex_sink."""

    @classmethod
    def setUpClass(cls):
        # Make sure no leftover test sinks from a previous failed run
        PactlRunner.unload_all_null_sinks()

    def tearDown(self):
        PactlRunner.unload_all_null_sinks()

    def test_create_sink_only(self):
        ok = PactlRunner.create_sink_only(
            "test_sink_only", "TestSinkOnly", channels=2
        )
        self.assertTrue(ok)
        nodes = get_pw_node("test_sink_only")
        self.assertEqual(len(nodes), 1, "expected exactly one PW node")
        self.assertEqual(
            nodes[0]["info"]["props"]["media.class"],
            "Audio/Sink",
            "sink-only should have media.class=Audio/Sink",
        )

    def test_create_source_only(self):
        ok = PactlRunner.create_source_only(
            "test_source_only", "TestSourceOnly", channels=2
        )
        self.assertTrue(ok)
        nodes = get_pw_node("test_source_only")
        self.assertEqual(len(nodes), 1, "expected exactly one PW node")
        self.assertEqual(
            nodes[0]["info"]["props"]["media.class"],
            "Audio/Source",
            "source-only should have media.class=Audio/Source",
        )

    def test_create_duplex_sink_regression(self):
        """The original create_duplex_sink must still produce Audio/Duplex."""
        ok = PactlRunner.create_duplex_sink(
            "test_duplex", "TestDuplex", channels=2
        )
        self.assertTrue(ok)
        nodes = get_pw_node("test_duplex")
        self.assertEqual(len(nodes), 1, "expected exactly one PW node")
        self.assertEqual(
            nodes[0]["info"]["props"]["media.class"],
            "Audio/Duplex",
            "duplex should still produce media.class=Audio/Duplex",
        )

    def test_three_types_differ_only_in_media_class(self):
        """All three helpers share parameter handling except for media.class."""
        PactlRunner.create_sink_only("t_sink", "D", channels=2)
        nodes_sink = get_pw_node("t_sink")
        PactlRunner.create_source_only("t_source", "D", channels=2)
        nodes_source = get_pw_node("t_source")
        PactlRunner.create_duplex_sink("t_duplex", "D", channels=2)
        nodes_duplex = get_pw_node("t_duplex")

        # Same channel count / sample spec on all three (modulo id differences)
        for nodes, expected_class in [
            (nodes_sink, "Audio/Sink"),
            (nodes_source, "Audio/Source"),
            (nodes_duplex, "Audio/Duplex"),
        ]:
            self.assertEqual(len(nodes), 1)
            self.assertEqual(
                nodes[0]["info"]["props"]["media.class"], expected_class
            )
            self.assertEqual(
                nodes[0]["info"]["props"]["audio.channels"], 2
            )

    def test_advanced_options_propagate(self):
        """rate, channel_map, sink_properties should reach the command."""
        ok = PactlRunner.create_sink_only(
            "t_advanced",
            "Advanced",
            channels=2,
            rate=48000,
            channel_map="front-left,front-right",
            sink_properties="device.description=MyCustomSink",
        )
        self.assertTrue(ok)
        nodes = get_pw_node("t_advanced")
        self.assertEqual(len(nodes), 1)
        props = nodes[0]["info"]["props"]
        self.assertEqual(props["audio.rate"], 48000)
        # PipeWire normalizes the channel map format internally; we just verify
        # the option was accepted (no error) and a 2-channel position is set.
        self.assertEqual(len(props["audio.position"].split(",")), 2)
        # sink_properties=device.description=X is stored as node.description
        self.assertEqual(props["node.description"], "MyCustomSink")


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestLoopback(unittest.TestCase):
    """End-to-end tests for module-loopback creation and routing helpers."""

    @classmethod
    def setUpClass(cls):
        PactlRunner.unload_all_null_sinks()

    def tearDown(self):
        # Unload any leftover loopbacks, then any leftover null-sinks
        for lb in PactlRunner.list_loopbacks():
            PactlRunner.unload_loopback(lb["id"])
        PactlRunner.unload_all_null_sinks()

    def _pick_hardware_output(self):
        """Pick any non-null-sink output for routing tests. Skip if none."""
        outputs = PactlRunner.list_hardware_outputs()
        if not outputs:
            self.skipTest("no hardware output sinks available for routing test")
        return outputs[0]

    def test_monitor_source_for(self):
        self.assertEqual(
            PactlRunner.monitor_source_for("my_sink"), "my_sink.monitor"
        )
        self.assertIsNone(PactlRunner.monitor_source_for(""))

    def test_is_null_sink(self):
        PactlRunner.create_sink_only("ns_check", "NSCheck", channels=2)
        self.assertTrue(PactlRunner.is_null_sink("ns_check"))
        hw = self._pick_hardware_output()
        self.assertFalse(PactlRunner.is_null_sink(hw))

    def test_create_loopback_returns_module_id(self):
        PactlRunner.create_sink_only("lb_create", "LBCreate", channels=2)
        target = self._pick_hardware_output()
        monitor = PactlRunner.monitor_source_for("lb_create")

        lb_id = PactlRunner.create_loopback(monitor, target, latency_msec=1)
        self.assertIsNotNone(lb_id)
        self.assertTrue(lb_id.isdigit(), f"module ID should be numeric, got {lb_id!r}")

    def test_list_loopbacks_finds_what_we_created(self):
        PactlRunner.create_sink_only("lb_list", "LBList", channels=2)
        target = self._pick_hardware_output()
        monitor = PactlRunner.monitor_source_for("lb_list")

        lb_id = PactlRunner.create_loopback(monitor, target, latency_msec=1)
        loopbacks = PactlRunner.list_loopbacks()

        ours = [lb for lb in loopbacks if lb["id"] == lb_id]
        self.assertEqual(len(ours), 1)
        self.assertEqual(ours[0]["source"], monitor)
        self.assertEqual(ours[0]["sink"], target)

    def test_unload_loopback_removes_it(self):
        PactlRunner.create_sink_only("lb_unload", "LBUnload", channels=2)
        target = self._pick_hardware_output()
        monitor = PactlRunner.monitor_source_for("lb_unload")

        lb_id = PactlRunner.create_loopback(monitor, target, latency_msec=1)
        self.assertTrue(PactlRunner.unload_loopback(lb_id))
        remaining = [
            lb for lb in PactlRunner.list_loopbacks() if lb["id"] == lb_id
        ]
        self.assertEqual(remaining, [])

    def test_create_loopback_with_unknown_sink_still_loads(self):
        """PipeWire tolerates a non-existent sink: the loopback module loads but
        has no effect. We assert behavior, not validation — the module is loaded
        successfully (no error) even with a bogus sink name. This documents that
        the UI must verify sink existence separately before calling create_loopback.
        """
        PactlRunner.create_sink_only("lb_bad", "LBBad", channels=2)
        monitor = PactlRunner.monitor_source_for("lb_bad")
        result = PactlRunner.create_loopback(
            monitor, "this_sink_does_not_exist_12345", latency_msec=1
        )
        # Module loads — but it won't actually route anywhere
        self.assertIsNotNone(result)
        # And we can clean it up
        self.assertTrue(PactlRunner.unload_loopback(result))

    def test_list_hardware_outputs_excludes_null_sinks(self):
        PactlRunner.create_sink_only("ns_exclude", "NSExclude", channels=2)
        outputs = PactlRunner.list_hardware_outputs()
        self.assertNotIn("ns_exclude", outputs)
        # Every returned name should NOT be a null-sink
        for name in outputs:
            self.assertFalse(PactlRunner.is_null_sink(name))

    def test_multi_output_loopback_two_targets_from_same_source(self):
        """A single virtual sink can be routed to multiple hardware outputs
        via multiple loopbacks from the same monitor source. This is the
        Phase 5b 'multi-output routing' capability — used for the
        'music plays on speakers AND headphones simultaneously' use case.
        """
        # Need at least 2 hardware outputs for this test to be meaningful
        outputs = PactlRunner.list_hardware_outputs()
        if len(outputs) < 2:
            self.skipTest("need at least 2 hardware outputs for this test")

        PactlRunner.create_sink_only("multi_out", "Multi Out", channels=2)
        monitor = PactlRunner.monitor_source_for("multi_out")

        # Route to first 2 outputs
        lb1_id = PactlRunner.create_loopback(monitor, outputs[0])
        lb2_id = PactlRunner.create_loopback(monitor, outputs[1])
        self.assertIsNotNone(lb1_id)
        self.assertIsNotNone(lb2_id)
        self.assertNotEqual(lb1_id, lb2_id)

        # Verify both are present
        loopbacks = PactlRunner.list_loopbacks()
        from_monitor = [
            lb for lb in loopbacks if lb.get("source") == monitor
        ]
        self.assertEqual(len(from_monitor), 2)
        targets = {lb.get("sink") for lb in from_monitor}
        self.assertEqual(targets, {outputs[0], outputs[1]})

        # Remove one loopback, the other should remain
        self.assertTrue(PactlRunner.unload_loopback(lb1_id))
        from_monitor = [
            lb for lb in PactlRunner.list_loopbacks() if lb.get("source") == monitor
        ]
        self.assertEqual(len(from_monitor), 1)
        self.assertEqual(from_monitor[0].get("sink"), outputs[1])

        # Cleanup the remaining loopback
        PactlRunner.unload_loopback(lb2_id)


if __name__ == "__main__":
    unittest.main()
