"""Unit tests for pure device-name parsing in src/utils/device_names.py,
and for the monitor-filter of MainWindow._categorize_hardware_devices.

The parsing tests call module-level functions directly: no Tkinter, no
MainWindow, no mocks, no display, no pactl. The monitor tests use a bare
`object.__new__(MainWindow)` and only touch pure methods.
"""

import unittest

from ui.main_window import MainWindow
from utils.device_names import classify_device_name

#: A PipeWire monitor of the built-in analog card. Must NOT be named
#: `alsa_output.test.monitor`: line 4631 drops any name containing 'test'
#: or 'voip' before the monitor check is ever reached, so such a fixture
#: could never exercise the monitor filter.
MONITOR_SOURCE_NAME = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"
PLAIN_SOURCE_NAME = "alsa_input.pci-0000_00_1f.3.analog-stereo"


def collect_source_dicts(categories):
    """Every source dict reachable in a `_categorize_hardware_devices` result.

    The result is a dict keyed by device type ('builtin'/'usb'/'bluetooth'/
    'hdmi') whose values are lists of entries. A source can appear either as
    an orphaned_source entry's 'source' dict or inside a
    hardware_device_group entry's 'sources' list, so flatten both shapes.

    There is no top-level 'sources' key -- reading result.get("sources", [])
    would always yield [] and every assertion built on it would pass
    vacuously.
    """
    found = []
    for entries in categories.values():
        for entry in entries:
            if isinstance(entry.get("source"), dict):
                found.append(entry["source"])
            found.extend(s for s in entry.get("sources", []) if isinstance(s, dict))
    return found


def _fixture_sources():
    return [
        {"id": "src-monitor", "name": MONITOR_SOURCE_NAME,
         "properties": {"device.description": "Built-in Audio Analog Stereo"}},
        {"id": "src-plain", "name": PLAIN_SOURCE_NAME,
         "properties": {"device.description": "Built-in Audio Analog Stereo"}},
    ]


class TestMonitorFilter(unittest.TestCase):
    """show_monitors is an explicit parameter, so a bare instance can drive it."""

    def _run(self, show_monitors):
        win = object.__new__(MainWindow)
        return win._categorize_hardware_devices(
            [], [], _fixture_sources(), show_monitors=show_monitors
        )

    def test_monitor_source_hidden_when_show_monitors_false(self):
        names = [s.get("name") for s in collect_source_dicts(self._run(False))]
        # Guard: the non-monitor source must survive, or the assertion below
        # would be vacuously true on an empty/garbage result.
        self.assertIn(PLAIN_SOURCE_NAME, names)
        self.assertNotIn(MONITOR_SOURCE_NAME, names)

    def test_monitor_source_shown_when_show_monitors_true(self):
        names = [s.get("name") for s in collect_source_dicts(self._run(True))]
        self.assertIn(PLAIN_SOURCE_NAME, names)
        self.assertIn(MONITOR_SOURCE_NAME, names)


class TestUsbNames(unittest.TestCase):
    def test_usb_headset_extracts_brand_and_model(self):
        result = classify_device_name(
            "alsa_output.usb-BOSS_GCS-8-01.pro-output-0",
            {"properties": {}},
        )
        self.assertEqual(result["device_type"], "usb")
        self.assertEqual(result["device_name"], "BOSS GCS 8")


class TestPciNames(unittest.TestCase):
    def test_pci_with_gpu_description_is_hdmi(self):
        result = classify_device_name(
            "alsa_output.pci-0000_01_00.1.hdmi-stereo",
            {"properties": {"device.description": "NVIDIA HDMI Audio"}},
        )
        self.assertEqual(result["device_type"], "hdmi")
        self.assertEqual(result["device_name"], "NVIDIA HDMI Audio")

    def test_pci_without_gpu_description_is_builtin(self):
        result = classify_device_name(
            "alsa_output.pci-0000_00_1f.3.analog-stereo",
            {"properties": {"device.description": "Built-in Audio"}},
        )
        self.assertEqual(result["device_type"], "builtin")


class TestBluetoothNames(unittest.TestCase):
    """Regression: these were filed as 'builtin' because the bluez branch
    lived inside the alsa_ block and so could never be reached."""

    def test_bluez_output_is_bluetooth(self):
        result = classify_device_name(
            "bluez_output.AC_12_2F_9B_00_11.1",
            {"properties": {"device.description": "Studio Headphones"}},
        )
        self.assertEqual(
            result["device_type"],
            "bluetooth",
            "a bluez sink must not be classified as builtin",
        )
        self.assertEqual(result["device_name"], "Studio Headphones")

    def test_bluez_input_is_bluetooth(self):
        result = classify_device_name(
            "bluez_input.AC_12_2F_9B_00_11.1",
            {"properties": {"device.description": "Studio Headphones"}},
        )
        self.assertEqual(result["device_type"], "bluetooth")


class TestFallback(unittest.TestCase):
    def test_unknown_prefix_falls_back_to_description(self):
        result = classify_device_name(
            "some_unknown_device_thing",
            {"properties": {}, "description": "Mystery Device"},
        )
        self.assertEqual(result["device_type"], "builtin")
        self.assertEqual(result["device_name"], "Mystery Device")

    def test_empty_name_returns_none(self):
        self.assertIsNone(classify_device_name("", {"properties": {}}))


# --- Added beyond the plan's 7, to reach the plan's stated 9 and to pin the
# --- branches of classify_device_name that the plan's cases leave uncovered.

class TestBranchCoverage(unittest.TestCase):
    def test_unknown_alsa_connection_type_falls_back_to_builtin(self):
        """alsa_ with a connection part that is neither usb- nor pci-."""
        result = classify_device_name(
            "alsa_output.weird-thing.pro-output-0",
            {"properties": {}, "description": "Weird Card"},
        )
        self.assertEqual(result["device_type"], "builtin")
        self.assertEqual(result["device_name"], "Weird Card")

    def test_alsa_name_without_dot_falls_through_to_fallback(self):
        """A single-segment alsa_ name has no parts[1]; it must still return a dict."""
        result = classify_device_name(
            "alsa_output",
            {"properties": {}, "description": "Bare ALSA"},
        )
        self.assertEqual(result["device_type"], "builtin")
        self.assertEqual(result["device_name"], "Bare ALSA")


if __name__ == "__main__":
    unittest.main()
