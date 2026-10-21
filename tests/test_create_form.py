"""
Tests for the Create tab's form defaults.

These build the real window under a virtual display and mock every
PulseAudio query, so what is asserted is what the user actually sees in
the form fields — not a stand-in's idea of them.

Skipped without a display, since `tk.Tk()` needs one. Under Xvfb:

    DISPLAY=:95 .venv/bin/python -m pytest tests/test_create_form.py -q

Three user-reported defects are covered:

* The sample rate was hardcoded to 44100, so a user whose hardware runs
  at 48000 got every new sink resampled. It must follow the DEFAULT SINK's
  actual rate (`pactl list sinks` -> `Sample Specification: float32le 2ch
  48000Hz`).
* Channels was a read-only-ish dropdown of 1/2/4/6/8. Any positive
  integer works at the PulseAudio level, so the entry must accept one.
* The name field was pre-filled with e.g. "stereo (auto)", so typing a new
  name meant erasing the old one first. It must start empty.
"""

import os
import shutil
import tkinter as tk
import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow


#: A sink entry as `PactlRunner.list_sinks()` returns it.
def _sink(name, spec="float32le 2ch 48000Hz"):
    return {
        "id": "0",
        "name": name,
        "description": name,
        "state": "SINK_RUNNING",
        "sample_spec": spec,
    }


def build_app(default_sink="alsa_output.usb", sinks=None):
    """Construct a real MainWindow with the audio server mocked out.

    Returns (app, root). The caller destroys root.
    """
    if sinks is None:
        sinks = [_sink("alsa_output.usb"), _sink("stereo", "s16le 2ch 44100Hz")]

    root = tk.Tk()
    root.withdraw()

    pactl = mock.MagicMock()
    pactl.list_sinks.return_value = sinks
    pactl.list_sources.return_value = [_sink("alsa_output.usb.monitor")]
    pactl.list_modules.return_value = []
    pactl.list_hardware_outputs.return_value = ["alsa_output.usb"]
    pactl.list_sink_inputs.return_value = []
    pactl.list_clients.return_value = []
    pactl.get_default_sink.return_value = default_sink

    with mock.patch("pulsar.ui.main_window.PactlRunner", pactl), \
            mock.patch("pulsar.ui.main_window.PresetManager") as pm:
        pm.return_value.get_preset_names.return_value = [
            "Stereo", "Mono", "5.1 Surround", "7.1 Surround", "Custom",
        ]
        pm.return_value.get_preset.side_effect = lambda name: {
            "Stereo": {
                "channels": "2",
                "channel_map": "front-left,front-right",
                "description": "Stereo Virtual Device",
                "builtin": True,
            },
        }.get(name)
        pm.return_value.is_builtin_preset.return_value = True
        app = MainWindow(root)
    root.update_idletasks()
    return app, root


@unittest.skipUnless(os.environ.get("DISPLAY"), "no DISPLAY; run under Xvfb")
class TestSampleRateDefault(unittest.TestCase):
    """The rate must follow the default sink, not a hardcoded 44100."""

    def setUp(self):
        self.app, self.root = build_app()
        self.addCleanup(self.root.destroy)

    def test_defaults_to_the_default_sinks_rate(self):
        self.assertEqual(
            self.app.rate_var.get(), "48000",
            "form still defaults to 44100, so every sink is resampled",
        )

    def test_follows_a_non_48k_default_sink(self):
        """Proves the rate is parsed, not just swapped for 48000."""
        app, root = build_app(
            default_sink="alsa_output.hdmi",
            sinks=[_sink("alsa_output.usb", "s16le 2ch 44100Hz"),
                   _sink("alsa_output.hdmi", "float32le 2ch 96000Hz")],
        )
        self.addCleanup(root.destroy)
        self.assertEqual(app.rate_var.get(), "96000")

    def test_non_default_sinks_do_not_influence_the_default(self):
        app, root = build_app(
            default_sink="alsa_output.usb",
            sinks=[_sink("stereo", "s16le 2ch 44100Hz"),
                   _sink("alsa_output.usb", "float32le 2ch 48000Hz")],
        )
        self.addCleanup(root.destroy)
        self.assertEqual(app.rate_var.get(), "48000")

    def test_falls_back_to_48000_when_the_default_is_unknown(self):
        app, root = build_app(default_sink=None, sinks=[])
        self.addCleanup(root.destroy)
        self.assertEqual(app.rate_var.get(), "48000")

    def test_falls_back_to_48000_when_sample_spec_is_unparseable(self):
        app, root = build_app(
            default_sink="alsa_output.usb",
            sinks=[{"id": "0", "name": "alsa_output.usb"}],
        )
        self.addCleanup(root.destroy)
        self.assertEqual(app.rate_var.get(), "48000")

    def test_the_default_rate_is_reachable_in_the_dropdown(self):
        """A combobox whose selection isn't in its list looks broken."""
        values = tuple(str(v) for v in self.app.rate_combo["values"])
        self.assertIn(self.app.rate_var.get(), values)


@unittest.skipUnless(os.environ.get("DISPLAY"), "no DISPLAY; run under Xvfb")
class TestChannelsAcceptAnyCount(unittest.TestCase):
    """16 channels works in PulseAudio; the form must not forbid it."""

    def setUp(self):
        self.app, self.root = build_app()
        self.addCleanup(self.root.destroy)

    def test_channels_entry_is_editable(self):
        self.assertEqual(
            str(self.app.channels_combo.cget("state")), "normal",
            "channels entry is not editable, so no count outside the "
            "dropdown is typeable",
        )

    def test_the_old_suggestions_are_still_offered(self):
        values = tuple(str(v) for v in self.app.channels_combo["values"])
        for expected in ("1", "2", "4", "6", "8"):
            self.assertIn(expected, values)

    def test_an_arbitrary_channel_count_is_accepted(self):
        self.app.channels_var.set("16")
        self.root.update_idletasks()
        self.assertEqual(int(self.app.channels_var.get()), 16)
        self.app.update_command_preview()
        self.assertIn("channels=16", self.app.command_preview_var.get())

    def test_an_odd_channel_count_is_accepted(self):
        self.app.channels_var.set("7")
        self.root.update_idletasks()
        self.assertEqual(int(self.app.channels_var.get()), 7)

    def test_channel_map_does_not_stay_stale_for_an_unlisted_count(self):
        """A 2-entry channel map with 16 channels is rejected by pactl, so
        the map must be cleared (PulseAudio then picks by channel count)."""
        self.app.channels_var.set("16")
        self.root.update_idletasks()
        self.assertEqual(self.app.channel_map_var.get().strip(), "")


@unittest.skipUnless(os.environ.get("DISPLAY"), "no DISPLAY; run under Xvfb")
class TestNameFieldStartsEmpty(unittest.TestCase):
    """No pre-filled name: the user must not have to erase one."""

    def setUp(self):
        self.app, self.root = build_app()
        self.addCleanup(self.root.destroy)

    def test_name_entry_is_empty_on_open(self):
        self.assertEqual(
            self.app.sink_name_var.get(), "",
            "name is pre-filled; the user has to delete it before typing",
        )

    def test_reapplying_a_preset_does_not_refill_the_name(self):
        """`on_audio_preset_selected` is what put the value there before;
        it is re-run on every preset change and on tab re-entry."""
        self.app.on_audio_preset_selected(None)
        self.root.update_idletasks()
        self.assertEqual(self.app.sink_name_var.get(), "")

    def test_refresh_create_tab_state_does_not_refill_the_name(self):
        self.app.refresh_create_tab_state()
        self.root.update_idletasks()
        self.assertEqual(self.app.sink_name_var.get(), "")

    def test_a_typed_name_is_kept_verbatim(self):
        self.app.sink_name_var.set("game_sink")
        self.app.on_audio_preset_selected(None)
        self.root.update_idletasks()
        self.assertEqual(self.app.sink_name_var.get(), "game_sink")

    def test_an_empty_name_still_auto_names_at_create_time(self):
        """Removing the pre-fill must not break auto-naming."""
        self.assertEqual(self.app.sink_name_var.get(), "")
        self.app.update_command_preview()
        self.assertIn("sink_name=", self.app.command_preview_var.get())


@unittest.skipUnless(os.environ.get("DISPLAY"), "no DISPLAY; run under Xvfb")
class TestStaticPactlHintsRemoved(unittest.TestCase):
    """The two gray hint labels were read as instructions to type."""

    def setUp(self):
        self.app, self.root = build_app()
        self.addCleanup(self.root.destroy)

    def test_the_static_format_hint_is_not_shown(self):
        label = self.app.command_format_label
        self.assertEqual(str(label.cget("text")), "")
        self.assertEqual(label.winfo_manager(), "",
                         "the static 'Format: pactl load-module ...' hint "
                         "is still gridded into the form")

    def test_switching_device_type_does_not_fail(self):
        """`on_device_type_changed` still writes to the label, so the
        attribute must exist even though nothing is displayed."""
        self.app.device_type_var.set("sink")
        self.app.on_device_type_changed()
        self.root.update_idletasks()

    def test_the_live_command_preview_still_mirrors_the_form(self):
        self.app.sink_name_var.set("game_sink")
        self.app.channels_var.set("8")
        self.app.update_command_preview()
        cmd = self.app.command_preview_var.get()
        self.assertIn("sink_name=game_sink", cmd)
        self.assertIn("channels=8", cmd)