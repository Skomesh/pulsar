"""
Tests that widgets actually get displayed.

Issue #17: `routing_frame` was built and fully populated -- a "Route to:"
dropdown, a status line, and the whole Multi-output Routing panel nested
inside it -- but was never passed to pack/grid/place. Tk reported:

    routing_frame       manager=''  ismapped=0  size=1x1

so the section was invisible and unreachable by any means: not by
scrolling, not by resizing, not by keyboard.

The existing GUI test could not have caught it. tests/test_tab_refresh.py
drives handlers on `object.__new__(MainWindow)`, so no widget is ever
given a geometry manager and nothing about display is observable at all.
That is the same blind spot as the `->` glyph that rendered as a circled
symbol while every unit test stayed green.

These tests construct the real window and ask Tk what it actually did.

Skipped without a display, since `tk.Tk()` needs one. Under Xvfb:

    /tmp/xvfb-local/root/usr/bin/Xvfb :95 -screen 0 1400x1000x24 -nolisten tcp
    DISPLAY=:95 .venv/bin/python -m pytest tests/test_widget_display.py -q
"""

import os
import shutil
import tkinter as tk
import unittest

#: Widgets that are meant to be on screen and are reachable as attributes.
#: `button_frame` is deliberately absent -- it is a local variable in the
#: builder, not `self.button_frame`, so there is nothing to assert on.
#: `advanced_frame` is also absent: `toggle_advanced_options` grids it
#: conditionally, so an empty winfo_manager() is correct until the user
#: ticks the checkbox.
MUST_BE_VISIBLE = [
    "routing_frame",
    "unified_tree",
    "multi_routing_frame",
]


def have_display():
    return os.environ.get("DISPLAY") and shutil.which("xvfb-run")


@unittest.skipUnless(os.environ.get("DISPLAY"),
                     "no DISPLAY; run under Xvfb to exercise this")
class TestWidgetsAreDisplayed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pulsar.ui.main_window import MainWindow

        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MainWindow(cls.root)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_routing_frame_has_a_geometry_manager(self):
        """The regression guard for issue #17."""
        self.assertTrue(
            self.app.routing_frame.winfo_manager(),
            "routing_frame has no geometry manager, so it can never be seen",
        )

    def test_routing_frame_is_larger_than_a_collapsed_default(self):
        """A packed widget gets a real size; an unmanaged one stays 1x1."""
        self.app.routing_frame.update_idletasks()
        self.assertGreater(
            self.app.routing_frame.winfo_width(), 1,
            "routing_frame is still 1x1 -- built but never displayed")

    def test_frames_intended_to_be_visible_are_all_attached(self):
        orphans = []
        for name in MUST_BE_VISIBLE:
            widget = getattr(self.app, name, None)
            if widget is None:
                orphans.append(f"{name}: MISSING")
            elif not widget.winfo_manager():
                orphans.append(f"{name}: no geometry manager")
        self.assertEqual(orphans, [], "widgets not attached to any parent")

    def test_the_nested_multi_routing_panel_is_reachable(self):
        """It was gridded into an invisible parent, so it never mapped."""
        self.app.routing_frame.update_idletasks()
        self.assertTrue(
            self.app.multi_routing_frame.winfo_manager(),
            "multi_routing_frame is not attached")


if __name__ == "__main__":
    unittest.main()
