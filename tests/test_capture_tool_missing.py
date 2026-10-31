"""
The Capture tab failed silently when pw-dump was missing.

The app hard-requires PipeWire tooling in every package format:

    debian/control          pipewire-bin
    packaging/pulsar.spec   pipewire-utils
    snap                    pipewire-bin

`pw-dump` is what the Capture tab uses to discover app audio streams.
When it was absent, discover_app_audio_nodes() returned an empty list and
the GUI reported:

    (no app audio streams running — start playing audio in an app,
     then hit Refresh)

which is a LIE. The truth is that the tool that finds the streams was
never run. A user with no pipewire tooling installed would see an empty
list, conclude that no app was playing audio, and have no idea why.

Reproduced by hiding pw-dump from PATH:

    pw_dump_available() = False
    LOG: pw-dump not found on PATH; install pipewire-bin (Debian/Ubuntu)
    discover_app_audio_nodes() -> []

The log line went to the app's output pane, which is not where a user
looking at an empty tree looks. The empty tree is what they see, and it
says the wrong thing.

These tests assert the difference between "the tool is missing" and
"nothing is playing" is preserved rather than collapsed.

    DISPLAY=:99 .venv/bin/python -m pytest tests/test_capture_tool_missing.py -q
"""

import os
import shutil
import tkinter as tk
import unittest
from unittest import mock

from pulsar.ui import main_window
from pulsar.utils import pw_app_capture


def _have_display():
    return bool(os.environ.get("DISPLAY"))


class _NoPwDump:
    """Make shutil.which() report pw-dump as absent, everything else real."""

    def __init__(self):
        self._real = shutil.which

    def __call__(self, cmd, *args, **kwargs):
        if cmd == "pw-dump":
            return None
        return self._real(cmd, *args, **kwargs)

    def __enter__(self):
        self._patcher = mock.patch.object(shutil, "which", self)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


class TestMissingToolIsNotTheSameAsNoStreams(unittest.TestCase):
    """The distinction the Capture tab depends on."""

    def test_availability_is_reported_false_without_the_tool(self):
        with _NoPwDump():
            self.assertFalse(pw_app_capture.pw_dump_available())

    def test_availability_is_reported_true_with_the_tool(self):
        if not shutil.which("pw-dump"):
            self.skipTest("pw-dump not installed on this host")
        self.assertTrue(pw_app_capture.pw_dump_available())

    def test_discovery_reports_the_missing_tool_rather_than_no_streams(self):
        """The caller must be able to tell the two apart.

        Returning [] for both is what made the GUI lie.
        """
        with _NoPwDump():
            with self.assertRaises(pw_app_capture.PwDumpUnavailable):
                pw_app_capture.discover_app_audio_nodes()

    def test_the_message_names_the_package_to_install(self):
        """'pipewire-tools' is not a package name on Debian or Fedora, so
        naming the binary would leave the user guessing which package
        to install."""
        with _NoPwDump():
            with self.assertRaises(pw_app_capture.PwDumpUnavailable) as cm:
                pw_app_capture.discover_app_audio_nodes()
        self.assertIn("pipewire-bin", str(cm.exception))


@unittest.skipUnless(_have_display(), "no DISPLAY; run under Xvfb")
class TestCaptureTabSaysWhyItIsEmpty(unittest.TestCase):
    """What the user actually sees in the window."""

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

    def test_the_empty_tree_explains_the_missing_tool(self):
        """The status line must not claim nothing is playing.

        The claim it replaces -- "start playing audio in an app" -- sends
        the user off to start an app that is already playing.
        """
        # The generic handler opens a MODAL messagebox, which blocks
        # forever under Xvfb. Stub it, so a regression fails the test
        # instead of hanging the suite.
        with _NoPwDump(), mock.patch.object(
                main_window.messagebox, "showerror") as showerror:
            self.app._refresh_capture_tree()
            status = self.app.capture_selected_var.get()
            showerror.assert_not_called()
        self.assertNotIn(
            "start playing audio in an app", status,
            "the tree claims no app is playing, but the real reason is "
            "that the tool which finds apps is missing")
        self.assertIn(
            "pipewire-bin", status,
            "the user is not told what to install")

    def test_the_route_buttons_are_disabled_with_no_rows(self):
        """No rows means no app is selected, so the buttons stay off.

        This is the pre-existing behaviour and it must survive the
        change: a disabled button with an empty tree is correct, as
        long as the tree says WHY it is empty.
        """
        with _NoPwDump(), mock.patch.object(
                main_window.messagebox, "showerror"):
            self.app._refresh_capture_tree()
        self.assertEqual(self.app.capture_tree.get_children(), ())
        self.assertEqual(
            str(self.app.capture_route_button.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
