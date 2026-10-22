"""
The channel count must not be capped at 8, and the cap must be visible
as absent.

User testing: "Channels are still limited to 8 with no custom field on
advanced options in create tab."

The old control was an editable combobox whose dropdown listed only
1/2/4/6/8. Typing a different number did work -- and was verified live
at 16 channels -- but nothing said so, so the dropdown read as the
complete set of allowed values. The cap was in the UI, not in
PulseAudio.

These tests pin the shape of the fix: an explicit "Custom" option that
reveals a field, and no silent fallback to 2 when the value is wrong.
"""

import os
import tkinter as tk
import unittest


def _have_display():
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCustomChannelCount(unittest.TestCase):
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

    def tearDown(self):
        self.app.channels_var.set("2")
        self.app.channels_custom_var.set("")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()

    def test_the_dropdown_offers_custom_explicitly(self):
        """The complaint was that no custom field was offered.

        A combobox you can type into is not discoverable as such; the
        option has to be IN the list.
        """
        values = list(self.app.channels_combo.cget("values"))
        self.assertIn("Custom", values)
        self.assertEqual(
            sorted(v for v in values if v != "Custom"),
            ["1", "2", "4", "6", "8"],
            "the common values should stay as suggestions")

    def test_choosing_custom_shows_the_field(self):
        """Hidden until Custom is chosen, then shown on its own row.

        Visibility rather than an enabled/disabled state: a greyed-out
        field on screen still reads as "a field that is somehow not
        active", which is what the first version of this looked like.
        """
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        # winfo_manager, not winfo_ismapped: the Advanced Options frame is
        # collapsed in this test, so nothing inside it is mapped no matter
         # what we do. What matters is whether the widget is gridded at all.
        self.assertEqual(
            self.app.channels_custom_entry.winfo_manager(), "grid",
            "the custom channel field is not shown after choosing Custom")
        self.assertEqual(
            str(self.app.channels_custom_entry.cget("state")), "normal")

    def test_choosing_a_preset_hides_the_field(self):
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        self.app.channels_var.set("8")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        self.assertEqual(
            self.app.channels_custom_entry.winfo_manager(), "",
            "the custom channel field is still shown after choosing a preset")

    def test_a_custom_count_is_used(self):
        """The whole point: 16 is not 8."""
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("16")
        self.assertEqual(self.app._effective_channels(), 16)

    def test_a_preset_count_is_used(self):
        self.app.channels_var.set("8")
        self.assertEqual(self.app._effective_channels(), 8)

    def test_the_old_ceiling_is_not_enforced(self):
        """Any count up to 16 is allowed, including ones nobody suggested.

        The old list topped out at 8, which was a UI invention rather
        than a PulseAudio limit. The ceiling is 16.
        """
        for count in (1, 3, 5, 7, 9, 16):
            self.app.channels_var.set("Custom")
            self.app.channels_custom_var.set(str(count))
            self.assertEqual(
                self.app._effective_channels(), count,
                f"{count} channels should be allowed")

    def test_more_than_sixteen_channels_is_rejected(self):
        """The user asked for a maximum of 16."""
        for count in (17, 24, 64):
            self.app.channels_var.set("Custom")
            self.app.channels_custom_var.set(str(count))
            with self.assertRaises(ValueError) as ctx:
                self.app._effective_channels()
            self.assertIn("maximum of 16", str(ctx.exception))

    def test_custom_selected_with_nothing_typed_is_an_error(self):
        """Not a silent fallback to 2."""
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("")
        with self.assertRaises(ValueError) as ctx:
            self.app._effective_channels()
        self.assertIn("Enter a channel count", str(ctx.exception))

    def test_a_nonsense_count_is_an_error_not_a_default(self):
        """The old code did `except ValueError: channels = 2`.

        That produced a stereo sink when the user asked for sixteen, with
        no indication anything had gone wrong.
        """
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("sixteen")
        with self.assertRaises(ValueError) as ctx:
            self.app._effective_channels()
        self.assertIn("not a valid channel count", str(ctx.exception))

    def test_zero_and_negative_are_rejected(self):
        for bad in ("0", "-4"):
            self.app.channels_var.set("Custom")
            self.app.channels_custom_var.set(bad)
            with self.assertRaises(ValueError):
                self.app._effective_channels()

    def test_the_preview_shows_the_custom_count(self):
        """The preview must not describe a different sink than gets made."""
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("16")
        self.app.update_command_preview()
        preview = self.app.command_preview_var.get()
        self.assertIn("channels=16", preview)

    def test_typing_in_the_custom_field_reacts_immediately(self):
        """The custom field is a second source for the same value.

        It needs the same reactions as the dropdown: the channel map
        default and the command preview must both follow it, without
        the user having to touch the dropdown first.
        """
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("")
        self.root.update_idletasks()

        self.app.channels_custom_var.set("16")
        self.root.update_idletasks()

        # 16 has no preset layout, so the map must be cleared.
        self.assertEqual(self.app.channel_map_var.get().strip(), "")
        self.assertIn("channels=16", self.app.command_preview_var.get())

    def test_the_trace_callback_does_not_shadow_self(self):
        """A Tk trace callback is called with the variable's NAME.

        `def handler(self, *_)` binds self to the string 'channels_var'
        and every attribute access inside raises AttributeError. The
        symptom is a traceback printed to stderr by Tk -- which is
        swallowed in a background callback -- and a value that quietly
        stops updating. So this asserts the observable effect, not the
        signature.
        """
        self.app.channel_map_var.set("front-left,front-right")
        self.app.channels_var.set("Custom")
        self.app.channels_custom_var.set("16")
        self.root.update_idletasks()
        self.assertEqual(
            self.app.channel_map_var.get().strip(), "",
            "the channel map did not react to the custom count, which is "
            "what a shadowed `self` in a trace callback looks like")


if __name__ == "__main__":
    unittest.main()
