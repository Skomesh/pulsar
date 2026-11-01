"""
The Multi-output Routing buttons were never enabled.

`update_routing_panel` used to enable them, as a side effect of
populating the routing panel. It was deleted in bc4a0ec ("ui: remove
the redundant single-output routing panel") along with the single-output
panel it also drove, and nothing replaced it. The buttons were created
with `state="disabled"` and stayed that way:

    $ grep -n multi_apply_button src/pulsar/ui/main_window.py
    1097:        self.multi_apply_button = ttk.Button(
    1103:        self.multi_routing_listbox.pack(...)   # (the button's pack)

Two mentions, both in the constructor. There was no code path anywhere
that set the state to "normal", so "Apply to Selected" was greyed out
forever.

The user sees a panel that looks completely ready -- the listbox is
populated with their hardware outputs -- and a button that cannot be
pressed. Selecting a virtual sink and finding the route button dead
gives no hint that the button is the problem.

These tests drive the real widgets through the real selection handler,
because the defect is in the wiring between the handler and the button,
not in either of them alone.

    DISPLAY=:99 .venv/bin/python -m pytest tests/test_multi_routing_buttons.py -q
"""

import os
import tkinter as tk
import unittest
from unittest import mock

from pulsar.utils.pactl_runner import PactlRunner


def _have_display():
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestMultiRoutingButtons(unittest.TestCase):
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

    def setUp(self):
        # Mocked rather than read from the machine: the real hardware
        # list depends on what the host happens to have, and this box has
        # none visible, which would make the button permanently disabled
        # for a reason that has nothing to do with the defect.
        self.hw_outputs = ["speakers", "headphones"]
        self.loopbacks = []
        patcher_hw = mock.patch.object(
            PactlRunner, "list_hardware_outputs",
            staticmethod(lambda *a, **kw: list(self.hw_outputs)))
        patcher_lb = mock.patch.object(
            PactlRunner, "list_loopbacks",
            staticmethod(lambda *a, **kw: list(self.loopbacks)))
        patcher_hw.start()
        patcher_lb.start()
        self.addCleanup(patcher_hw.stop)
        self.addCleanup(patcher_lb.stop)
        # Start from a known state: nothing selected.
        self.app._routing_sink_name = None

    def _select(self, entity_type, entity_name, is_null=True):
        """Drive on_unified_tree_select the way a click does.

        The handler reads values out of the tree itself, so a real row
        has to be inserted -- faking selection() alone fails with
        "Item not found".
        """
        tree = self.app.unified_tree
        item_id = tree.insert("", tk.END, values=["30", entity_type, entity_name])

        def fake_selection(*a, **kw):
            return (item_id,)

        def fake_is_null_sink(name, *a, **kw):
            return bool(is_null) and entity_type == "sink"

        with mock.patch.object(type(tree), "selection", fake_selection), \
                mock.patch.object(
                    PactlRunner, "is_null_sink", staticmethod(fake_is_null_sink)):
            self.app.on_unified_tree_select(None)
            self.root.update_idletasks()
            self.root.update()
        tree.delete(item_id)

    def _state(self, button_name):
        return str(getattr(self.app, button_name).cget("state"))

    # ---------------------------------------------------------------
    # The regression itself
    # ---------------------------------------------------------------

    def test_apply_button_is_enabled_when_a_virtual_sink_is_selected(self):
        """A virtual sink plus a hardware output is a routable combination.

        The button has to be pressable in exactly that case, or the
        feature does not exist.
        """
        self._select("sink", "pulsar_test_sink", is_null=True)
        self.assertEqual(
            self._state("multi_apply_button"), "normal",
            "'Apply to Selected' stayed greyed out, so a virtual sink "
            "could not be routed to hardware at all")

    def test_apply_button_is_disabled_when_a_hardware_sink_is_selected(self):
        """A hardware sink is already an output; there is nothing to route."""
        self._select("sink", "alsa_output.pci-0000_00_1f.3.analog-stereo",
                     is_null=False)
        self.assertEqual(self._state("multi_apply_button"), "disabled")

    def test_apply_button_is_disabled_when_a_source_is_selected(self):
        """A source is an input, not a routing source."""
        self._select("source", "alsa_input.analog-stereo")
        self.assertEqual(self._state("multi_apply_button"), "disabled")

    def test_apply_button_is_disabled_when_nothing_is_selected(self):
        """No sink selected means no route to create."""
        self.app._routing_sink_name = None
        self.app._update_multi_routing_buttons()
        self.assertEqual(self._state("multi_apply_button"), "disabled")

    def test_apply_button_is_disabled_when_there_are_no_hardware_outputs(self):
        """Nothing to route TO, so there is nothing to apply."""
        self.hw_outputs = []
        self._select("sink", "pulsar_test_sink", is_null=True)
        self.assertEqual(self._state("multi_apply_button"), "disabled")

    # ---------------------------------------------------------------
    # Stop All Routing
    # ---------------------------------------------------------------

    def test_stop_all_button_is_disabled_when_the_sink_has_no_routes(self):
        """Nothing is routed, so there is nothing to stop."""
        self.loopbacks = []
        self._select("sink", "pulsar_test_sink", is_null=True)
        self.assertEqual(self._state("multi_stop_all_button"), "disabled")

    def test_stop_all_button_is_enabled_when_the_sink_has_a_route(self):
        """A sink that is routed can be un-routed."""
        self.loopbacks = [{"id": "42", "source": "pulsar_test_sink.monitor",
                           "sink": "speakers"}]
        self._select("sink", "pulsar_test_sink", is_null=True)
        self.assertEqual(self._state("multi_stop_all_button"), "normal")

    def test_stop_all_button_is_disabled_for_a_route_of_another_sink(self):
        """The route must belong to the selected sink."""
        self.loopbacks = [{"id": "42", "source": "some_other_sink.monitor",
                           "sink": "speakers"}]
        self._select("sink", "pulsar_test_sink", is_null=True)
        self.assertEqual(self._state("multi_stop_all_button"), "disabled")

    # ---------------------------------------------------------------
    # The checkmarks have to follow the selection too
    # ---------------------------------------------------------------

    def test_the_checkmarks_follow_the_selected_sink(self):
        """Switching sinks must not carry the old sink's targets over.

        This only became reachable once the Apply button was enabled:
        with stale checkmarks, pressing it would route the NEW sink to
        the OLD sink's targets.
        """
        self.loopbacks = [{"id": "42", "source": "first_sink.monitor",
                           "sink": "speakers"}]
        self._select("sink", "first_sink", is_null=True)
        self.assertEqual(
            list(self.app.multi_routing_listbox.curselection()), [0],
            "the first sink's route should be pre-checked")

        self.loopbacks = [{"id": "43", "source": "second_sink.monitor",
                           "sink": "headphones"}]
        self._select("sink", "second_sink", is_null=True)
        self.assertEqual(
            list(self.app.multi_routing_listbox.curselection()), [1],
            "the second sink's route should be pre-checked, not the "
            "first sink's")


if __name__ == "__main__":
    unittest.main()
