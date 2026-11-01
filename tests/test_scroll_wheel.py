"""The wheel must scroll one thing at a time.

A wheel press over a tree inside a scrollable tab scrolled the tree
AND jumped the page -- so the user scrolling through their devices
watched the page slide out from under them. The page also scrolled
once per ancestor that carried the wheel binding, so one press moved
two lines.

Tk's binding order is what makes this happen: for an event on a
widget, the widget's own binding runs, then its class binding, then
the bindings of its ancestors. A Treeview's class binding scrolls the
tree; the event then bubbles up to the app's handler on the tab's
inner frame, which scrolled the page as well.

The fix has two parts: a widget that scrolls itself is not given the
app's wheel binding at all, and the decision to scroll the page lives
in one pure function, _wheel_scrolls_page, so it can be tested
without a pointer.

They need a display, so they skip without one.
"""

import os
import tkinter as tk
import unittest
from tkinter import ttk

from pulsar.ui.main_window import (
    MainWindow,
    _at_scroll_end,
    _handle_wheel,
    _scrolls_itself,
    _wheel_direction,
    _wheel_scrolls_page,
)


def _have_display():
    return bool(os.environ.get("DISPLAY"))


class _FakeEvent:
    """What the handler sees: the widget the press started on."""

    def __init__(self, widget, num=0, delta=0):
        self.widget = widget
        self.num = num
        self.delta = delta


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestScrollWheel(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.app = MainWindow(self.root)
        self.root.geometry("800x600")
        self.root.update_idletasks()
        self.root.update()
        self.app.tab_control.select(self.app.manage_tab)
        self.root.update_idletasks()
        self.root.update()
        self.addCleanup(self.root.destroy)

        self.canvas = None
        for child in self.app.manage_tab.winfo_children():
            if child.winfo_class() == "Canvas":
                self.canvas = child
        self.assertIsNotNone(self.canvas, "the manage tab is not scrollable")
        self.inner = self.canvas.winfo_children()[0]

    def _fill_tree(self, tree, rows=80):
        for i in range(rows):
            tree.insert("", "end", text=f"row {i}", values=(str(i), "sink", f"d{i}"))
        self.root.update_idletasks()

    # -- the structure ------------------------------------------------

    def test_a_self_scrolling_widget_is_not_given_the_binding(self):
        """Otherwise the page and the widget fight over the wheel."""
        for name in ("unified_tree", "multi_routing_listbox"):
            widget = getattr(self.app, name)
            self.assertFalse(
                widget.bind("<Button-5>"),
                f"{name} scrolls itself, so the app must not bind the wheel "
                f"to it as well",
            )

    def test_the_inner_frame_still_has_the_binding(self):
        """It is the one that receives the bubbled event."""
        self.assertTrue(self.inner.bind("<Button-5>"))

    def test_a_plain_widget_has_the_binding(self):
        label = self.inner.winfo_children()[0]
        self.assertTrue(label.bind("<Button-5>"))

    # -- the behaviour, driven through the real handler ---------------

    def test_a_wheel_over_a_plain_widget_scrolls_the_page(self):
        label = self.inner.winfo_children()[0]
        before = self.canvas.yview()
        result = _handle_wheel(self.canvas, _FakeEvent(label, num=5))
        self.root.update_idletasks()
        self.assertNotEqual(
            self.canvas.yview(), before, "the page did not scroll"
        )
        self.assertEqual(result, "break")

    def test_a_wheel_over_a_tree_does_not_scroll_the_page(self):
        """The tree has the wheel. The page must stay put.

        The tree is scrolled to the middle so that it has room in BOTH
        directions -- at an end it hands the wheel on, which is the
        other two tests.
        """
        tree = self.app.unified_tree
        self._fill_tree(tree)
        tree.yview_moveto(0.5)
        self.root.update_idletasks()
        before = self.canvas.yview()
        result = _handle_wheel(self.canvas, _FakeEvent(tree, num=5))
        self.root.update_idletasks()
        self.assertEqual(
            self.canvas.yview(), before,
            "the page scrolled while the wheel belonged to the tree",
        )
        self.assertEqual(result, "break")

    def test_a_wheel_over_a_tree_at_its_end_scrolls_the_page(self):
        """Otherwise the user is stuck: the tree has nothing left and
        the page will not move either."""
        tree = self.app.unified_tree
        self._fill_tree(tree)
        tree.yview_moveto(1.0)
        self.root.update_idletasks()
        before = self.canvas.yview()
        _handle_wheel(self.canvas, _FakeEvent(tree, num=5))
        self.root.update_idletasks()
        self.assertNotEqual(
            self.canvas.yview(), before,
            "the page did not take the wheel from a tree that was at its end",
        )

    def test_the_handler_always_breaks(self):
        """Without "break" the event bubbles on and the page scrolls
        once per ancestor that carries the binding."""
        label = self.inner.winfo_children()[0]
        self.assertEqual(
            _handle_wheel(self.canvas, _FakeEvent(label, num=5)), "break"
        )
        self.assertEqual(
            _handle_wheel(self.canvas, _FakeEvent(label, num=4)), "break"
        )
        self.assertEqual(
            _handle_wheel(self.canvas, _FakeEvent(label, num=3)), "break"
        )
        self.assertEqual(
            _handle_wheel(self.canvas, _FakeEvent(None, num=5)), "break"
        )

    # -- the decision -------------------------------------------------

    def test_the_decision_agrees_with_the_handler(self):
        label = self.inner.winfo_children()[0]
        self.assertTrue(_wheel_scrolls_page(label, 1))
        self.assertTrue(_wheel_scrolls_page(label, -1))

    def test_a_wheel_up_over_a_tree_at_the_top_scrolls_the_page(self):
        tree = self.app.unified_tree
        self._fill_tree(tree)
        tree.yview_moveto(0.0)
        self.root.update_idletasks()
        self.assertTrue(
            _wheel_scrolls_page(tree, -1),
            "the page did not take the wheel from a tree that was at its top",
        )

    def test_no_direction_scrolls_nothing(self):
        label = self.inner.winfo_children()[0]
        self.assertFalse(_wheel_scrolls_page(label, 0))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestWheelHelpers(unittest.TestCase):
    """The decisions the handler makes, tested on their own."""

    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)

    def test_a_tree_scrolls_itself(self):
        self.assertTrue(_scrolls_itself(ttk.Treeview(self.root)))
        self.assertTrue(_scrolls_itself(tk.Listbox(self.root)))
        self.assertTrue(_scrolls_itself(tk.Text(self.root)))

    def test_a_label_does_not(self):
        self.assertFalse(_scrolls_itself(ttk.Label(self.root)))
        self.assertFalse(_scrolls_itself(ttk.Frame(self.root)))
        self.assertFalse(_scrolls_itself(None))

    def test_the_wheel_direction(self):
        self.assertEqual(_wheel_direction(_FakeEvent(None, num=5)), 1)
        self.assertEqual(_wheel_direction(_FakeEvent(None, num=4)), -1)
        self.assertEqual(_wheel_direction(_FakeEvent(None, num=3)), 0)

    def test_a_trackpad_delta_is_a_direction_too(self):
        # A positive delta is a scroll up, the convention the handler
        # has always used.
        self.assertEqual(_wheel_direction(_FakeEvent(None, delta=120)), -1)
        self.assertEqual(_wheel_direction(_FakeEvent(None, delta=-120)), 1)
        self.assertEqual(_wheel_direction(_FakeEvent(None, delta=0)), 0)

    def test_at_scroll_end(self):
        tree = ttk.Treeview(self.root)
        # The tree has to be on screen: an unmapped tree has no height,
        # so everything is visible and it always looks like it is at
        # the end of its scroll.
        tree.pack()
        for i in range(40):
            tree.insert("", "end", text=f"row {i}")
        self.root.update_idletasks()
        self.root.update()
        tree.yview_moveto(0.0)
        self.root.update_idletasks()
        self.assertTrue(_at_scroll_end(tree, -1), "the top is the end, upwards")
        self.assertFalse(_at_scroll_end(tree, 1))
        tree.yview_moveto(1.0)
        self.root.update_idletasks()
        self.assertTrue(_at_scroll_end(tree, 1), "the bottom is the end, downwards")
        self.assertFalse(_at_scroll_end(tree, -1))
