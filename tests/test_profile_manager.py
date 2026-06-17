"""
Unit tests for the ProfileManager.

End-to-end tests that exercise capture, save, load, and apply on the running
PipeWire/PulseAudio system. They create real null-sinks and loopbacks and
clean up after themselves. Tests are skipped on systems without pactl.

See docs/PHASE3_INTROSPECTION_REPORT.md for the underlying research that
motivated this design.
"""

import shutil
import tempfile
import unittest

from utils.pactl_runner import PactlRunner
from utils.profile_manager import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_SINK_SENTINEL,
    ProfileError,
    ProfileManager,
)


def pactl_available() -> bool:
    """Return True if pactl is on PATH (tests can run end-to-end)."""
    import shutil as _shutil
    return _shutil.which("pactl") is not None


def _wipe_topology():
    """Unload all null-sinks and loopbacks — for a clean test state."""
    for lb in PactlRunner.list_loopbacks():
        PactlRunner.unload_loopback(lb["id"])
    PactlRunner.unload_all_null_sinks()


def _sink_names():
    """Names of all null-sinks currently in the system."""
    return {s["name"] for s in PactlRunner.list_sinks() if PactlRunner.is_null_sink(s["name"])}


def _source_names():
    """Names of all null-sink-derived sources currently in the system."""
    return {s["name"] for s in PactlRunner.list_sources() if PactlRunner.is_null_sink(s["name"])}


class _ProfileTestBase(unittest.TestCase):
    """Common setup: temp dir for profiles file, clean topology."""

    @classmethod
    def setUpClass(cls):
        if not pactl_available():
            raise unittest.SkipTest("pactl not available")
        cls.tmpdir = tempfile.mkdtemp(prefix="pulsar_test_")
        cls.pm = ProfileManager(presets_dir=cls.tmpdir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        _wipe_topology()


class TestMigration(unittest.TestCase):
    """Schema migration from v1 (channel presets) to v2 (profiles)."""

    def test_v1_entry_migrates_to_empty_v2(self):
        v1 = {
            "channels": "2",
            "channel_map": "front-left,front-right",
            "description": "Stereo",
            "builtin": True,
        }
        out = ProfileManager.migrate_entry("Stereo", v1)
        self.assertEqual(out["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(out["devices"], [])
        self.assertEqual(out["routing"], [])
        self.assertIn("Migrated from v1", out["description"])

    def test_v2_entry_passes_through(self):
        v2 = {
            "schema_version": 2,
            "devices": [{"name": "x", "type": "sink", "channels": 2}],
            "routing": [{"from": "x", "to": "hw"}],
        }
        out = ProfileManager.migrate_entry("Modern", v2)
        self.assertEqual(out["schema_version"], 2)
        self.assertEqual(len(out["devices"]), 1)
        self.assertEqual(out["devices"][0]["name"], "x")

    def test_v2_entry_without_version_gets_current(self):
        """A v2-shaped dict missing the version field should still be tagged v2."""
        v2_shaped = {
            "devices": [],
            "routing": [],
        }
        out = ProfileManager.migrate_entry("X", v2_shaped)
        self.assertEqual(out["schema_version"], CURRENT_SCHEMA_VERSION)

    def test_load_all_profiles_migrates_v1_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProfileManager(presets_dir=tmp)
            # Write a v1 file with user profile names
            pm._save_raw({
                "Stereo": {"channels": "2", "builtin": True},
                "MyCustom": {"channels": "6", "builtin": False},
            })
            # load_all_profiles now merges in built-ins too — we should
            # see 2 (user) + 3 (built-ins) = 5 total.
            loaded = pm.load_all_profiles()
            self.assertEqual(len(loaded), 5)
            # The user v1 entries were migrated to v2
            self.assertEqual(loaded["Stereo"]["schema_version"], 2)
            self.assertEqual(loaded["MyCustom"]["schema_version"], 2)
            self.assertEqual(loaded["Stereo"]["devices"], [])
            # The built-ins are present (3 generic profiles now)
            self.assertIn("Headphones (single output)", loaded)
            self.assertIn("Per-application routing (4 channels)", loaded)
            self.assertIn("Stereo split (apps + voice)", loaded)
            self.assertEqual(pm.is_shadowed_by_user("Gaming"), False)
            # Save user "Gaming" and verify shadow works
            pm.save_profile("Gaming", {"devices": [], "routing": []})
            self.assertEqual(pm.is_shadowed_by_user("Gaming"), True)
            loaded = pm.load_all_profiles()
            # Shadowed Gaming is now the user's (empty) version
            self.assertEqual(loaded["Gaming"]["devices"], [])
            # Other built-ins still load (now generic names)
            self.assertIn("Headphones (single output)", loaded)
            self.assertIn("Per-application routing (4 channels)", loaded)
            self.assertIn("Stereo split (apps + voice)", loaded)

class TestDefaultSinkSentinel(unittest.TestCase):
    """Validation and substitution of the <AUTO_DEFAULT> sentinel.

    These tests don't touch pactl — they exercise _validate_profile
    (pure-Python) and use unittest.mock to simulate the apply-time
    behavior of PactlRunner.get_default_sink / sink_exists / create_*.
    """

    def test_sentinel_passes_validation(self):
        """A routing entry with the sentinel must validate cleanly."""
        profile = {
            "schema_version": 2,
            "devices": [{"name": "d", "type": "sink", "channels": 2}],
            "routing": [{"from": "d", "to": DEFAULT_SINK_SENTINEL}],
        }
        errors, warnings = ProfileManager._validate_profile(profile)
        self.assertEqual(errors, [])

    def test_non_sentinel_garbage_rejected_by_validation(self):
        """A non-shell-safe routing target should still be rejected."""
        profile = {
            "schema_version": 2,
            "devices": [{"name": "d", "type": "sink", "channels": 2}],
            "routing": [{"from": "d", "to": "has spaces in name"}],
        }
        errors, _ = ProfileManager._validate_profile(profile)
        self.assertTrue(any("routing" in e for e in errors))


class TestSaveAndLoad(_ProfileTestBase):
    """save_profile / get_profile / delete_profile roundtrip."""

    def test_save_and_get(self):
        profile = {
            "schema_version": 2,
            "description": "Test",
            "devices": [],
            "routing": [],
        }
        self.assertTrue(self.pm.save_profile("test1", profile))
        loaded = self.pm.get_profile("test1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["description"], "Test")
        self.assertEqual(loaded["schema_version"], 2)

    def test_save_rejects_invalid_name(self):
        with self.assertRaises(ProfileError):
            self.pm.save_profile("has spaces", {"devices": []})
        with self.assertRaises(ProfileError):
            self.pm.save_profile("has/slash", {"devices": []})

    def test_delete(self):
        self.pm.save_profile("toremove", {"devices": [], "routing": []})
        self.assertIn("toremove", self.pm.get_profile_names())
        self.assertTrue(self.pm.delete_profile("toremove"))
        self.assertNotIn("toremove", self.pm.get_profile_names())

    def test_delete_nonexistent_returns_false(self):
        self.assertFalse(self.pm.delete_profile("doesnotexist"))

    def test_get_profile_names(self):
        self.pm.save_profile("a", {"devices": [], "routing": []})
        self.pm.save_profile("b", {"devices": [], "routing": []})
        names = self.pm.get_profile_names()
        self.assertIn("a", names)
        self.assertIn("b", names)


class TestCaptureTopology(_ProfileTestBase):
    """capture_topology walks pactl list modules and builds a profile."""

    def test_captures_sink_only(self):
        PactlRunner.create_sink_only("cap_sink", "CaptureSink", channels=2)
        profile = self.pm.capture_topology("p1", description="test")
        names = [d["name"] for d in profile["devices"]]
        self.assertIn("cap_sink", names)
        device = next(d for d in profile["devices"] if d["name"] == "cap_sink")
        self.assertEqual(device["type"], "sink")
        self.assertEqual(device["channels"], 2)

    def test_captures_source_only(self):
        PactlRunner.create_source_only("cap_src", "CaptureSource", channels=2)
        profile = self.pm.capture_topology("p2", description="test")
        device = next(d for d in profile["devices"] if d["name"] == "cap_src")
        self.assertEqual(device["type"], "source")

    def test_captures_duplex(self):
        PactlRunner.create_duplex_sink("cap_dup", "CaptureDup", channels=2)
        profile = self.pm.capture_topology("p3", description="test")
        device = next(d for d in profile["devices"] if d["name"] == "cap_dup")
        self.assertEqual(device["type"], "both")

    def test_captures_loopback_routing(self):
        target = PactlRunner.list_hardware_outputs()
        if not target:
            self.skipTest("no hardware outputs available")
        PactlRunner.create_sink_only("cap_lb", "CapLB", channels=2)
        PactlRunner.create_loopback("cap_lb.monitor", target[0], latency_msec=1)
        profile = self.pm.capture_topology("p4", description="test")
        routes = [r for r in profile["routing"] if r["from"] == "cap_lb"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["to"], target[0])
        self.assertEqual(routes[0]["latency_msec"], 1)

    def test_capture_includes_optional_fields(self):
        PactlRunner.create_sink_only(
            "cap_opt",
            "OptionalTest",
            channels=2,
            rate=48000,
            sink_properties="device.description='CapOpt' device.icon_name='speaker'",
        )
        profile = self.pm.capture_topology("p5", description="test")
        device = next(d for d in profile["devices"] if d["name"] == "cap_opt")
        self.assertEqual(device.get("rate"), 48000)
        self.assertIn("sink_properties", device)
        # sink_properties is parsed to a dict
        self.assertIsInstance(device["sink_properties"], dict)
        self.assertEqual(device["sink_properties"].get("device.description"), "CapOpt")

    def test_capture_ignores_loopbacks_from_non_captured_sinks(self):
        """Loopbacks of hardware sinks (created outside Pulsar) are not captured."""
        profile = self.pm.capture_topology("p6", description="test")
        # No routing entries should exist when we have no null-sinks
        self.assertEqual(profile["routing"], [])


class TestApplyProfile(_ProfileTestBase):
    """apply_profile recreates a topology from a profile dict."""

    def test_apply_creates_devices_and_loopbacks(self):
        target = PactlRunner.list_hardware_outputs()
        if not target:
            self.skipTest("no hardware outputs available")
        profile = {
            "schema_version": 2,
            "description": "roundtrip",
            "devices": [
                {"name": "app_a", "type": "sink", "channels": 2},
                {"name": "app_b", "type": "sink", "channels": 2},
                {"name": "app_c", "type": "source", "channels": 2},
            ],
            "routing": [
                {"from": "app_a", "to": target[0], "latency_msec": 1},
                {"from": "app_b", "to": target[0], "latency_msec": 1},
            ],
        }
        result = self.pm.apply_profile(profile)
        self.assertTrue(result["success"], msg=f"apply failed: {result['errors']}")
        self.assertEqual(len(result["created_devices"]), 3)
        self.assertEqual(len(result["created_loopbacks"]), 2)

        # Verify the actual topology
        sinks = _sink_names()
        sources = _source_names()
        self.assertIn("app_a", sinks)
        self.assertIn("app_b", sinks)
        self.assertIn("app_c", sources)
        self.assertEqual(len(PactlRunner.list_loopbacks()), 2)

    def test_apply_unloads_existing_by_default(self):
        # Set up an old sink
        PactlRunner.create_sink_only("stale", "Stale", channels=2)
        self.assertIn("stale", _sink_names())

        target = PactlRunner.list_hardware_outputs()
        if not target:
            self.skipTest("no hardware outputs available")
        profile = {
            "schema_version": 2,
            "description": "fresh",
            "devices": [{"name": "fresh", "type": "sink", "channels": 2}],
            "routing": [{"from": "fresh", "to": target[0], "latency_msec": 1}],
        }
        result = self.pm.apply_profile(profile, unload_existing=True)
        self.assertTrue(result["success"], msg=f"errors: {result['errors']}")
        self.assertNotIn("stale", _sink_names())
        self.assertIn("fresh", _sink_names())

    def test_apply_keeps_existing_when_requested(self):
        PactlRunner.create_sink_only("keepme", "Keep", channels=2)
        target = PactlRunner.list_hardware_outputs()
        if not target:
            self.skipTest("no hardware outputs available")
        profile = {
            "schema_version": 2,
            "description": "layered",
            "devices": [{"name": "addme", "type": "sink", "channels": 2}],
            "routing": [],
        }
        result = self.pm.apply_profile(profile, unload_existing=False)
        self.assertTrue(result["success"], msg=f"errors: {result['errors']}")
        self.assertIn("keepme", _sink_names())
        self.assertIn("addme", _sink_names())

    def test_apply_rejects_bad_routing_target(self):
        # Routing to a sink that doesn't exist must fail cleanly with no state changes
        profile = {
            "schema_version": 2,
            "description": "bad target",
            "devices": [{"name": "d1", "type": "sink", "channels": 2}],
            "routing": [
                {"from": "d1", "to": "alsa_output.does_not_exist", "latency_msec": 1}
            ],
        }
        result = self.pm.apply_profile(profile, unload_existing=False)
        self.assertFalse(result["success"])
        self.assertTrue(any("does_not_exist" in e for e in result["errors"]))
        # No devices should have been created (pre-validation failed)
        self.assertNotIn("d1", _sink_names())

    def test_apply_rejects_invalid_profile(self):
        bad = {
            "schema_version": 99,  # wrong version
            "devices": "not a list",
        }
        result = self.pm.apply_profile(bad)
        self.assertFalse(result["success"])
        self.assertTrue(len(result["errors"]) > 0)

    def test_apply_rejects_invalid_device_name(self):
        bad = {
            "schema_version": 2,
            "devices": [{"name": "has space", "type": "sink", "channels": 2}],
            "routing": [],
        }
        result = self.pm.apply_profile(bad)
        self.assertFalse(result["success"])
        self.assertTrue(any("name" in e for e in result["errors"]))

    def test_apply_resolves_auto_default_sentinel(self):
        """routing[].to == <AUTO_DEFAULT> must resolve to the current default sink."""
        default = PactlRunner.get_default_sink()
        if not default:
            self.skipTest("no default sink available")
        profile = {
            "schema_version": 2,
            "description": "sentinel test",
            "devices": [{"name": "auto_a", "type": "sink", "channels": 2}],
            "routing": [
                {"from": "auto_a", "to": DEFAULT_SINK_SENTINEL, "latency_msec": 1}
            ],
        }
        result = self.pm.apply_profile(profile, unload_existing=True)
        self.assertTrue(result["success"], msg=f"apply failed: {result['errors']}")
        # The created loopback must target the actual default sink, not the
        # sentinel literal.
        loopbacks = PactlRunner.list_loopbacks()
        sinks_for = {lb["sink"] for lb in loopbacks
                     if lb["source"] == "auto_a.monitor"}
        self.assertEqual(sinks_for, {default})
        # No loopback should have the sentinel as its sink target.
        self.assertFalse(
            any(lb["sink"] == DEFAULT_SINK_SENTINEL for lb in loopbacks)
        )

    def test_apply_sentinel_fails_when_no_default_sink(self):
        """If the sentinel can't be resolved, apply must fail cleanly."""
        from unittest.mock import patch as _patch
        profile = {
            "schema_version": 2,
            "description": "no default",
            "devices": [{"name": "nod_a", "type": "sink", "channels": 2}],
            "routing": [
                {"from": "nod_a", "to": DEFAULT_SINK_SENTINEL, "latency_msec": 1}
            ],
        }
        with _patch.object(PactlRunner, "get_default_sink", return_value=None):
            result = self.pm.apply_profile(profile, unload_existing=True)
        self.assertFalse(result["success"])
        self.assertTrue(
            any("AUTO_DEFAULT" in e for e in result["errors"])
        )
        # No devices should have been created (sentinel resolution failed
        # before the device-creation step).
        self.assertNotIn("nod_a", _sink_names())


class TestRoundtrip(_ProfileTestBase):
    """capture → save → wipe → apply → verify the topology matches."""

    def test_full_roundtrip(self):
        target = PactlRunner.list_hardware_outputs()
        if not target:
            self.skipTest("no hardware outputs available")

        # Build a topology
        PactlRunner.create_sink_only("rt_game", "Game", channels=2)
        PactlRunner.create_sink_only("rt_team", "Teamspeak", channels=2)
        PactlRunner.create_source_only("rt_music", "Music", channels=2)
        PactlRunner.create_loopback("rt_game.monitor", target[0], latency_msec=1)
        PactlRunner.create_loopback("rt_team.monitor", target[0], latency_msec=1)

        # Capture
        profile = self.pm.capture_topology("roundtrip", description="test")
        self.assertTrue(self.pm.save_profile("roundtrip", profile))

        # Wipe
        _wipe_topology()
        self.assertEqual(len(_sink_names() | _source_names()), 0)

        # Apply
        loaded = self.pm.get_profile("roundtrip")
        result = self.pm.apply_profile(loaded)
        self.assertTrue(result["success"], msg=f"apply failed: {result['errors']}")

        # Verify
        sinks = _sink_names()
        sources = _source_names()
        self.assertEqual(sinks, {"rt_game", "rt_team"})
        self.assertEqual(sources, {"rt_music"})
        loopbacks = PactlRunner.list_loopbacks()
        self.assertEqual(len(loopbacks), 2)
        # Both loopbacks should target the same hardware
        targets = {lb["sink"] for lb in loopbacks}
        self.assertEqual(targets, {target[0]})
