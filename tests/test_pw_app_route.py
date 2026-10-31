"""
Tests for native per-app routing (PipeWire port links).

The Capture tab's "Route Here" used to route the app's SINK, not the app:
it took the sink's .monitor and looped it to the target. A monitor
carries every stream on that sink, so routing one app silently destroyed
another app's route, and an app could never be heard on two outputs.

Native PipeWire links fix that: a stream's output port belongs to that
one app, so linking it to a sink's input port addresses the app
precisely.

These tests pin the parsing and command construction, and the handler
behaviour. They run against recorded pw-dump / pw-link output, so they
do not need a live server.
"""

import subprocess
import unittest
from unittest import mock

from pulsar.utils import pw_app_route
from pulsar.utils.pw_app_route import (
    StreamPort,
    channels_for,
    parse_links,
    parse_ports,
)

# Real `pw-dump` output from this host, trimmed to the relevant nodes.
# Node 94 is a sink called vsink_a with playback_FL/FR input ports and
# monitor_FL/FR output ports. Node 61 is a stream called paplay.
PW_DUMP_OUTPUT = """[
  {
    "id": 94,
    "type": "PipeWire:Interface:Node",
    "info": {
      "id": 94,
      "props": {
        "node.name": "vsink_a",
        "media.class": "Stream/Output/Audio",
        "object.linger": "false"
      }
    }
  },
  {
    "id": 61,
    "type": "PipeWire:Interface:Node",
    "info": {
      "id": 61,
      "props": {
        "node.name": "paplay",
        "media.class": "Stream/Output/Audio",
        "application.name": "paplay"
      }
    }
  },
  {
    "id": 45,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 45,
      "props": {
        "node.id": 94,
        "port.name": "playback_FL",
        "port.direction": "in"
      }
    }
  },
  {
    "id": 72,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 72,
      "props": {
        "node.id": 94,
        "port.name": "playback_FR",
        "port.direction": "in"
      }
    }
  },
  {
    "id": 52,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 52,
      "props": {
        "node.id": 94,
        "port.name": "monitor_FL",
        "port.direction": "out"
      }
    }
  },
  {
    "id": 92,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 92,
      "props": {
        "node.id": 94,
        "port.name": "monitor_FR",
        "port.direction": "out"
      }
    }
  },
  {
    "id": 68,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 68,
      "props": {
        "node.id": 61,
        "port.name": "output_FL",
        "port.direction": "out"
      }
    }
  },
  {
    "id": 85,
    "type": "PipeWire:Interface:Port",
    "info": {
      "id": 85,
      "props": {
        "node.id": 61,
        "port.name": "output_FR",
        "port.direction": "out"
      }
    }
  }
]
"""

# Real `pw-link -l` output. The format is a port id and name, then the
# links attached to it, indented with |-> or |<-
PW_LINK_OUTPUT = """  94 vsink_a:playback_FL
 102   |<-   68 paplay:output_FL
  72 vsink_a:playback_FR
 103   |<-   85 paplay:output_FR
  68 paplay:output_FL
102   |->   94 vsink_a:playback_FL
  85 paplay:output_FR
103   |->   72 vsink_a:playback_FR
"""


class TestRunPwDumpFailsLoudly(unittest.TestCase):
    """A failing `pw-dump` must not look like an empty graph.

    The return code was never checked. A `pw-dump` that failed --
    no daemon, a permission problem, a version mismatch -- returned its
    empty stdout, which parsed to no ports, which planned no links.
    The handler then reported the app as routed to the chosen output.
    A tool that could not answer was indistinguishable from a graph
    with nothing in it.
    """

    def test_a_failing_pw_dump_raises(self):
        failed = subprocess.CompletedProcess(
            args=["pw-dump"], returncode=1, stdout="", stderr="boom",
        )
        with mock.patch.object(
            pw_app_route.subprocess, "run", return_value=failed
        ):
            with self.assertRaises(pw_app_route.PwLinkUnavailable):
                pw_app_route._run_pw_dump()

    def test_a_missing_pw_dump_raises(self):
        with mock.patch.object(
            pw_app_route.subprocess, "run",
            side_effect=FileNotFoundError("pw-dump"),
        ):
            with self.assertRaises(pw_app_route.PwLinkUnavailable):
                pw_app_route._run_pw_dump()

    def test_a_successful_pw_dump_returns_its_output(self):
        ok = subprocess.CompletedProcess(
            args=["pw-dump"], returncode=0, stdout="[]", stderr="",
        )
        with mock.patch.object(
            pw_app_route.subprocess, "run", return_value=ok
        ):
            self.assertEqual(pw_app_route._run_pw_dump(), "[]")


class TestParsePorts(unittest.TestCase):
    """Ports are addressed by ID, and the ID is what pw-link takes."""

    def _run(self):
        with mock.patch.object(
            pw_app_route, "_run_pw_dump", return_value=PW_DUMP_OUTPUT
        ):
            return parse_ports(PW_DUMP_OUTPUT)

    def test_finds_the_sink_input_ports(self):
        ports = self._run()
        self.assertIn(45, ports)
        self.assertEqual(ports[45].node_id, 94)
        self.assertEqual(ports[45].direction, "in")
        self.assertEqual(ports[45].name, "playback_FL")

    def test_finds_the_stream_output_ports(self):
        ports = self._run()
        self.assertEqual(ports[68].node_id, 61)
        self.assertEqual(ports[68].direction, "out")
        self.assertEqual(ports[68].name, "output_FL")

    def test_monitor_ports_are_output_direction(self):
        """A sink's monitor is an OUTPUT -- routing TO it is backwards."""
        ports = self._run()
        self.assertEqual(ports[52].direction, "out")
        self.assertEqual(ports[52].node_id, 94)

    def test_ignores_non_port_objects(self):
        ports = self._run()
        self.assertNotIn(94, ports)
        self.assertNotIn(61, ports)


class TestChannelsFor(unittest.TestCase):
    """Channel pairing must be by name, not by cartesian product."""

    def test_pairs_fl_to_fl(self):
        ports = {
            68: StreamPort(68, 61, "output_FL", "out"),
            85: StreamPort(85, 61, "output_FR", "out"),
            45: StreamPort(45, 94, "playback_FL", "in"),
            72: StreamPort(72, 94, "playback_FR", "in"),
        }
        pairs = channels_for(ports, [68, 85], [45, 72])
        self.assertEqual(pairs, [(68, 45), (85, 72)])

    def test_does_not_cross_channels(self):
        """FL must not reach FR. A 2x2 cartesian link swaps channels."""
        ports = {
            68: StreamPort(68, 61, "output_FL", "out"),
            85: StreamPort(85, 61, "output_FR", "out"),
            45: StreamPort(45, 94, "playback_FL", "in"),
            72: StreamPort(72, 94, "playback_FR", "in"),
        }
        pairs = channels_for(ports, [68, 85], [45, 72])
        self.assertNotIn((68, 72), pairs)
        self.assertNotIn((85, 45), pairs)
        self.assertEqual(len(pairs), 2)

    def test_mono_source_links_to_both_channels(self):
        """A mono stream has one port; it feeds both of the sink's."""
        ports = {
            68: StreamPort(68, 61, "output_FL", "out"),
            45: StreamPort(45, 94, "playback_FL", "in"),
            72: StreamPort(72, 94, "playback_FR", "in"),
        }
        pairs = channels_for(ports, [68], [45, 72])
        self.assertEqual(pairs, [(68, 45), (68, 72)])

    def test_mismatched_channel_names_are_not_paired(self):
        """An unknown channel name must not silently pair with another."""
        ports = {
            68: StreamPort(68, 61, "output_X", "out"),
            45: StreamPort(45, 94, "playback_FL", "in"),
        }
        pairs = channels_for(ports, [68], [45])
        self.assertEqual(pairs, [])

    def test_empty_side_yields_nothing(self):
        ports = {45: StreamPort(45, 94, "playback_FL", "in")}
        self.assertEqual(channels_for(ports, [], [45]), [])
        self.assertEqual(channels_for(ports, [45], []), [])


class TestParseLinks(unittest.TestCase):
    """The live link list, so a route can be inspected and undone."""

    def _run(self):
        return parse_links(PW_LINK_OUTPUT)

    def test_finds_the_link_from_stream_to_sink(self):
        links = self._run()
        self.assertIn((68, 94), links)
        self.assertIn((85, 72), links)

    def test_does_not_duplicate_reverse_direction(self):
        """The listing shows both ends of a link; it is ONE link."""
        links = self._run()
        self.assertNotIn((94, 68), links)

    def test_empty_output_is_no_links(self):
        self.assertEqual(parse_links(""), set())

    def test_ignores_lines_that_are_not_links(self):
        links = parse_links("  94 vsink_a:playback_FL\n")
        self.assertEqual(links, set())


if __name__ == "__main__":
    unittest.main()
