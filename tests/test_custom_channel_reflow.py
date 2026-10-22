"""Does showing the custom channel row push the rest of the panel down?

User testing: 'Custom channels doesn't properly push down the other
elements below it.'

Tk's grid does not reflow. A widget keeps the row it was given, so
inserting a row means moving everything below it -- or the new row
overlaps what was there.

The check is on the actual grid rows, not on a screenshot: overlapping
widgets still report their own rows, so a screenshot can look plausible
while two widgets occupy the same space.
"""

import os
import tkinter as tk
import unittest


def _have_display():
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCustomChannelRowReflows(unittest.TestCase):
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
        self.app._on_channels_preset_change()
        self.root.update_idletasks()

    def _row(self, widget):
        info = widget.grid_info()
        return int(info["row"])

    def test_the_custom_field_takes_row_three(self):
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        self.assertEqual(self._row(self.app.channels_custom_entry), 3)

    def test_the_channel_map_is_pushed_down(self):
        """It was on row 3 too -- the two overlapped."""
        before = self._row(self.app.channel_map_combo)
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        after = self._row(self.app.channel_map_combo)
        self.assertEqual(before, 3)
        self.assertEqual(after, 4, "the Channel Map did not move down")
        self.assertNotEqual(
            after, self._row(self.app.channels_custom_entry),
            "the custom field and the Channel Map are on the same row")

    def test_everything_below_is_pushed_down(self):
        """Channel Map, Additional Properties and Loopback Latency."""
        widgets = [
            self.app.channel_map_label,
            self.app.channel_map_combo,
            self.app.properties_label,
            self.app.properties_entry,
            self.app.latency_label,
            self.app.latency_combo,
        ]
        before = {id(w): self._row(w) for w in widgets}
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        for w in widgets:
            self.assertEqual(
                self._row(w), before[id(w)] + 1,
                f"{w} did not move down by one row")

    def test_no_two_widgets_share_a_row(self):
        """The actual bug: the custom field and the Channel Map both
        wanted row 3, so one covered the other."""
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()

        rows = {}
        for w in (self.app.channels_custom_entry,
                  self.app.channels_custom_label,
                  self.app.channel_map_combo,
                  self.app.channel_map_label,
                  self.app.properties_entry,
                  self.app.properties_label,
                  self.app.latency_combo,
                  self.app.latency_label):
            rows.setdefault(self._row(w), []).append(w)
        for row, widgets in rows.items():
            self.assertLessEqual(
                len(widgets), 2,
                f"row {row} has {len(widgets)} widgets: {widgets} "
                f"(a label and its control is fine; more is an overlap)")

    def test_hiding_it_puts_everything_back(self):
        widgets = [
            self.app.channel_map_combo,
            self.app.properties_entry,
            self.app.latency_combo,
        ]
        before = {id(w): self._row(w) for w in widgets}
        self.app.channels_var.set("Custom")
        self.app._on_channels_preset_change()
        self.app.channels_var.set("2")
        self.app._on_channels_preset_change()
        self.root.update_idletasks()
        for w in widgets:
            self.assertEqual(
                self._row(w), before[id(w)],
                f"{w} did not go back to its original row")

    def test_the_hint_is_gone(self):
        """The user asked for the '(1 to 16)' hint to be removed."""
        self.assertFalse(
            hasattr(self.app, "channels_custom_hint"),
            "the (1 to 16) hint is still there")


if __name__ == "__main__":
    unittest.main()
