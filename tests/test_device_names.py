"""Unit tests for pure device-name parsing in src/utils/device_names.py.

These call module-level functions directly: no Tkinter, no MainWindow, no
mocks, no display, no pactl.
"""

import unittest

from utils.device_names import classify_device_name


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
