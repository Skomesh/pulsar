"""
Tests for the App Audio tab refresh and the removal of the post-create
route-to-default popup.

These drive the real MainWindow methods on a bare `object.__new__` instance
with just the attributes each method touches. No Tk root, no display: the
combo and tree stand-ins are the smallest objects that record what the
production code does to them.

Background
----------
The App Audio tab used to populate its output dropdown and stream tree once,
at construction time. Any sink created afterwards -- on the Create tab, by a
headset connecting, by an OBS virtual sink appearing -- stayed invisible
until the app was restarted. `on_tab_changed` now re-scans on entry.

The "Route to default output?" popup that fired after every sink/source
creation was removed along with the `_offer_route_to_default` helper it
called, so the routing it performed no longer happens implicitly either.
"""

import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow


class FakeStringVar:
    """Just enough of a tkinter StringVar for the code under test."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeCombobox:
    """Records config(values=...) calls."""

    def __init__(self):
        self.values = ()

    def config(self, **kwargs):
        if "values" in kwargs:
            self.values = tuple(kwargs["values"])


class FakeBoolVar:
    def __init__(self, value=False):
        self._value = value

    def get(self):
        return self._value


class FakeTree:
    def __init__(self):
        self.deleted = 0

    def get_children(self):
        return ()

    def delete(self, *a):
        self.deleted += 1


def make_window(tab_text="App Audio"):
    """A MainWindow stub with only what the two methods under test touch."""
    win = object.__new__(MainWindow)
    win.capture_route_var = FakeStringVar("")
    win.capture_route_combo = FakeCombobox()
    win._capture_route_hardware_only_var = FakeBoolVar(False)
    win.capture_tree = FakeTree()
    win._capture_nodes = []
    win._refresh_capture_tree = mock.Mock()
    win.add_output = mock.Mock()
    # The Create tab's own handler reads the name field; stub it so a
    # "which tab am I on" test doesn't need the whole Create form.
    win.refresh_create_tab_state = mock.Mock()

    class FakeNotebook:
        def select(self):
            return "!notebook.tab"

        def tab(self, _index, _opt):
            return tab_text

    win.tab_control = FakeNotebook()
    return win


class TestAppAudioTabRefreshesOnEntry(unittest.TestCase):
    """The dropdown and the stream tree must be rebuilt on tab entry."""

    def setUp(self):
        self.win = make_window()

    def test_entering_app_audio_tab_repopulates_outputs(self):
        with mock.patch.object(
            MainWindow, "_populate_capture_route_options"
        ) as pop:
            self.win.on_tab_changed(None)
        pop.assert_called_once_with()

    def test_entering_app_audio_tab_refreshes_stream_tree(self):
        # Patch the server lookups too: on_tab_changed calls the real
        # _populate_capture_route_options, and a test must not depend on
        # whether the host has a working PulseAudio.
        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.list_hardware_outputs", return_value=[]
        ), mock.patch("pulsar.ui.main_window.PactlRunner.list_sinks", return_value=[]):
            self.win.on_tab_changed(None)
        self.win._refresh_capture_tree.assert_called_once_with()

    def test_other_tabs_do_not_trigger_capture_refresh(self):
        for tab in ("Create", "Manage", "Profiles", "Diagnostics", "Output"):
            with self.subTest(tab=tab):
                win = make_window(tab_text=tab)
                with mock.patch.object(
                    MainWindow, "_populate_capture_route_options"
                ) as pop:
                    win.on_tab_changed(None)
                pop.assert_not_called()

    def test_refresh_uses_currently_listed_sinks(self):
        """The refresh must actually ask the server, not reuse a cache."""
        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.list_hardware_outputs",
            return_value=["alsa_output.new_headset"],
        ), mock.patch(
            "pulsar.ui.main_window.PactlRunner.list_sinks",
            return_value=[{"name": "game_sink"}, {"name": "alsa_output.new_headset"}],
        ):
            self.win._populate_capture_route_options()
        self.assertEqual(
            self.win.capture_route_combo.values,
            ("alsa_output.new_headset", "game_sink  [virtual]"),
        )


class TestRouteSelectionSurvivesRefresh(unittest.TestCase):
    """Rebuilding the list must not silently discard the user's pick.

    Once the refresh runs on every tab entry, a reset-to-first would fire on
    every visit -- the user would pick headphones, tab away, tab back, and be
    looking at speakers again.
    """

    def setUp(self):
        self.win = make_window()
        self.win.capture_route_var.set("headphones  [virtual]")

    def _rebuild(self, outputs):
        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.list_hardware_outputs",
            return_value=outputs,
        ), mock.patch("pulsar.ui.main_window.PactlRunner.list_sinks", return_value=[]):
            self.win._populate_capture_route_options()

    def test_existing_selection_is_kept(self):
        self._rebuild(["speakers", "headphones  [virtual]"])
        self.assertEqual(self.win.capture_route_var.get(), "headphones  [virtual]")

    def test_vanished_device_falls_back_to_first(self):
        """If the picked sink is unplugged, fall back rather than keep a ghost."""
        self._rebuild(["speakers", "monitor_headphones"])
        self.assertEqual(self.win.capture_route_var.get(), "speakers")

    def test_no_outputs_at_all_clears_selection(self):
        self._rebuild([])
        self.assertEqual(self.win.capture_route_var.get(), "")
        self.assertEqual(self.win.capture_route_combo.values, ())


class TestCreateDevicePopupRemoved(unittest.TestCase):
    """Creating a device must not pop a dialog or create a loopback."""

    def test_helper_is_gone(self):
        self.assertFalse(
            hasattr(MainWindow, "_offer_route_to_default"),
            "The popup and its implicit routing were both removed; "
            "routing is now an explicit action on the Manage tab.",
        )

    def test_creating_a_sink_creates_no_loopback(self):
        win = make_window()
        win.device_type_var = FakeStringVar("sink")
        win.sink_name_var = FakeStringVar("game_sink")
        win.sink_desc_var = FakeStringVar("Game")
        win.audio_preset_var = FakeStringVar("Stereo")
        win.channels_var = FakeStringVar("2")
        win.status_var = FakeStringVar("")
        win.add_output = mock.Mock()
        win.refresh_all_views = mock.Mock()
        win._validate_sink_name = lambda name: (True, name, "")
        win._get_available_name = mock.Mock()
        win.root = mock.Mock()

        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.create_sink_only", return_value=True
        ) as create_sink, mock.patch(
            "pulsar.ui.main_window.PactlRunner.create_loopback"
        ) as create_lb, mock.patch(
            "pulsar.ui.main_window.messagebox"
        ) as msgbox:
            win.create_device()

        self.assertTrue(create_sink.called, "the sink should still be created")
        create_lb.assert_not_called()
        # No dialog of any kind, not just the yes/no offer.
        self.assertEqual(msgbox.method_calls, [])

    def test_creating_a_source_creates_no_loopback(self):
        win = make_window()
        win.device_type_var = FakeStringVar("source")
        win.sink_name_var = FakeStringVar("mic_src")
        win.sink_desc_var = FakeStringVar("Mic")
        win.audio_preset_var = FakeStringVar("Mono")
        win.channels_var = FakeStringVar("1")
        win.status_var = FakeStringVar("")
        win.add_output = mock.Mock()
        win.refresh_all_views = mock.Mock()
        win._validate_sink_name = lambda name: (True, name, "")
        win._get_available_name = mock.Mock()
        win.root = mock.Mock()

        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.create_source_only", return_value=True
        ), mock.patch(
            "pulsar.ui.main_window.PactlRunner.create_loopback"
        ) as create_lb, mock.patch(
            "pulsar.ui.main_window.messagebox"
        ) as msgbox:
            win.create_device()

        create_lb.assert_not_called()
        self.assertEqual(msgbox.method_calls, [])


if __name__ == "__main__":
    unittest.main()
