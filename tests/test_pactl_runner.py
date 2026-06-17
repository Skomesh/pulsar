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


if __name__ == "__main__":
    unittest.main()
