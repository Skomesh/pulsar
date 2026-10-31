"""Regressions for defects found in the 2026-10-31 comprehensive review.

Each test here pins a bug that was reproduced against the code, not a
hypothetical. The reviewer's report and the fix are in the commit that
introduced this file.

Nothing in here needs a display or an audio server.
"""

import os
import tempfile
import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow
from pulsar.utils.paths import read_json_dict
from pulsar.utils.profile_manager import ProfileError


def bare():
    """A MainWindow with no Tk at all."""
    return object.__new__(MainWindow)


class TestDevicesMatchDoesNotCrash(unittest.TestCase):
    """`_devices_match` indexed a key one of its two producers does not have.

    `classify_device_name` -- used for sinks and sources -- returns
    `device_identifier` and `connection_part`. `_extract_hardware_device_info`
    -- used for hardware modules -- returns `device_key`, `device_type`,
    `device_name`, `device_string` and `properties`. It has NO
    `device_identifier`.

    `_categorize_hardware_devices` compares a module's info against an
    existing group's info, so the module side is the one without the
    key, and `device_info1['device_identifier']` raised KeyError. The
    call sits in `refresh_all_views`, which runs while the window is
    being built, so the application failed to START on a machine with
    two or more hardware modules.

    The existing tests never reached it: they pass `[]` for modules, so
    the comparison loop never runs.
    """

    MODULE_INFO = {
        "device_key": "alsa_card",
        "device_type": "alsa",
        "device_name": "Built-in Audio",
        "device_string": "",
        "properties": {},
    }
    SINK_INFO = {
        "device_key": "alsa_Built-in Audio",
        "device_type": "alsa",
        "device_name": "Built-in Audio",
        "device_identifier": "Built-in Audio",
        "connection_part": "",
        "properties": {},
    }

    def test_a_module_info_matches_a_group_info(self):
        win = bare()
        self.assertTrue(
            win._devices_match(dict(self.MODULE_INFO), dict(self.SINK_INFO))
        )

    def test_it_does_not_raise_key_error(self):
        """The exact failure: a KeyError on 'device_identifier'."""
        win = bare()
        try:
            win._devices_match(dict(self.MODULE_INFO), dict(self.SINK_INFO))
        except KeyError as exc:  # pragma: no cover - the bug
            self.fail(f"_devices_match raised KeyError({exc})")

    def test_two_different_devices_still_do_not_match(self):
        win = bare()
        other = dict(self.SINK_INFO)
        other["device_name"] = "USB Headset"
        other["device_identifier"] = "USB Headset"
        self.assertFalse(win._devices_match(dict(self.MODULE_INFO), other))

    def test_two_sink_infos_still_match_on_identifier(self):
        """The original primary match must keep working."""
        win = bare()
        a = dict(self.SINK_INFO)
        b = dict(self.SINK_INFO)
        b["device_name"] = "something else"
        self.assertTrue(win._devices_match(a, b))


class TestCopyToClipboardExists(unittest.TestCase):
    """The Capture tab's copy buttons called a method that did not exist.

    `_copy_selected_capture_node_id` and `_copy_selected_capture_node_name`
    both call `self._copy_to_clipboard(...)`. There was no such method
    anywhere in the repository -- verified by grep across src/, tests/
    and scripts/. Clicking either button raised AttributeError inside the
    Tk callback, so nothing was copied and the user got a traceback
    instead of the node ID. That is the tab's stated headline purpose:
    paste it into OBS.
    """

    def test_the_method_exists(self):
        win = bare()
        self.assertTrue(
            hasattr(win, "_copy_to_clipboard"),
            "MainWindow._copy_to_clipboard is missing; the Capture tab's "
            "Copy Node ID / Copy Node Name buttons raise AttributeError",
        )

    def test_it_puts_the_text_on_the_clipboard(self):
        win = bare()
        win.root = mock.Mock()
        win.add_output = mock.Mock()
        win._copy_to_clipboard("82", label="Node ID 82")
        win.root.clipboard_clear.assert_called_once()
        win.root.clipboard_append.assert_called_once_with("82")

    def test_the_copy_handler_reaches_the_clipboard(self):
        """The handler the button is wired to must not raise."""
        win = bare()
        win.root = mock.Mock()
        win.add_output = mock.Mock()
        win.capture_tree = mock.Mock()
        win.capture_tree.selection.return_value = ("i1",)
        win.capture_tree.item.return_value = {
            "values": ("82", "paplay", "Stream/Output/Audio", "paplay")
        }
        win._copy_selected_capture_node_id()
        win.root.clipboard_append.assert_called_once_with("82")


class TestRejectedProfileNameIsReported(unittest.TestCase):
    """A rejected profile name raised out of the Tk callback.

    `ProfileManager.save_profile` raises ProfileError for a name that
    fails `_VALID_SINK_NAME` (`^[A-Za-z0-9_.-]+$`). The Save Profile
    dialog's `try` wrapped only `capture_topology`; the `save_profile`
    call sat outside it. So a name outside the allowed set -- which the
    dialog itself documents, right under the entry field -- raised
    ProfileError out of the callback: no dialog, no profile saved, and
    nothing said about why.
    """

    def _window(self, save_side_effect=None, save_returns=True):
        win = bare()
        win.profile_manager = mock.Mock()
        win.profile_manager.capture_topology.return_value = {
            "devices": [{"name": "a"}], "routing": [],
        }
        if save_side_effect is not None:
            win.profile_manager.save_profile.side_effect = save_side_effect
        else:
            win.profile_manager.save_profile.return_value = save_returns
        win.add_output = mock.Mock()
        return win

    def test_a_rejected_name_is_reported_not_raised(self):
        win = self._window(
            ProfileError("Invalid profile name: 'My Game Profile'.")
        )
        with mock.patch("pulsar.ui.main_window.messagebox") as mb:
            saved = win._save_profile_named("My Game Profile")
        self.assertFalse(saved)
        mb.showerror.assert_called_once()
        self.assertIn(
            "My Game Profile", mb.showerror.call_args[0][1],
            "the dialog must say which name was refused",
        )

    def test_a_good_name_is_saved(self):
        win = self._window(save_returns=True)
        with mock.patch("pulsar.ui.main_window.messagebox") as mb:
            saved = win._save_profile_named("good-name")
        self.assertTrue(saved)
        mb.showerror.assert_not_called()
        win.profile_manager.save_profile.assert_called_once()

    def test_an_io_failure_is_reported(self):
        # save_profile returns False on an IO error.
        win = self._window(save_returns=False)
        with mock.patch("pulsar.ui.main_window.messagebox") as mb:
            saved = win._save_profile_named("good-name")
        self.assertFalse(saved)
        mb.showerror.assert_called_once()


class TestReadJsonDictSurvivesBinaryFiles(unittest.TestCase):
    """A non-UTF-8 presets file raised straight through the reader.

    `read_json_dict` caught `(OSError, json.JSONDecodeError)`.
    `UnicodeDecodeError` is neither -- it subclasses ValueError, not
    JSONDecodeError. A presets file containing non-UTF-8 bytes
    therefore raised out of `read_json_dict`, and the preset list is
    read while the window is being built, so the window never opened.

    The docstring on that function says it returns {} "rather than
    raising when the file is missing, unreadable, not valid JSON" --
    a binary file is unreadable, and it has to be handled the same way.
    """

    def test_a_binary_file_is_treated_as_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audio_presets.json")
            with open(path, "wb") as handle:
                handle.write(b"\xff\xfe\x00\x01garbage")
            data, reason = read_json_dict(path, label="presets")
        self.assertEqual(data, {})
        self.assertIsNotNone(reason, "a binary file must be reported, not ignored")

    def test_a_valid_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audio_presets.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"a": {"b": 1}}')
            data, reason = read_json_dict(path, label="presets")
        self.assertEqual(data, {"a": {"b": 1}})
        self.assertIsNone(reason)


class FakeListbox:
    """Just enough listbox for the selection-preservation contract."""

    def __init__(self, items=()):
        self.items = list(items)
        self.selected = set()

    def curselection(self):
        return tuple(sorted(self.selected))

    def get(self, index):
        return self.items[index]

    def delete(self, start, end):
        self.items = []
        self.selected = set()

    def insert(self, end, name):
        self.items.append(name)
        return len(self.items) - 1

    def selection_set(self, index):
        self.selected.add(index)


class TestMultiRoutingSelectionIsPreservedByName(unittest.TestCase):
    """The multi-routing selection was preserved by index, not by name.

    `_refresh_multi_routing_outputs` re-listed the hardware outputs and
    re-applied the previous selection using the listbox INDICES. The
    list is rebuilt from a fresh listing, so an index that still exists
    can name a different device: a headset unplugged and an HDMI output
    taking its slot moved the checkmark onto the HDMI output. Apply
    would then route the audio to a device the user never selected.

    The sibling code `_populate_capture_route_options` already
    preserved names, so the two were inconsistent.
    """

    def _window(self, listed, selected_names):
        win = bare()
        box = FakeListbox(listed)
        win.multi_routing_listbox = box
        win.multi_apply_button = mock.Mock()
        win.multi_stop_all_button = mock.Mock()
        win.multi_remove_button = mock.Mock()
        win.add_output = mock.Mock()
        win._routing_sink_name = "vsink"
        win._multi_listbox_sink = "vsink"
        for name in selected_names:
            box.selection_set(box.items.index(name))
        return win, box

    def _refresh(self, win, outputs):
        with mock.patch(
            "pulsar.ui.main_window.PactlRunner.list_hardware_outputs",
            return_value=outputs,
        ):
            with mock.patch(
                "pulsar.ui.main_window.PactlRunner.monitor_source_for",
                return_value=None,
            ):
                win._refresh_multi_routing_outputs()

    def test_the_same_device_stays_selected(self):
        win, box = self._window(["a", "b"], ["b"])
        self._refresh(win, ["a", "b"])
        self.assertEqual(
            [box.items[i] for i in box.curselection()], ["b"]
        )

    def test_a_changed_list_does_not_move_the_checkmark(self):
        """The headset is gone and HDMI has taken its slot."""
        win, box = self._window(["headset", "hdmi"], ["headset"])
        self._refresh(win, ["hdmi", "other"])
        self.assertEqual(
            [box.items[i] for i in box.curselection()], [],
            "the selection moved to a device the user never selected",
        )

    def test_a_device_that_is_still_there_stays_selected_after_a_reorder(self):
        """Same devices, different order -- the selection follows the device."""
        win, box = self._window(["a", "b"], ["a"])
        self._refresh(win, ["b", "a"])
        self.assertEqual(
            [box.items[i] for i in box.curselection()], ["a"]
        )


if __name__ == "__main__":
    unittest.main()
