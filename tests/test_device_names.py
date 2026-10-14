"""Unit tests for pure device-name parsing in src/utils/device_names.py,
and for the monitor-filter of MainWindow._categorize_hardware_devices.

The parsing tests call module-level functions directly: no Tkinter, no
MainWindow, no mocks, no display, no pactl. The monitor tests use a bare
`object.__new__(MainWindow)` and only touch pure methods.
"""

import unittest

from ui.main_window import MainWindow
from utils.device_names import classify_device_name, detect_device_type

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


# --- Task 5: detect_device_type moved out of MainWindow, plus the
# --- agreement tests pinning that both classifiers return the same type
# --- for the same device. Added the module_data case because the real
# --- signature takes three parameters, not the two the plan states.

class TestDetectDeviceType(unittest.TestCase):
    def test_bluez_sink_is_bluetooth(self):
        data = {"name": "bluez_output.AC_12_2F_9B_00_11.1",
                "description": "Studio Headphones", "properties": {}}
        self.assertEqual(detect_device_type(sink_data=data), "bluetooth")

    def test_usb_sink_is_usb(self):
        data = {"name": "alsa_output.usb-BOSS_GCS-8-01.pro-output-0",
                "description": "BOSS", "properties": {}}
        self.assertEqual(detect_device_type(sink_data=data), "usb")

    def test_nothing_supplied_is_builtin(self):
        self.assertEqual(detect_device_type(), "builtin")

    def test_module_data_is_classified(self):
        """module_data is the third parameter; _extract_hardware_device_info
        depends on it, so dropping it would silently mis-file every module."""
        data = {"name": "module-bluetooth", "argument": "card=bluez",
                "properties": {"device.bus": "bluetooth"}}
        self.assertEqual(detect_device_type(module_data=data), "bluetooth")

    def test_delegate_on_main_window_keeps_all_three_parameters(self):
        """The MainWindow method is a thin delegate; every call site must
        keep working through it, including the module_data one."""
        win = object.__new__(MainWindow)
        sink = {"name": "alsa_output.usb-BOSS_GCS-8-01.pro-output-0",
                "properties": {}}
        module = {"name": "module-hdmi", "properties": {}}
        self.assertEqual(win._detect_device_type(sink_data=sink), "usb")
        self.assertEqual(win._detect_device_type(source_data=sink), "usb")
        self.assertEqual(win._detect_device_type(module_data=module), "hdmi")


class TestClassifiersAgree(unittest.TestCase):
    """The two classifiers must not disagree about the same device.

    Before the extraction, _extract_hardware_device_info_from_name returned
    'builtin' for a bluetooth headset while _detect_device_type returned
    'bluetooth' for the identical input.
    """

    def test_bluez_agrees(self):
        name = "bluez_output.AC_12_2F_9B_00_11.1"
        data = {"name": name, "description": "Studio Headphones", "properties": {}}
        self.assertEqual(
            classify_device_name(name, data)["device_type"],
            detect_device_type(sink_data=data),
        )

    def test_usb_agrees(self):
        name = "alsa_output.usb-BOSS_GCS-8-01.pro-output-0"
        data = {"name": name, "description": "BOSS", "properties": {}}
        self.assertEqual(
            classify_device_name(name, data)["device_type"],
            detect_device_type(sink_data=data),
        )


class TestMainWindowBluetoothRegression(unittest.TestCase):
    """The bug the branch exists to fix lived in MainWindow, not in the
    extracted module.

    TestBluetoothNames covers classify_device_name directly, so it passes
    even when MainWindow still has the old inline parsing. These tests go
    through the UI path -- the delegate at MainWindow._extract_hardware_
    device_info_from_name -- which is what actually shipped broken.
    """

    def setUp(self):
        self.win = object.__new__(MainWindow)

    def test_ui_path_classifies_bluez_sink_as_bluetooth(self):
        result = self.win._extract_hardware_device_info_from_name(
            "bluez_output.AC_12_2F_9B_00_11.1",
            {"properties": {"device.description": "Studio Headphones"}},
        )
        self.assertEqual(
            result["device_type"],
            "bluetooth",
            "MainWindow must not file a bluetooth headset as builtin",
        )

    def test_ui_path_agrees_with_detect_device_type(self):
        data = {
            "name": "bluez_output.AC_12_2F_9B_00_11.1",
            "description": "Studio Headphones",
            "properties": {},
        }
        ui_type = self.win._extract_hardware_device_info_from_name(
            data["name"], data
        )["device_type"]
        self.assertEqual(ui_type, detect_device_type(sink_data=data))

    def test_ui_path_keeps_alsa_behaviour(self):
        """Guard the extraction from over-correcting: ALSA names must be
        unaffected by the bluetooth fix."""
        result = self.win._extract_hardware_device_info_from_name(
            "alsa_output.usb-BOSS_GCS-8-01.pro-output-0", {"properties": {}}
        )
        self.assertEqual(result["device_type"], "usb")
        self.assertEqual(result["device_name"], "BOSS GCS 8")


if __name__ == "__main__":
    unittest.main()
