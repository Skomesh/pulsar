"""Does multi-output routing still work now that update_routing_panel is gone?

Deleting the single-output panel meant deleting update_routing_panel,
which used to be what SET _routing_sink_name. Two multi-routing
functions read that attribute:

    apply_multi_routing
    stop_all_routing_for_selected

They now depend on on_unified_tree_select setting it directly, which is
a much weaker guarantee than "a function that refreshes the panel ran".
Nothing else would catch a regression here: the deleted panel's own tests
are gone, and the multi-routing tests construct the object directly
without going through a tree selection.

So this drives the real handler against the real app object and asserts
the attribute multi-routing depends on is correct afterwards.
"""

import os
import time
import tkinter as tk
import unittest
from unittest import mock

from pulsar.utils.pactl_runner import PactlRunner


def _have_display():
    return bool(os.environ.get("DISPLAY"))


def _a_real_item_id(tree):
    """Deprecated helper -- the handler reads values out of the tree itself."""
    raise NotImplementedError


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestSelectionFeedsMultiRouting(unittest.TestCase):
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

    def _select(self, entity_id, entity_type, entity_name, is_null=True):
        """Drive on_unified_tree_select the way a click does.

        is_null_sink is stubbed: the question here is whether the handler
        routes the selection into _routing_sink_name, not whether pactl
        can find a device called this.
        """
        tree = self.app.unified_tree
        # The handler does tree.item(selected[0])['values'], so the values
        # have to live in the tree. Inserting a real row is the honest way
        # to drive it -- faking selection() alone is not enough.
        item_id = tree.insert("", tk.END, values=["30", entity_type, entity_name])

        def fake_selection(*a, **kw):
            return (item_id,)

        def fake_is_null_sink(name, *a, **kw):
            return bool(is_null) and entity_type == "sink"

        # PactlRunner is patched by object, not by string path: is_null_sink
        # is a staticmethod and mock refuses to resolve one that way.
        with mock.patch.object(type(tree), "selection", fake_selection), \
                mock.patch.object(
                    PactlRunner, "is_null_sink", staticmethod(fake_is_null_sink)):
            self.app.on_unified_tree_select(None)
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.1)
        tree.delete(item_id)

    def test_selecting_a_virtual_sink_sets_the_routing_sink(self):
        """The attribute apply_multi_routing reads must be populated."""
        self.app._routing_sink_name = None
        self._select("30", "sink", "pulsar_test_sink")
        self.assertEqual(
            self.app._routing_sink_name, "pulsar_test_sink",
            "multi-output routing would silently no-op without this")

    def test_selecting_a_hardware_sink_clears_it(self):
        """A hardware sink is not routeable, so it must not be treated as one."""
        self._select("12", "sink", "alsa_output.pci-0000_00_1f.3.analog-stereo",
                   is_null=False)
        self.assertIsNone(self.app._routing_sink_name)

    def test_selecting_a_source_clears_it(self):
        self._select("20", "source", "alsa_input.analog-stereo")
        self.assertIsNone(self.app._routing_sink_name)

    def test_the_single_output_attributes_are_really_gone(self):
        """Not merely unused -- deleted. A leftover would be dead UI."""
        for attr in ("routing_status_var", "routing_output_var",
                     "routing_output_combo", "route_button",
                     "stop_route_button"):
            self.assertFalse(
                hasattr(self.app, attr),
                f"{attr} should have been removed with the panel")

    def test_the_deleted_handlers_are_really_gone(self):
        for method in ("apply_routing", "stop_routing", "update_routing_panel"):
            self.assertFalse(
                hasattr(self.app, method),
                f"{method} should have been removed with the panel")

    def test_multi_output_is_still_present(self):
        """The replacement has to actually be there."""
        self.assertTrue(hasattr(self.app, "multi_routing_listbox"))
        self.assertTrue(callable(getattr(self.app, "apply_multi_routing", None)))


if __name__ == "__main__":
    unittest.main()
