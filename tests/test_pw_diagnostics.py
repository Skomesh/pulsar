"""
Unit tests for the Phase 7 diagnostics backend.

Tests cover the snapshot parser (with mocked pw-dump output) and the
rate audit + health summary helpers. A live integration test verifies
that get_graph_snapshot() works against a real PipeWire daemon.
"""

import unittest
from unittest import mock

from utils.pw_diagnostics import (
    PwDumpError,
    _extract_rate,
    get_graph_snapshot,
    health_summary,
    sample_rate_audit,
)


def pactl_or_pw_dump_available() -> bool:
    import shutil
    return shutil.which("pw-dump") is not None


# ----------------------------------------------------------------------
# _extract_rate — the EnumFormat parser
# ----------------------------------------------------------------------


class TestExtractRate(unittest.TestCase):
    def test_simple_int_rate(self):
        info = {"params": {"EnumFormat": [{"rate": 48000}]}}
        self.assertEqual(_extract_rate(info), 48000)

    def test_dict_rate_with_default(self):
        info = {"params": {"EnumFormat": [{"rate": {
            "default": 48000, "min": 1, "max": 192000
        }}]}}
        self.assertEqual(_extract_rate(info), 48000)

    def test_no_enum_format_returns_none(self):
        self.assertIsNone(_extract_rate({}))
        self.assertIsNone(_extract_rate({"params": {}}))
        self.assertIsNone(_extract_rate({"params": {"EnumFormat": []}}))

    def test_missing_rate_field(self):
        info = {"params": {"EnumFormat": [{"channels": 2}]}}
        self.assertIsNone(_extract_rate(info))


# ----------------------------------------------------------------------
# get_graph_snapshot — with mocked pw-dump output
# ----------------------------------------------------------------------


# A representative pw-dump response — covers all object types we parse
MOCK_PW_DUMP = [
    {"id": 0, "type": "PipeWire:Interface:Core",
     "info": {"name": "pipewire-0", "version": "1.6.2",
              "cookie": 12345, "user-name": "alice", "host-name": "host",
              "props": {"default.clock.rate": 48000,
                        "default.clock.quantum": 1024}}},
    {"id": 1, "type": "PipeWire:Interface:Module",
     "info": {"name": "libpipewire-module-rt",
              "filename": "/usr/lib/.../libpipewire-module-rt.so",
              "args": "rt.prio = 88",
              "props": {}}},
    {"id": 2, "type": "PipeWire:Interface:Client",
     "info": {"name": "test-client", "props": {
         "application.name": "TestApp",
         "application.process.binary": "test-app",
         "application.process.id": "9999"}}},
    {"id": 3, "type": "PipeWire:Interface:Node",
     "info": {"state": "running",
              "props": {"media.class": "Audio/Sink",
                        "node.name": "test-sink",
                        "client.id": "2",
                        "application.name": "TestApp"}}},
    {"id": 4, "type": "PipeWire:Interface:Node",
     "info": {"state": "idle",
              "props": {"media.class": "Audio/Source",
                        "node.name": "test-source",
                        "client.id": "2"}}},
    {"id": 5, "type": "PipeWire:Interface:Device",
     "info": {"props": {"media.class": "Audio/Device",
                        "device.name": "test-card",
                        "device.description": "Test Card",
                        "device.nick": "TC"}}},
    {"id": 6, "type": "PipeWire:Interface:Port",
     "info": {"direction": "in",
              "props": {"port.name": "test-port-in",
                        "node.id": 3,
                        "audio.channel": "FL"}}},
    {"id": 7, "type": "PipeWire:Interface:Port",
     "info": {"direction": "out",
              "props": {"port.name": "test-port-out",
                        "node.id": 3,
                        "audio.channel": "FR"}}},
    {"id": 8, "type": "PipeWire:Interface:Link",
     "info": {"output-node-id": 4, "output-port-id": 7,
              "input-node-id": 3, "input-port-id": 6,
              "state": "active", "active": True, "error": None,
              "props": {}}},
]


class TestGraphSnapshotMocked(unittest.TestCase):
    @mock.patch("utils.pw_diagnostics._run_pw_dump")
    def test_parses_all_object_types(self, mock_run):
        mock_run.return_value = MOCK_PW_DUMP
        snap = get_graph_snapshot()

        # Core
        self.assertEqual(snap["core"]["version"], "1.6.2")
        self.assertEqual(snap["core"]["clock_rate"], 48000)
        # Clients
        self.assertEqual(len(snap["clients"]), 1)
        self.assertEqual(snap["clients"][0]["app_name"], "TestApp")
        self.assertEqual(snap["clients"][0]["pid"], 9999)
        # Modules
        self.assertEqual(len(snap["modules"]), 1)
        self.assertEqual(snap["modules"][0]["name"],
                         "libpipewire-module-rt")
        self.assertEqual(snap["modules"][0]["args"], "rt.prio = 88")
        # Nodes
        self.assertEqual(len(snap["nodes"]), 2)
        node_by_name = {n["name"]: n for n in snap["nodes"]}
        self.assertEqual(node_by_name["test-sink"]["media_class"],
                         "Audio/Sink")
        self.assertEqual(node_by_name["test-source"]["state"], "idle")
        # Devices
        self.assertEqual(len(snap["devices"]), 1)
        self.assertEqual(snap["devices"][0]["description"], "Test Card")
        # Ports
        self.assertEqual(len(snap["ports"]), 2)
        self.assertEqual(snap["ports"][0]["node_id"], 3)
        # Links
        self.assertEqual(len(snap["links"]), 1)
        link = snap["links"][0]
        self.assertEqual(link["output_node"], 4)
        self.assertEqual(link["input_node"], 3)
        self.assertEqual(link["state"], "active")

    @mock.patch("utils.pw_diagnostics._run_pw_dump")
    def test_handles_non_list_response(self, mock_run):
        mock_run.return_value = {"unexpected": "shape"}
        with self.assertRaises(PwDumpError):
            get_graph_snapshot()


# ----------------------------------------------------------------------
# sample_rate_audit
# ----------------------------------------------------------------------


class TestSampleRateAudit(unittest.TestCase):
    def test_no_audio_nodes_is_consistent(self):
        snap = {"nodes": []}
        audit = sample_rate_audit(snap)
        self.assertTrue(audit["is_consistent"])
        self.assertEqual(audit["recommended_rate"], None)
        self.assertEqual(audit["audio_node_count"], 0)

    def test_all_same_rate_consistent(self):
        snap = {"nodes": [
            {"name": "a", "media_class": "Audio/Sink", "rate": 48000},
            {"name": "b", "media_class": "Audio/Source", "rate": 48000},
        ]}
        audit = sample_rate_audit(snap)
        self.assertTrue(audit["is_consistent"])
        self.assertEqual(audit["recommended_rate"], 48000)
        self.assertEqual(audit["audio_node_count"], 2)
        self.assertEqual(audit["mismatched_nodes"], [])

    def test_mixed_rates_flags_mismatch(self):
        snap = {"nodes": [
            {"name": "a", "media_class": "Audio/Sink", "rate": 48000},
            {"name": "b", "media_class": "Audio/Source", "rate": 44100},
            {"name": "c", "media_class": "Audio/Sink", "rate": 48000},
            {"name": "d", "media_class": "Audio/Source", "rate": 96000},
        ]}
        audit = sample_rate_audit(snap)
        self.assertFalse(audit["is_consistent"])
        self.assertEqual(audit["recommended_rate"], 48000)
        self.assertEqual(audit["audio_node_count"], 4)
        # Recommended rate is most common (48000, 2 nodes). Others are flagged.
        self.assertEqual(len(audit["mismatched_nodes"]), 2)
        self.assertTrue(any("44100" in n for n in audit["mismatched_nodes"]))
        self.assertTrue(any("96000" in n for n in audit["mismatched_nodes"]))

    def test_non_audio_nodes_ignored(self):
        snap = {"nodes": [
            {"name": "a", "media_class": "Audio/Sink", "rate": 48000},
            {"name": "video", "media_class": "Video/Source", "rate": None},
            {"name": "midi", "media_class": "Midi/Source", "rate": None},
        ]}
        audit = sample_rate_audit(snap)
        self.assertEqual(audit["audio_node_count"], 1)


# ----------------------------------------------------------------------
# health_summary
# ----------------------------------------------------------------------


class TestHealthSummary(unittest.TestCase):
    def test_empty_graph_is_healthy(self):
        snap = {"nodes": [], "links": [], "clients": [], "modules": [],
                "devices": [], "ports": []}
        health = health_summary(snap)
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["warnings"], [])
        self.assertEqual(health["errors"], [])

    def test_error_link_flags_error_status(self):
        snap = {"nodes": [], "links": [{"id": 1, "state": "error"}],
                "clients": [], "modules": [], "devices": [], "ports": []}
        health = health_summary(snap)
        self.assertEqual(health["status"], "errors")
        self.assertEqual(len(health["errors"]), 1)
        self.assertIn("Link #1", health["errors"][0])

    def test_rate_mismatch_flags_warning(self):
        snap = {"nodes": [
            {"name": "a", "media_class": "Audio/Sink", "rate": 48000},
            {"name": "b", "media_class": "Audio/Source", "rate": 44100},
        ], "links": [], "clients": [], "modules": [], "devices": [],
                "ports": []}
        health = health_summary(snap)
        self.assertEqual(health["status"], "warnings")
        self.assertEqual(len(health["warnings"]), 1)
        self.assertIn("Sample rate mismatch", health["warnings"][0])


# ----------------------------------------------------------------------
# Live integration test
# ----------------------------------------------------------------------


@unittest.skipUnless(
    pactl_or_pw_dump_available(), "pw-dump not available"
)
class TestGraphSnapshotLive(unittest.TestCase):
    """Smoke test against a real PipeWire daemon."""

    def test_snapshot_returns_expected_categories(self):
        snap = get_graph_snapshot()
        self.assertIsInstance(snap, dict)
        # All standard keys are present (even if empty)
        for key in ("core", "clients", "modules", "nodes", "devices",
                    "links", "ports", "raw_count"):
            self.assertIn(key, snap)
        # Core has version
        self.assertNotEqual(snap["core"].get("version"), "?")
        # On a real system there should be at least one client (the
        # daemon itself counts as one)
        self.assertGreater(len(snap["clients"]), 0)
        # raw_count matches the sum of parsed categories (some
        # duplicates may exist due to metadata/security-context objects
        # we don't parse, so allow >= for raw_count)
        total_parsed = (
            len(snap["clients"]) + len(snap["modules"])
            + len(snap["nodes"]) + len(snap["devices"])
            + len(snap["links"]) + len(snap["ports"])
        )
        self.assertGreaterEqual(snap["raw_count"], total_parsed - 1)

    def test_snapshot_is_idempotent(self):
        """Two snapshots should have the same general shape."""
        s1 = get_graph_snapshot()
        s2 = get_graph_snapshot()
        self.assertEqual(s1["core"]["version"], s2["core"]["version"])
        # Counts should be in the same ballpark (allow some variation
        # since audio streams come and go)
        self.assertLess(
            abs(len(s1["nodes"]) - len(s2["nodes"])), 10
        )


if __name__ == "__main__":
    unittest.main()
