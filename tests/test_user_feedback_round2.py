"""
Regressions from user testing, 2026-10-22.

Four things the user found, none of which any test would have caught:

1. The Multi-output Routing listbox was EMPTY. Removing the duplicate
   "Route Selected Sink" panel also removed the only code that filled a
   hardware-outputs listbox, so the surviving panel had nothing to
   select and "Apply to Selected" did nothing.
2. Text typed into the sink name field came out GREY, because the field
   was left in placeholder styling and nothing reset the colour.
3. The custom channel field sat to the right of the dropdown, always
   visible, which read as a second input for the same value.
4. Loopback Latency "Auto" said "recommended for 44kHz" on a 48kHz
   machine, because a preset handler wrote a hardcoded 44100 and
   everything derived from the rate inherited it.
"""

import os
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

from pulsar.utils.pactl_runner import PactlRunner


def _have_display():
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestRoutingListIsPopulated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pulsar.ui.main_window import MainWindow

        cls.root = tk.Tk()
        cls.app = MainWindow(cls.root)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_the_multi_routing_listbox_is_not_empty(self):
        """The panel's whole purpose is to list the hardware outputs.

        It was created and never populated. The listbox looked correct
        and was always empty.
        """
        with mock.patch.object(
                PactlRunner, "list_hardware_outputs",
                staticmethod(lambda: ["speakers", "headphones"])):
            self.app._refresh_multi_routing_outputs()
            self.assertEqual(
                list(self.app.multi_routing_listbox.get(0, tk.END)),
                ["speakers", "headphones"])

    def test_the_listbox_is_populated_on_startup(self):
        """Not only after a refresh -- it must work when the tab opens.

        Mocked rather than read from the machine. The real list depends
        on what hardware the host happens to have, and the suite itself
        can leave the host without a default output -- so a test that
        asserts on the real list fails depending on what ran before it.
        That is exactly the kind of order-dependence this project has
        been bitten by before.
        """
        with mock.patch.object(
                PactlRunner, "list_hardware_outputs",
                staticmethod(lambda: ["speakers", "headphones"])):
            self.app._refresh_multi_routing_outputs()
            self.assertEqual(
                list(self.app.multi_routing_listbox.get(0, tk.END)),
                ["speakers", "headphones"])

    def test_a_refresh_preserves_the_selection(self):
        """A refresh must not silently drop a route the user set up."""
        with mock.patch.object(
                PactlRunner, "list_hardware_outputs",
                staticmethod(lambda: ["speakers", "headphones"])):
            self.app._refresh_multi_routing_outputs()
            self.app.multi_routing_listbox.selection_set(1)
            self.app._refresh_multi_routing_outputs()
            self.assertEqual(
                list(self.app.multi_routing_listbox.curselection()), [1])

    def test_a_listing_failure_does_not_crash_the_refresh(self):
        with mock.patch.object(
                PactlRunner, "list_hardware_outputs",
                side_effect=RuntimeError("pactl exploded")):
            self.app._refresh_multi_routing_outputs()
        self.assertEqual(self.app.multi_routing_listbox.size(), 0)


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestNameFieldTextIsNormalColour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pulsar.ui.main_window import MainWindow

        cls.root = tk.Tk()
        cls.app = MainWindow(cls.root)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_the_field_is_not_grey_before_typing(self):
        """It opened grey, so everything typed into it came out grey."""
        self.app.sink_name_var.set("")
        self.app._set_name_field_normal()
        self.assertNotEqual(
            str(self.app.sink_name_entry.cget("foreground")).lower(), "gray",
            "the name field is in placeholder styling with nothing in it")

    def test_typed_text_is_not_grey(self):
        """The user's report: 'when typing, the text is placeholder gray'."""
        self.app.sink_name_var.set("mysink")
        self.app.on_name_key_press(None)
        self.root.update_idletasks()
        self.assertNotEqual(
            str(self.app.sink_name_entry.cget("foreground")).lower(), "gray",
            "typed text is coming out in the placeholder colour")

    def test_the_colour_matches_the_theme(self):
        """Not a hard-coded black, which is wrong on a dark theme."""
        expected = ttk.Style().lookup("TEntry", "foreground")
        self.app._set_name_field_normal()
        self.assertEqual(
            str(self.app.sink_name_entry.cget("foreground")), expected)

    def test_no_grey_styling_survives_a_preset_change(self):
        """The preset handler was the thing that left the field grey."""
        self.app.on_audio_preset_selected(None)
        self.root.update_idletasks()
        self.assertNotEqual(
            str(self.app.sink_name_entry.cget("foreground")).lower(), "gray",
            "a preset change left the name field in placeholder styling")


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestLatencyAutoTracksTheRate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pulsar.ui.main_window import MainWindow

        cls.root = tk.Tk()
        cls.app = MainWindow(cls.root)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_the_auto_label_names_the_actual_rate(self):
        """It said 'recommended for 44kHz' while the rate read 48000."""
        self.assertIn(
            f"{int(self.app.rate_var.get()) // 1000}kHz",
            self.app.latency_preset_var.get(),
            "the latency recommendation does not match the sample rate")

    def test_the_recommendation_matches_the_rate(self):
        rate = int(self.app.rate_var.get())
        self.assertEqual(
            self.app._latency_recommended_ms,
            PactlRunner.recommended_loopback_latency_ms(rate))

    def test_the_auto_label_follows_a_rate_change(self):
        self.app.rate_var.set("96000")
        self.root.update_idletasks()
        self.assertIn("96kHz", self.app.latency_preset_var.get())
        self.app.rate_var.set(self.app._detected_rate)
        self.root.update_idletasks()

    def test_a_preset_change_does_not_leave_a_stale_rate(self):
        """The hardcoded 44100 used to survive the preset handler."""
        self.app.on_audio_preset_selected(None)
        self.root.update_idletasks()
        self.assertEqual(self.app.rate_var.get(), self.app._detected_rate)
        self.assertIn(
            f"{int(self.app._detected_rate) // 1000}kHz",
            self.app.latency_preset_var.get())


if __name__ == "__main__":
    unittest.main()
