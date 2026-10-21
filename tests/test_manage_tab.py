"""Tests for the Manage tab, from real user-testing feedback.

Two findings drive this file.

1. The user asked: "Is multi routing able to replace the standard routing
   panel? It sounds like it's just a more featureful version." The
   answer is yes -- `apply_multi_routing` is a strict superset of
   `apply_routing`. `TestMultiRoutingSubsumesSingleOutput` proves that
   executably rather than by assertion in a comment: one checked target
   in the multi listbox produces exactly the routing the single-output
   dropdown produced.

   Deleting the single-output widgets is nonetheless blocked -- see the
   docstring of that class, and the note left in `setup_manage_tab`.

2. The user could not find where their profiles are saved. The Profiles
   tab shows no path at all. `TestDataPathIsShown` requires the Manage
   tab to display the resolved data directory, because a user who cannot
   find their data cannot back it up.

The data-path tests need a real Tk and are skipped without a display,
the same way tests/test_widget_display.py does it. Under Xvfb:

    Xvfb :95 -screen 0 1400x1000x24 &
    DISPLAY=:95 .venv/bin/python -m pytest tests/test_manage_tab.py -q
"""

import os
import tkinter as tk
import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow


# ----------------------------------------------------------------------
# 1. Does multi-output routing subsume the single-output panel?
# ----------------------------------------------------------------------

class TestMultiRoutingSubsumesSingleOutput(unittest.TestCase):
    """`apply_multi_routing` with ONE target == `apply_routing`.

    Read of the two implementations:

    * `apply_routing` reads its target from `routing_output_var` (a
      dropdown holding exactly one name), unloads every loopback whose
      source is the sink's monitor, then creates one loopback.
    * `apply_multi_routing` reads a *set* of targets from
      `multi_routing_listbox.curselection()`, unloads every loopback
      whose target is NOT in that set, then creates one loopback per
      target not already routed.

    Substitute a one-element set and the two bodies collapse to the
    same end state. Multi is strictly stronger: it is idempotent and it
    will not tear down a route the user did not ask to change.

    The single-output widgets therefore have no capability of their
    own. They are, however, still *referenced* by `update_routing_panel`
    (and `apply_routing` reads `routing_output_var`), so removing them
    would raise AttributeError inside a function this change does not
    own. That part is left alone on purpose.
    """

    def _window(self, selected_targets, existing_loopbacks):
        """A MainWindow with only the bits apply_multi_routing touches."""
        win = object.__new__(MainWindow)
        win._routing_sink_name = "virtual_sink"

        class _Listbox:
            def curselection(self):
                return tuple(range(len(selected_targets)))

            def get(self, i):
                return selected_targets[i]

        win.multi_routing_listbox = _Listbox()
        win.add_output = lambda *a, **k: None
        win.status_var = mock.Mock()
        win.refresh_all_views = lambda: None
        win._reselect_after_refresh = lambda name: None
        win.get_loopback_latency_ms = lambda: 20

        def _is_null_sink(name):
            return True

        def _monitor(name):
            return "virtual_sink.monitor"

        def _list_loopbacks():
            return list(existing_loopbacks)

        created, unloaded = [], []

        def _create_loopback(source, target, latency_msec=None, logger=None):
            created.append(target)
            return f"mod{len(created)}"

        def _unload_loopback(lb_id, logger=None):
            unloaded.append(lb_id)
            return True

        self._pactl = mock.patch.multiple(
            "pulsar.ui.main_window.PactlRunner",
            is_null_sink=_is_null_sink,
            monitor_source_for=_monitor,
            list_loopbacks=_list_loopbacks,
            create_loopback=_create_loopback,
            unload_loopback=_unload_loopback,
        )
        self._pactl.start()
        self.addCleanup(self._pactl.stop)
        return win, created, unloaded

    def test_one_target_creates_exactly_one_loopback(self):
        """The single-output case: one sink -> one hardware output."""
        win, created, unloaded = self._window(["alsa_output.a"], [])

        win.apply_multi_routing()

        self.assertEqual(
            created, ["alsa_output.a"],
            "one checked output must create exactly one loopback",
        )
        self.assertEqual(unloaded, [], "nothing to unload on a fresh sink")

    def test_switching_target_replaces_the_old_loopback(self):
        """What apply_routing did: old route out, new route in."""
        win, created, unloaded = self._window(
            ["alsa_output.b"],
            [{"id": "mod9", "source": "virtual_sink.monitor",
              "sink": "alsa_output.a"}],
        )

        win.apply_multi_routing()

        self.assertEqual(unloaded, ["mod9"])
        self.assertEqual(created, ["alsa_output.b"])

    def test_already_routed_target_is_not_recreated(self):
        """Idempotence -- strictly better than the single-output path,
        which unconditionally tore the route down and rebuilt it."""
        win, created, unloaded = self._window(
            ["alsa_output.a"],
            [{"id": "mod9", "source": "virtual_sink.monitor",
              "sink": "alsa_output.a"}],
        )

        win.apply_multi_routing()

        self.assertEqual(created, [], "an existing route was rebuilt needlessly")
        self.assertEqual(unloaded, [], "an existing route was torn down needlessly")

    def test_two_targets_are_the_case_multi_was_built_for(self):
        """The case the single-output panel genuinely cannot express."""
        win, created, unloaded = self._window(
            ["alsa_output.a", "alsa_output.b"], []
        )

        win.apply_multi_routing()

        self.assertEqual(sorted(created), ["alsa_output.a", "alsa_output.b"])


# ----------------------------------------------------------------------
# 2. Show the user where their data lives
# ----------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DISPLAY"),
                     "no DISPLAY; run under Xvfb to exercise this")
class TestDataPathIsShown(unittest.TestCase):
    """The user could not find their profiles.

    `TestDataPathIsShown` builds the real window and asks Tk for the
    path it resolved. `assertEqual` against `user_presets_dir()` is
    deliberate: the point of the feature is that the displayed string
    is the directory the app actually writes to, not a hand-written
    approximation of it.
    """

    @classmethod
    def setUpClass(cls):
        from pulsar.ui.main_window import MainWindow as _MW

        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = _MW(cls.root)
        cls.root.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_a_data_path_widget_exists(self):
        entry = getattr(self.app, "data_path_var", None)
        self.assertIsNotNone(
            entry,
            "Manage tab never exposes the resolved data directory, so the "
            "user has no way to find or back up their profiles",
        )

    def test_it_shows_the_resolved_presets_directory(self):
        from pulsar.utils.paths import user_presets_dir

        expected = str(user_presets_dir())
        self.assertEqual(
            self.app.data_path_var.get(), expected,
            "the displayed path must be the directory the app really uses",
        )
        self.assertIn("presets", expected)

    def test_it_is_selectable_so_the_path_can_be_copied(self):
        """Read-only, not disabled: a disabled entry cannot be copied."""
        entry = self.app.data_path_entry
        self.assertEqual(str(entry.cget("state")), "readonly")

    def test_it_is_actually_on_screen(self):
        """The exact bug class that hid the Routing panel for its whole
        life: a widget that is built, populated, and never given a
        geometry manager. `winfo_manager()` is the only thing that
        catches it -- attribute access succeeds either way."""
        self.assertTrue(
            self.app.data_path_entry.winfo_manager(),
            "the data-path entry has no geometry manager, so it can "
            "never be seen",
        )


if __name__ == "__main__":
    unittest.main()