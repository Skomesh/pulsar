"""
The Route button's state, driven by REAL Tk widgets.

The button must be on only when BOTH an app and an output are chosen,
and it must come on whichever order the user picks them in. This is
tested against real widgets rather than stubs because the binding and
the event delivery are Tk's business -- a stub proves nothing about
whether `<<ListboxSelect>>` actually fires.

The Capture tab's app list and output list are independent controls, so
both orders are natural:

    pick output, then app      pick app, then output

Both must end with the button enabled.
"""

import tkinter as tk
import unittest

from pulsar.ui.main_window import MainWindow


class TestRouteButtonState(unittest.TestCase):
    """Real Tk widgets, real event delivery."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:  # pragma: no cover - no display
            raise unittest.SkipTest(f"no display available: {e}") from e
        # The window must be MAPPED. Tk does not deliver
        # <<ListboxSelect>> to an unmapped widget, so a withdrawn root
        # would silently skip the binding and the test would prove
        # nothing about the real behaviour.
        cls.root.geometry("200x120+0+0")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def setUp(self):
        self._windows = []

    def _window(self):
        win = object.__new__(MainWindow)
        win.root = self.root
        win.capture_route_listbox = tk.Listbox(
            win.root, selectmode=tk.EXTENDED, exportselection=False
        )
        # The same binding setup_capture_tab installs. Without it the
        # event has nothing to call, and the test would prove nothing
        # about the real widget's behaviour.
        win.capture_route_listbox.bind(
            "<<ListboxSelect>>", win._on_capture_route_selection_changed
        )
        # The widget must be PACKED, so it is mapped and receives the
        # selection event.
        win.capture_route_listbox.pack()
        win.capture_route_button = _FakeButton()
        win.capture_selected_var = tk.StringVar()
        win._capture_nodes = [{
            "id": 63,
            "application_name": "firefox",
            "media_class": "Stream/Output/Audio",
            "node_name": "Firefox",
            "pid": 123,
            "sink_name": "speakers",
        }]
        win.capture_tree = _FakeTree()
        self._windows.append(win)
        return win

    def tearDown(self):
        # Each test packs a listbox into the shared root. Leaving them
        # all mapped means an earlier test's listbox can still be the
        # one that receives an event, so the tests would depend on
        # each other's order.
        for win in self._windows:
            try:
                win.capture_route_listbox.destroy()
            except tk.TclError:
                pass
        self._windows = []

    def _pick_output(self, win, index=0):
        win.capture_route_listbox.selection_clear(0, tk.END)
        win.capture_route_listbox.selection_set(index)
        win.capture_route_listbox.event_generate("<<ListboxSelect>>")
        self.root.update()

    def _pick_app(self, win):
        # The real widget fires <<TreeviewSelect>>; here the handler is
        # called directly, which is what that binding does.
        win._on_capture_tree_select()
        self.root.update()

    def _state(self, win):
        return win.capture_route_button.state

    def test_picking_the_output_then_the_app_enables_the_button(self):
        win = self._window()
        win.capture_route_listbox.insert(tk.END, "speakers")
        win.capture_route_listbox.insert(tk.END, "headphones")

        self._pick_output(win)
        self.assertEqual(
            self._state(win), "disabled",
            "no app chosen yet, so the button must be off",
        )

        self._pick_app(win)
        self.assertEqual(
            self._state(win), "normal",
            "an app AND an output are chosen, so the button must be on",
        )

    def test_picking_the_app_then_the_output_enables_the_button(self):
        win = self._window()
        win.capture_route_listbox.insert(tk.END, "speakers")

        self._pick_app(win)
        self.assertEqual(
            self._state(win), "disabled",
            "no output chosen yet, so the button must be off",
        )

        self._pick_output(win)
        self.assertEqual(
            self._state(win), "normal",
            "an app AND an output are chosen, so the button must be on",
        )

    def test_clearing_the_output_disables_the_button_again(self):
        win = self._window()
        win.capture_route_listbox.insert(tk.END, "speakers")
        self._pick_output(win)
        self._pick_app(win)
        self.assertEqual(self._state(win), "normal")

        win.capture_route_listbox.selection_clear(0, tk.END)
        win.capture_route_listbox.event_generate("<<ListboxSelect>>")
        self.root.update()
        self.assertEqual(
            self._state(win), "disabled",
            "no output chosen any more, so the button must go off",
        )


class _FakeButton:
    """Records the last state it was given."""

    def __init__(self):
        self.state = "disabled"

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class _FakeTree:
    """Just enough for the selection lookup."""

    def selection(self):
        return ("row0",)

    def item(self, _i):
        return {"values": (63, "firefox (pid 123)",
                           "Stream/Output/Audio", "Firefox")}

    def index(self, _i):
        return 0


if __name__ == "__main__":
    unittest.main()
