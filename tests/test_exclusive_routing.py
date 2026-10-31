"""
Tests for exclusive app-audio routing ("Output Only Here").

The feature moves a playing stream with `pactl move-sink-input` instead of
loopbacking it. That makes it the only path where audio has to be correlated
across two different views of the same server: discovery reads pw-dump and
reports a PipeWire node name, while move-sink-input is addressed by a
pulseaudio sink-input index. Getting that correlation wrong would move the
wrong application's audio, so it is pinned here.

The parser and matcher are tested against captured `pactl list sink-inputs`
output. The handler is tested with mocked pactl calls.
"""

import unittest
from contextlib import ExitStack
from unittest import mock

from pulsar.ui.main_window import MainWindow
from pulsar.utils.pactl_runner import PactlRunner

# Real output from `pactl list sink-inputs` on PipeWire, trimmed. Note there
# is no pid anywhere in it — pactl does not expose one for a sink input,
# which is why the correlation key is node.name.
SINK_INPUTS_OUTPUT = """Sink Input #2507
\tDriver: PipeWire
\tOwner Module: n/a
\tClient: 2506
\tSink: 2425
\tSample Specification: s16le 2ch 48000Hz
\tCorked: no
\tMute: no
\tProperties:
\t\tobject.id = "63"
\t\tapplication.name = "pw-play"
\t\tnode.name = "pw-play"
\t\tmedia.name = "/tmp/tone.wav"
\t\tmedia.class = "Stream/Output/Audio"
Sink Input #2519
\tDriver: PipeWire
\tSink: 2443
\tProperties:
\t\tobject.id = "64"
\t\tapplication.name = "firefox"
\t\tnode.name = "Firefox"
\t\tmedia.name = "Sound in Firefox"
"""

SINKS_OUTPUT = """Sink #2425
\tState: RUNNING
\tName: alsa_output.usb-Headphones
\tDescription: Headphones
\tMonitor Source: alsa_output.usb-Headphones.monitor
Sink #2443
\tState: IDLE
\tName: alsa_output.pci-Built-in
\tDescription: Built-in Audio
\tMonitor Source: alsa_output.pci-Built-in.monitor
"""


class FakeStringVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeButton:
    def __init__(self):
        self.configs = []

    def config(self, **kw):
        self.configs.append(kw)


class FakeTree:
    def __init__(self, values):
        self._values = values

    def selection(self):
        return ("row0",)

    def item(self, _i):
        return {"values": self._values}

    def index(self, _i):
        return 0


def make_stream_node(node_id=63, node_name="pw-play", app="pw-play"):
    return {
        "id": node_id,
        "application_name": app,
        "media_class": "Stream/Output/Audio",
        "node_name": node_name,
        "pid": None,
        "sink_name": "alsa_output.usb-Headphones",
    }


class FakeListbox:
    """A multi-select listbox stub."""

    def __init__(self, items=(), selected=()):
        self._items = list(items)
        self._selected = tuple(selected)

    def curselection(self):
        return self._selected

    def get(self, index):
        return self._items[index]

    def size(self):
        return len(self._items)

    def delete(self, *a):
        self._items = []

    def insert(self, _end, item):
        self._items.append(item)

    def selection_set(self, *a):
        pass


def make_window(node=None, target="alsa_output.pci-Built-in"):
    node = node if node is not None else make_stream_node()
    win = object.__new__(MainWindow)
    win._capture_nodes = [node]
    win.capture_selected_var = FakeStringVar("")
    win.capture_route_button = FakeButton()
    win.status_var = FakeStringVar("")
    win.add_output = mock.Mock()
    win._refresh_capture_tree = mock.Mock()
    win.capture_route_listbox = FakeListbox(
        items=[target], selected=(0,)
    )
    win.capture_tree = FakeTree(
        (node["id"], f"{node['application_name']} (pid ?)",
         node["media_class"], node["node_name"])
    )
    return win


class TestListSinkInputsParsing(unittest.TestCase):
    """The parser must read index, node name and current sink out of pactl."""

    def _run(self, sink_inputs_out=SINK_INPUTS_OUTPUT):
        with mock.patch.object(
            PactlRunner, "run_command",
            side_effect=[(sink_inputs_out, 0), (SINKS_OUTPUT, 0)],
        ):
            return PactlRunner.list_sink_inputs()

    def test_finds_both_streams(self):
        self.assertEqual(len(self._run()), 2)

    def test_extracts_index_app_and_media(self):
        first = self._run()[0]
        self.assertEqual(first["index"], "2507")
        self.assertEqual(first["application"], "pw-play")
        self.assertEqual(first["media"], "/tmp/tone.wav")

    def test_extracts_node_name(self):
        """node.name is the correlation key, so it has to be captured."""
        self.assertEqual(self._run()[0]["node_name"], "pw-play")
        self.assertEqual(self._run()[1]["node_name"], "Firefox")

    def test_extracts_the_pipewire_node_id(self):
        """object.id IS the PipeWire node id, 1:1.

        This is the key that joins the two views of the same stream: the
        discovery side reports a PipeWire node id, and this side reports
        the same id as object.id. Matching on it is exact, so two
        instances of one application can be told apart -- which matching
        on node.name cannot do, because they share a node.name.
        """
        self.assertEqual(self._run()[0]["node_id"], "63")
        self.assertEqual(self._run()[1]["node_id"], "64")

    def test_resolves_sink_index_to_sink_name(self):
        """A bare sink index is not actionable; callers need the name."""
        first = self._run()[0]
        self.assertEqual(first["sink_index"], "2425")
        self.assertEqual(first["sink"], "alsa_output.usb-Headphones")

    def test_empty_when_no_streams(self):
        with mock.patch.object(PactlRunner, "run_command", return_value=("", 0)):
            self.assertEqual(PactlRunner.list_sink_inputs(), [])

    def test_empty_on_command_failure(self):
        with mock.patch.object(PactlRunner, "run_command", return_value=("", 1)):
            self.assertEqual(PactlRunner.list_sink_inputs(), [])

    def test_sink_that_disappeared_yields_empty_name(self):
        """Do not crash when a stream outlives its sink."""
        with mock.patch.object(
            PactlRunner, "run_command",
            side_effect=[(SINK_INPUTS_OUTPUT, 0), ("", 0)],
        ):
            entries = PactlRunner.list_sink_inputs()
        self.assertEqual(entries[0]["sink"], "")
        self.assertEqual(entries[0]["sink_index"], "2425")


class TestSinkInputsForNode(unittest.TestCase):
    """Matching a discovery row to a sink input."""

    def setUp(self):
        self.streams = [
            {"index": "1", "node_name": "pw-play", "application": "pw-play"},
            {"index": "2", "node_name": "Firefox", "application": "firefox"},
        ]

    def test_matches_on_node_name(self):
        got = PactlRunner.sink_inputs_for_node(self.streams, "pw-play")
        self.assertEqual([s["index"] for s in got], ["1"])

    def test_falls_back_to_application_name(self):
        """Apps that set application.name but not node.name still match."""
        streams = [{"index": "9", "node_name": "", "application": "discord"}]
        got = PactlRunner.sink_inputs_for_node(streams, "discord")
        self.assertEqual([s["index"] for s in got], ["9"])

    def test_no_match_returns_empty(self):
        self.assertEqual(PactlRunner.sink_inputs_for_node(self.streams, "spotify"), [])

    def test_empty_node_name_matches_nothing(self):
        """A blank key must not match every stream."""
        self.assertEqual(PactlRunner.sink_inputs_for_node(self.streams, ""), [])

    def test_returns_all_matches_so_caller_can_disambiguate(self):
        streams = [
            {"index": "1", "node_name": "app", "application": "app"},
            {"index": "2", "node_name": "app", "application": "app"},
        ]
        self.assertEqual(len(PactlRunner.sink_inputs_for_node(streams, "app")), 2)


class TestMoveSinkInput(unittest.TestCase):
    def test_calls_pactl_with_index_and_name(self):
        with mock.patch.object(PactlRunner, "run_command", return_value=("", 0)) as run:
            self.assertTrue(PactlRunner.move_sink_input("2507", "alsa_output.x"))
        run.assert_called_once_with(
            ["move-sink-input", "2507", "alsa_output.x"], None
        )

    def test_reports_failure_without_raising(self):
        """A refused move must return False, not blow up the UI handler."""
        with mock.patch.object(PactlRunner, "run_command", return_value=("Failure", 1)):
            self.assertFalse(PactlRunner.move_sink_input("2507", "gone"))


class _PactlPatch:
    """Started mocks for the pactl calls the handler makes."""

    def __init__(self, move_ok=True, streams=None):
        self.streams = streams if streams is not None else [
            {"index": "2507", "node_id": "63", "application": "pw-play",
             "node_name": "pw-play", "media": "x", "sink_index": "2425",
             "sink": "alsa_output.usb-Headphones"},
        ]
        self.stack = ExitStack()
        self.sinks = self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sinks", return_value=[
                {"name": "alsa_output.usb-Headphones"},
                {"name": "alsa_output.pci-Built-in"},
            ]
        ))
        self.inputs = self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sink_inputs", return_value=self.streams
        ))
        self.move = self.stack.enter_context(mock.patch.object(
            PactlRunner, "move_sink_input", return_value=move_ok
        ))
        self.msgbox = self.stack.enter_context(
            mock.patch("pulsar.ui.main_window.messagebox")
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stack.close()
        return False


class TestRoutingDoesNotTouchOtherApps(unittest.TestCase):
    """The regression this file exists for: routing one app must not
    disturb another app's audio.

    The handler moved from `_on_capture_exclusive_clicked` to
    `_on_capture_route_to_outputs_clicked` when per-app routing became
    native and gained multi-target support. The contract is unchanged:
    the app that was selected is the app that moves, and no other.
    """

    def _click(self, win, **kwargs):
        with _PactlPatch(**kwargs) as p:
            captured = {}

            def fake_apply(plan, logger=None, move=None):
                captured["move"] = move
                return (0, 0)

            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.plan_route"
            ) as plan:
                plan.return_value = mock.Mock(
                    primary="alsa_output.pci-Built-in", create=[],
                    remove=[], needs_move=True,
                )
                with mock.patch(
                    "pulsar.ui.main_window.pw_app_route.apply_plan",
                    side_effect=fake_apply,
                ):
                    win._on_capture_route_to_outputs_clicked()
                # The move is done by apply_plan, which is mocked here,
                # so the callback is invoked directly to exercise it.
                if captured.get("move"):
                    captured["move"]("alsa_output.pci-Built-in")
            return p.move, p.msgbox

    def test_does_not_move_another_apps_stream(self):
        """The regression this pinning exists for: pick the wrong stream.

        Two streams are playing. Selecting Firefox must move Firefox's index
        and leave pw-play's alone.
        """
        streams = [
            {"index": "2507", "node_id": "63", "application": "pw-play",
             "node_name": "pw-play", "sink": "speakers", "sink_index": "1"},
            {"index": "2519", "node_id": "64", "application": "firefox",
             "node_name": "Firefox", "sink": "speakers", "sink_index": "1"},
        ]
        win = make_window(node=make_stream_node(node_id=64, node_name="Firefox",
                                                app="firefox"))
        move, _ = self._click(win, streams=streams)
        move.assert_called_once()
        self.assertEqual(move.call_args[0][0], "2519", "must move Firefox's index")

    def test_two_instances_of_one_app_are_distinguished_by_node_id(self):
        """Two instances share a node.name, so the node id is the key.

        The old handler matched on node.name and had to REFUSE when two
        streams matched. The node id is exact, so the right one can be
        picked instead of giving up.
        """
        dupes = [
            {"index": "1", "node_id": "63", "node_name": "pw-play",
             "application": "pw-play", "sink": "a", "sink_index": "1"},
            {"index": "2", "node_id": "64", "node_name": "pw-play",
             "application": "pw-play", "sink": "b", "sink_index": "2"},
        ]
        win = make_window(node=make_stream_node(node_id=64))
        move, _ = self._click(win, streams=dupes)
        move.assert_called_once()
        self.assertEqual(move.call_args[0][0], "2",
                         "must move the stream with the matching node id")

    def test_refuses_rather_than_guessing_when_the_app_is_absent(self):
        """If the selected app has no live stream, move nothing at all."""
        win = make_window(node=make_stream_node(node_id=99))
        move, mb = self._click(win)  # only a pw-play stream exists
        move.assert_not_called()
        self.assertTrue(mb.showerror.called)

    def test_no_loopback_is_created(self):
        """Routing an app must not ALSO leave a sink-level loopback.

        A loopback belongs to a SINK, not to an app, so one left behind
        would carry other apps' audio too -- the defect this replaces.
        """
        win = make_window()
        with mock.patch.object(PactlRunner, "create_loopback") as lb:
            self._click(win)
        lb.assert_not_called()

    def test_server_refusal_is_surfaced(self):
        win = make_window()
        move, mb = self._click(win, move_ok=False)
        self.assertTrue(mb.showerror.called)


if __name__ == "__main__":
    unittest.main()
