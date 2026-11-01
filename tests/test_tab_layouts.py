"""The layout contract for the Create, Manage and Diagnostics tabs.

The App Audio tab was rebuilt and the refuters found that the whole
suite passed identically before and after the rebuild -- which is how
a button that was squeezed to 1px and unmapped went unnoticed. These
tabs were rebuilt for the same reason, so the same thing is pinned
here: what must stay true about the LAYOUT.

Three of the things pinned here were actual defects:

  * The Manage tab's tree was packed with expand=True and took every
    spare pixel, which pushed the whole Multi-output Routing panel
    below the fold. The routing controls were invisible without
    scrolling.
  * The save and delete preset buttons were labelled with emoji, and
    an emoji font is not always installed, so on a system without one
    they rendered as a pair of EMPTY BOXES with no label at all.
  * The one-line sections were boxed. A box around one line of text
    says nothing the text does not already say, and costs padding.

They need a display, so they skip without one.
"""

import os
import re
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

from pulsar.ui.main_window import MainWindow

MAIN_WINDOW = Path(__file__).resolve().parent.parent / "src" / "pulsar" / "ui" / "main_window.py"

# Emoji, as opposed to the dingbats (✓ ✗ ⚠) which render in every
# default font. These are the blocks Tk will not fall back to.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F900-\U0001F9FF]"
)


def _have_display():
    return bool(os.environ.get("DISPLAY"))


def _canvas_of(tab):
    """The canvas and its inner frame, for a tab built by the helper."""
    for child in tab.winfo_children():
        if child.winfo_class() == "Canvas":
            inner = child.winfo_children()[0]
            return child, inner
    return None, None


class _AppCase(unittest.TestCase):
    """Builds the real app once, on the test's own interpreter."""

    TAB = None

    def setUp(self):
        self.root = tk.Tk()
        self.app = MainWindow(self.root)
        self.root.geometry("800x600")
        self.root.update_idletasks()
        self.root.update()
        if self.TAB is not None:
            self.app.tab_control.select(getattr(self.app, self.TAB))
            self.root.update_idletasks()
            self.root.update()
        self.addCleanup(self.root.destroy)

    def canvas_and_inner(self, tab):
        canvas, inner = _canvas_of(tab)
        self.assertIsNotNone(canvas, "the tab is not inside a scrollable canvas")
        return canvas, inner

    def bottom_inside_canvas(self, widget, canvas, inner):
        """Where the widget's bottom edge is, relative to the canvas."""
        top = widget.winfo_rooty() - inner.winfo_rooty()
        return top + widget.winfo_height()


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestManageTabLayout(_AppCase):
    TAB = "manage_tab"

    def test_the_routing_panel_is_above_the_fold(self):
        """It used to be below the fold, so the routing controls were
        invisible without scrolling.

        The tree was packed with expand=True and took every spare
        pixel of the tab. The panel -- its listbox and both of its
        buttons -- ended up under the bottom edge of the window.
        """
        canvas, inner = self.canvas_and_inner(self.app.manage_tab)
        panel = self.app.multi_routing_frame
        bottom = self.bottom_inside_canvas(panel, canvas, inner)
        self.assertLessEqual(
            bottom, canvas.winfo_height(),
            f"the routing panel ends at {bottom} but the viewport is only "
            f"{canvas.winfo_height()} -- it is below the fold",
        )

    def test_the_routing_buttons_are_mapped_and_visible(self):
        """A control that is built but not mapped cannot be clicked."""
        for name in ("multi_apply_button", "multi_stop_all_button"):
            button = getattr(self.app, name)
            self.assertTrue(
                button.winfo_ismapped(), f"{name} is not on the screen"
            )
            self.assertGreater(
                button.winfo_width(), 1,
                f"{name} was squeezed to {button.winfo_width()}px",
            )

    def test_the_routing_listbox_is_mapped(self):
        self.assertTrue(self.app.multi_routing_listbox.winfo_ismapped())

    def test_the_path_is_not_in_a_box(self):
        """One line of text and one line of path did not need a frame."""
        self.assertNotIsInstance(
            self.app.data_path_entry.master, ttk.LabelFrame,
            "the presets path is still inside a boxed section",
        )

    def test_the_routing_panel_is_not_a_box(self):
        self.assertNotIsInstance(
            self.app.multi_routing_frame, ttk.LabelFrame,
            "the routing panel is still a boxed section",
        )


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCreateTabLayout(_AppCase):
    TAB = "create_tab"

    def test_the_preset_buttons_have_readable_labels(self):
        """They were the 💾 and 🗑️ emoji.

        An emoji font is not always installed and Tk does not fall back
        to one, so on a system without it the two buttons rendered as a
        pair of empty boxes with no label at all.
        """
        for name in ("save_preset_btn", "delete_preset_btn"):
            label = getattr(self.app, name).cget("text")
            self.assertTrue(
                label.strip(), f"{name} has no label at all"
            )
            self.assertIsNone(
                EMOJI.search(label),
                f"{name} is labelled with an emoji: {label!r}",
            )
            self.assertGreaterEqual(
                len(label.strip()), 3,
                f"{name} is labelled {label!r}, which says nothing",
            )

    def test_the_heading_uses_the_derived_style(self):
        """So it grows with the user's font instead of being fixed."""
        heading = self.app.create_button.master
        labels = [
            child for child in heading.winfo_children()
            if isinstance(child, ttk.Label)
        ]
        self.assertTrue(labels, "the create tab has no heading label")
        style = ttk.Style()
        for label in labels:
            if label.cget("text") == "Create a new virtual audio device":
                self.assertEqual(
                    label.cget("style"), "Pulsar.Heading.TLabel",
                    "the heading is not using the derived style",
                )
                self.assertTrue(style.lookup("Pulsar.Heading.TLabel", "font"))

    def test_the_tab_fits_without_scrolling(self):
        canvas, inner = self.canvas_and_inner(self.app.create_tab)
        self.assertLessEqual(
            inner.winfo_reqheight(), canvas.winfo_height(),
            "the create tab no longer fits in the window",
        )


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestDiagnosticsTabLayout(_AppCase):
    TAB = "diagnostics_tab"

    def _label_named(self, text):
        """Find a label by its text, anywhere in the diagnostics tab."""
        canvas, inner = self.canvas_and_inner(self.app.diagnostics_tab)

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label):
                    try:
                        if child.cget("text") == text:
                            return child
                    except tk.TclError:
                        pass
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(inner)

    def test_the_one_line_sections_are_not_boxed(self):
        """One line of text did not need a frame around it."""
        for text in ("Health Summary", "Sample Rate Audit"):
            label = self._label_named(text)
            self.assertIsNotNone(label, f"no label reading {text!r}")
            self.assertNotIsInstance(
                label.master, ttk.LabelFrame,
                f"{text!r} is still inside a boxed section",
            )

    def test_the_section_headings_use_the_derived_style(self):
        style = ttk.Style()
        self.assertTrue(style.lookup("Pulsar.Section.TLabel", "font"))

    def test_the_tab_fits_without_scrolling(self):
        canvas, inner = self.canvas_and_inner(self.app.diagnostics_tab)
        self.assertLessEqual(
            inner.winfo_reqheight(), canvas.winfo_height(),
            "the diagnostics tab no longer fits in the window",
        )


class TestNoEmojiInTheUi(unittest.TestCase):
    """No emoji anywhere in the UI source.

    This one is a source scan rather than a widget check, because the
    emoji that mattered were in labels the tests do not reach -- the
    hardware category names in the tree, which are built from a dict.
    """

    def test_the_main_window_has_no_emoji(self):
        source = MAIN_WINDOW.read_text(encoding="utf-8")
        found = EMOJI.findall(source)
        self.assertEqual(
            found, [],
            f"the UI source contains emoji {found!r}; they render as empty "
            f"boxes on a system without an emoji font",
        )
