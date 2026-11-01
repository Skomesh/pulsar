"""The App Audio tab's layout contract.

The layout was rebuilt and the refuters found that the whole suite
passed identically before and after -- which is why a button that was
squeezed to 1px and unmapped, and therefore invisible and unusable,
went unnoticed. These tests pin the things that must stay true about
the LAYOUT, not about the behaviour the existing tests already cover.

They need a display, so they skip without one.
"""

import os
import tkinter as tk
import tkinter.font as tkfont
import unittest
from tkinter import ttk

from pulsar.ui.main_window import MainWindow


def _have_display():
    return bool(os.environ.get("DISPLAY"))


def _scale_fonts(size):
    """Make the app's fonts bigger, the way a user's system font would.

    This has to run against the app's OWN interpreter: every Tk()
    creates a separate one with its own named fonts.
    """
    for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                  "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                  "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(named).configure(size=size)
        except tk.TclError:
            pass


class _AppCase(unittest.TestCase):
    """Builds the real app once, on the test's own interpreter."""

    FONT_SIZE = None

    def setUp(self):
        self.root = tk.Tk()
        if self.FONT_SIZE is not None:
            _scale_fonts(self.FONT_SIZE)
        self.app = MainWindow(self.root)
        self.root.geometry("800x600")
        self.root.update_idletasks()
        self.root.update()
        self.app.tab_control.select(self.app.capture_tab)
        self.root.update_idletasks()
        self.root.update()
        self.addCleanup(self.root.destroy)

    def named_font(self):
        """The default font OF THIS INTERPRETER."""
        return tkfont.nametofont("TkDefaultFont")


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCaptureTabLayout(_AppCase):
    def test_the_tree_scrollbar_is_actually_visible(self):
        """It used to be 1px wide and unmapped, so it never appeared.

        The tree asks for more width than the cavity when its columns
        are too wide, and packed first -- with expand=True -- it took
        the whole cavity and left the scrollbar nothing.
        """
        children = self.app.capture_tree.master.children
        scrollbars = [w for w in children.values()
                      if isinstance(w, ttk.Scrollbar)]
        self.assertTrue(scrollbars, "the tree has no scrollbar at all")
        for scrollbar in scrollbars:
            self.assertTrue(
                scrollbar.winfo_ismapped(),
                "the tree's scrollbar is not on screen",
            )
            self.assertGreater(
                scrollbar.winfo_width(), 2,
                "the tree's scrollbar was starved to nothing",
            )

    def test_the_tree_fills_the_space_it_has(self):
        """The table is the headline, so it must grow.

        The scrollable-tab helper sized its inner frame to the canvas
        WIDTH and never its height, so a fixed-height tree stayed six
        rows tall however much room there was.
        """
        tree_height = self.app.capture_tree.winfo_height()
        self.assertGreater(
            tree_height, 150,
            f"the table is only {tree_height}px tall -- it is not "
            f"filling the page",
        )

    def test_no_column_is_narrower_than_its_heading(self):
        """A clipped heading cannot be read back: a Treeview has no
        horizontal scrollbar.

        The widths used to be hard-coded, so at a 12pt font the
        "PW Node ID" heading needed 96px in an 84px column and was cut
        off at every window size. The large-font class checks the same
        thing where it actually bites.
        """
        tree = self.app.capture_tree
        for column in tree["columns"]:
            heading = tree.heading(column)["text"]
            needed = self.named_font().measure(heading)
            width = tree.column(column, "width")
            self.assertGreaterEqual(
                width, needed,
                f"the {heading!r} column is {width}px but its heading "
                f"needs {needed}px",
            )

    def test_the_output_list_is_beside_the_buttons(self):
        """They share a row, so the user can see what they act on."""
        button = self.app.capture_route_button
        listing = self.app.capture_route_listbox
        self.assertLess(
            listing.winfo_rootx(), button.winfo_rootx(),
            "the output list is not to the left of the buttons",
        )
        self.assertLess(
            abs(listing.winfo_rooty() - button.winfo_rooty()), 80,
            "the output list and the buttons are not on the same row",
        )

    def test_the_text_is_proportional_not_monospace(self):
        """A font tuple's first element is a FAMILY, not a named font.

        ("TkDefaultFont", 9) asks for a family called "TkDefaultFont",
        does not find one, and silently falls back to a fixed-width
        face -- so the label rendered monospace and nobody noticed.
        """
        label = self._status_line()
        self.assertIsNotNone(label, "the status line label was not found")
        font = tkfont.Font(font=label.cget("font"))
        self.assertNotEqual(
            font.measure("i"), font.measure("W"),
            "the status line renders in a fixed-width face",
        )

    def _status_line(self):
        wanted = str(self.app.capture_selected_var)

        def find(widget):
            for child in widget.winfo_children():
                if (isinstance(child, (tk.Label, ttk.Label))
                        and str(child.cget("textvariable")) == wanted):
                    return child
                found = find(child)
                if found is not None:
                    return found
            return None

        return find(self.app.capture_tab)


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCaptureTabLayoutLargeFont(_AppCase):
    """The same tab, with the user's font set to 16pt.

    A hard-coded 9pt label is 9pt whatever the user's font is, and an
    output list that asks for more width than there is squeezes
    whatever is packed after it.
    """

    FONT_SIZE = 20

    def test_the_routing_buttons_are_never_squeezed_away(self):
        """At a 20pt font the output list took the row and the buttons
        went to 1px -- unmapped, so "Remove Routing" was not on screen.
        """
        for name in ("capture_route_button",
                     "capture_route_remove_button",
                     "capture_route_custom_button"):
            button = getattr(self.app, name)
            self.assertTrue(
                button.winfo_ismapped(),
                f"{name} is not on screen at a {self.FONT_SIZE}pt font",
            )
            self.assertGreater(
                button.winfo_width(), 60,
                f"{name} was squeezed to {button.winfo_width()}px",
            )

    def test_no_column_is_narrower_than_its_heading(self):
        """The same check at a large font, where it actually bites.

        At the default font a hard-coded 84px column still fits the
        heading. At 20pt the heading needs 110px, so a hard-coded
        width clips it at every window size, and a Treeview has no
        horizontal scrollbar to recover it.
        """
        tree = self.app.capture_tree
        for column in tree["columns"]:
            heading = tree.heading(column)["text"]
            needed = self.named_font().measure(heading)
            width = tree.column(column, "width")
            self.assertGreaterEqual(
                width, needed,
                f"the {heading!r} column is {width}px but its heading "
                f"needs {needed}px at a {self.FONT_SIZE}pt font",
            )

    def test_the_styled_fonts_track_the_users_font(self):
        """The styled widgets must not stay at a hard-coded size."""
        named = self.named_font()
        default_size = named.actual("size")
        style = ttk.Style()
        for name in ("Pulsar.Primary.TButton", "Pulsar.Heading.TLabel"):
            font = style.lookup(name, "font")
            self.assertIsNotNone(font, f"{name} has no font")
            # Both sides are read with `actual("size")`, which is in
            # points, so the points/pixels ambiguity does not apply.
            size = tkfont.Font(font=font).actual("size")
            self.assertGreaterEqual(
                size, default_size,
                f"{name} is {size}pt while the default font is "
                f"{default_size}pt -- it is not tracking the user's "
                f"font",
            )


if __name__ == "__main__":
    unittest.main()
