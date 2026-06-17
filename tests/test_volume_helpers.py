"""
Unit tests for the volume, mute, and default-device helpers added in Phase 4.

End-to-end tests that drive a real sink/source and verify round-trips.
"""

import unittest

from utils.pactl_runner import PactlRunner


def pactl_available() -> bool:
    import shutil
    return shutil.which("pactl") is not None


def _wipe_topology():
    for lb in PactlRunner.list_loopbacks():
        PactlRunner.unload_loopback(lb["id"])
    PactlRunner.unload_all_null_sinks()


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestVolumeHelpers(unittest.TestCase):

    def setUp(self):
        _wipe_topology()
        # Create one sink and one source for volume tests
        PactlRunner.create_sink_only("__vol_sink", "VolSink", channels=2)
        PactlRunner.create_source_only("__vol_source", "VolSource", channels=2)

    def tearDown(self):
        _wipe_topology()

    def test_sink_volume_roundtrip(self):
        for pct in (0, 25, 50, 100):
            self.assertTrue(PactlRunner.set_sink_volume("__vol_sink", pct))
            got = PactlRunner.get_sink_volume("__vol_sink")
            self.assertEqual(got, pct, f"set {pct}% should read back as {pct}%")

    def test_source_volume_roundtrip(self):
        for pct in (0, 33, 100):
            self.assertTrue(PactlRunner.set_source_volume("__vol_source", pct))
            got = PactlRunner.get_source_volume("__vol_source")
            self.assertEqual(got, pct)

    def test_set_sink_volume_clamps_negative(self):
        # Negative percent should clamp to 0
        self.assertTrue(PactlRunner.set_sink_volume("__vol_sink", -50))
        got = PactlRunner.get_sink_volume("__vol_sink")
        self.assertEqual(got, 0)

    def test_sink_mute_roundtrip(self):
        self.assertTrue(PactlRunner.set_sink_mute("__vol_sink", True))
        self.assertTrue(PactlRunner.get_sink_mute("__vol_sink"))
        self.assertTrue(PactlRunner.set_sink_mute("__vol_sink", False))
        self.assertFalse(PactlRunner.get_sink_mute("__vol_sink"))

    def test_get_sink_volume_returns_none_for_unknown_sink(self):
        self.assertIsNone(PactlRunner.get_sink_volume("__does_not_exist"))

    def test_parse_volume_percent_edge_cases(self):
        self.assertEqual(
            PactlRunner._parse_volume_percent("Volume: front-left: 65536 / 100% / 0.00 dB"),
            100,
        )
        self.assertIsNone(PactlRunner._parse_volume_percent(""))
        self.assertIsNone(PactlRunner._parse_volume_percent(None))
        self.assertIsNone(PactlRunner._parse_volume_percent("no percent here"))


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestDefaultDeviceHelpers(unittest.TestCase):

    def setUp(self):
        # Snapshot the original default so we can restore it
        self._original_sink = PactlRunner.get_default_sink()
        self._original_source = PactlRunner.get_default_source()

    def tearDown(self):
        # Always restore — don't pollute the user's machine
        if self._original_sink:
            PactlRunner.set_default_sink(self._original_sink)
        if self._original_source:
            PactlRunner.set_default_source(self._original_source)
        _wipe_topology()

    def test_get_default_sink_returns_a_string(self):
        sink = PactlRunner.get_default_sink()
        self.assertIsNotNone(sink)
        self.assertIsInstance(sink, str)
        self.assertGreater(len(sink), 0)

    def test_set_default_sink_roundtrip(self):
        # Create a null-sink and make IT the default — the original default
        # will be restored in tearDown
        PactlRunner.create_sink_only("__default_sink", "DefSink", channels=2)
        self.assertTrue(PactlRunner.set_default_sink("__default_sink"))
        self.assertEqual(PactlRunner.get_default_sink(), "__default_sink")

    def test_get_default_source_returns_a_string(self):
        src = PactlRunner.get_default_source()
        self.assertIsNotNone(src)
        self.assertIsInstance(src, str)


if __name__ == "__main__":
    unittest.main()
