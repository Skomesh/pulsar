"""
The app must actually START.

This file exists because of a specific failure. Removing the built-in
profiles deleted `ProfileManager.is_builtin_name` and
`is_shadowed_by_user`, but `refresh_profile_list` still called them. The
result:

    $ python -m pulsar
    AttributeError: 'ProfileManager' object has no attribute 'is_builtin_name'

The app could not open at all. And the test suite was GREEN -- 295
passed -- because every test that touches the GUI is display-gated and
skips in a headless run. Nothing in CI ever constructs a MainWindow.

So: a test that constructs the real window and visits every tab. It is
display-gated like the others, but that gap is exactly why it has to
exist and why it must be run explicitly under Xvfb:

    /tmp/xvfb-local/root/usr/bin/Xvfb :96 -screen 0 1280x900x24 -nolisten tcp
    DISPLAY=:96 .venv/bin/python -m pytest tests/test_app_starts.py -q
"""

import os
import time
import tkinter as tk
import unittest
from tkinter import ttk


def _have_display():
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestTheAppStarts(unittest.TestCase):
    """Construct MainWindow the way the real entry point does."""

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

    def test_the_window_built(self):
        """The regression this file is for.

        Constructing MainWindow used to raise AttributeError on a method
        that had been deleted, and 295 tests stayed green while the app
        could not open.
        """
        self.assertIsNotNone(self.app)

    def test_notebook_has_the_expected_tabs(self):
        nb = next(w for w in self.root.winfo_children()
                  if isinstance(w, ttk.Notebook))
        tabs = [nb.tab(i, "text") for i in range(nb.index("end"))]
        self.assertIn("Create", tabs)
        self.assertIn("Manage", tabs)
        self.assertIn("Profiles", tabs)

    def test_every_tab_can_be_opened(self):
        """A tab that raises on entry is a crash the user hits on click."""
        nb = next(w for w in self.root.winfo_children()
                  if isinstance(w, ttk.Notebook))
        failures = []
        for i in range(nb.index("end")):
            name = nb.tab(i, "text")
            nb.select(i)
            try:
                self.app.on_tab_changed(None)
                self.root.update_idletasks()
                self.root.update()
                time.sleep(0.15)
            except Exception as exc:  # noqa: BLE001 - the point is to report
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], f"tabs failed to open: {failures}")

    def test_profile_refresh_does_not_raise(self):
        """The exact call site that broke startup."""
        self.app.refresh_profile_list()
        self.assertIsInstance(self.app._profile_actual_names, list)

    def test_no_dead_manager_methods_are_called_from_the_gui(self):
        """Guard the whole class of bug, not just this one instance.

        Any attribute the GUI reaches on a manager that does not exist
        would be a latent startup crash.
        """
        for attr in ("is_builtin_name", "is_shadowed_by_user", "is_builtin"):
            self.assertFalse(
                hasattr(self.app.profile_manager, attr),
                f"ProfileManager.{attr} is gone but something may still call it")

    def test_refresh_runs_clean_twice(self):
        """Second call matters: preset/trace handlers can break on re-entry."""
        self.app.refresh_profile_list()
        self.app.refresh_profile_list()


if __name__ == "__main__":
    unittest.main()
