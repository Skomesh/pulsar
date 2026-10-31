"""
Tests for the Capture tab's per-app routing handler.

The Capture tab's "Route Here" used to route the app's SINK, not the
app: it took the sink's .monitor and looped it to the target. A monitor
carries every stream on that sink, so routing one app silently
destroyed another app's route, and an app could never be heard on two
outputs at once.

The new handler routes the APP, using native PipeWire links, and
supports one OR MORE targets.

The handler is tested with the server mocked. The mechanism itself is
pinned in test_pw_app_route.py and test_pw_app_route_plan.py.
"""

import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow
from pulsar.utils.pactl_runner import PactlRunner


class FakeStringVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeListbox:
    """A multi-select listbox stub."""

    def __init__(self, selected=()):
        self._selected = tuple(selected)
        self._items = []

    def curselection(self):
        return self._selected

    def get(self, index):
        return self._items[index]

    def delete(self, *a):
        self._items = []

    def insert(self, _end, item):
        self._items.append(item)

    def size(self):
        return len(self._items)

    def selection_set(self, *a):
        pass

    def selection_clear(self, *a):
        pass


class FakeTree:
    def __init__(self, values):
        self._values = values

    def selection(self):
        return ("row0",)

    def item(self, _i):
        return {"values": self._values}

    def index(self, _i):
        return 0


def make_node(node_id=63, node_name="paplay", app="paplay"):
    return {
        "id": node_id,
        "application_name": app,
        "media_class": "Stream/Output/Audio",
        "node_name": node_name,
        "pid": None,
        "sink_name": "speakers",
    }


def make_window(node=None, selected_outputs=()):
    node = node if node is not None else make_node()
    win = object.__new__(MainWindow)
    win._capture_nodes = [node]
    win.capture_selected_var = FakeStringVar("")
    win.capture_route_button = mock.Mock()
    win.capture_exclusive_button = mock.Mock()
    win.capture_route_listbox = FakeListbox(selected_outputs)
    win.status_var = FakeStringVar("")
    win.add_output = mock.Mock()
    win._refresh_capture_tree = mock.Mock()
    win.capture_tree = FakeTree(
        (node["id"], f"{node['application_name']} (pid ?)",
         node["media_class"], node["node_name"])
    )
    return win


class _Server:
    """The mocked server calls the handler makes."""

    def __init__(self, node_id=63, sm_target="speakers", move_ok=True):
        self.node_id = node_id
        self.sm_target = sm_target
        self.move_ok = move_ok
        self.stack = None
        self.move = None

    def __enter__(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.move = self.stack.enter_context(mock.patch.object(
            PactlRunner, "move_sink_input", return_value=self.move_ok
        ))
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sink_inputs", return_value=[
                {"index": "2507", "node_id": str(self.node_id),
                 "node_name": "paplay", "application": "paplay",
                 "media": "x", "sink_index": "1", "sink": self.sm_target},
            ]
        ))
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sinks", return_value=[
                {"name": "speakers"}, {"name": "headphones"},
            ]
        ))
        self.msgbox = self.stack.enter_context(
            mock.patch("pulsar.ui.main_window.messagebox")
        )
        return self

    def __exit__(self, *exc):
        self.stack.close()
        return False


class TestRouteAppToOutputs(unittest.TestCase):
    """The handler routes the app, to one or more outputs."""

    def _route(self, win, targets, **kwargs):
        win.capture_route_listbox._items = list(targets)
        win.capture_route_listbox._selected = tuple(range(len(targets)))
        with _Server(**kwargs) as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.plan_route"
            ) as plan:
                plan.return_value = mock.Mock(
                    primary=targets[0] if targets else None,
                    create=[],
                    remove=[],
                    needs_move=False,
                )
                with mock.patch(
                    "pulsar.ui.main_window.pw_app_route.apply_plan",
                    return_value=(0, 0),
                ) as apply:
                    win._on_capture_route_to_outputs_clicked()
                    return srv, plan, apply

    def test_no_selection_is_reported(self):
        win = make_window()
        with _Server() as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.apply_plan"
            ) as apply:
                win._on_capture_route_to_outputs_clicked()
        apply.assert_not_called()
        self.assertTrue(srv.msgbox.showinfo.called)

    def test_no_stream_selected_is_reported(self):
        win = make_window()
        win.capture_tree.selection = lambda: ()
        with _Server() as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.apply_plan"
            ) as apply:
                win._on_capture_route_to_outputs_clicked()
        apply.assert_not_called()
        self.assertTrue(srv.msgbox.showinfo.called)

    def test_the_targets_are_the_selected_outputs(self):
        win = make_window()
        _, plan, _ = self._route(win, ["speakers", "headphones"])
        self.assertEqual(
            sorted(plan.call_args.kwargs["target_sinks"]),
            ["headphones", "speakers"],
        )

    def test_the_stream_is_identified_by_node_id(self):
        """The exact key, so two instances of one app are not confused."""
        win = make_window(node=make_node(node_id=63))
        _, plan, _ = self._route(win, ["speakers"])
        self.assertEqual(plan.call_args.kwargs["stream_node_id"], 63)

    def test_the_target_must_still_exist(self):
        """The list can be stale -- a device unplugged since the refresh.

        The old handler checked this and refused; the new one must too,
        or a route to a device that is gone silently does nothing and
        the user is told it worked.
        """
        win = make_window()
        win.capture_route_listbox._items = ["unplugged_headphones"]
        win.capture_route_listbox._selected = (0,)
        with _Server() as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.apply_plan"
            ) as apply:
                win._on_capture_route_to_outputs_clicked()
        apply.assert_not_called()
        self.assertTrue(srv.msgbox.showerror.called)

    def test_a_missing_stream_is_reported(self):
        """The app stopped playing between the refresh and the click."""
        win = make_window(node=make_node(node_id=99))
        win.capture_route_listbox._items = ["speakers"]
        win.capture_route_listbox._selected = (0,)
        with _Server(node_id=63) as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.apply_plan"
            ) as apply:
                win._on_capture_route_to_outputs_clicked()
        apply.assert_not_called()
        self.assertTrue(srv.msgbox.showerror.called)

    def test_the_session_manager_target_is_passed_in(self):
        """The planner needs to know which sink the session manager owns."""
        win = make_window()
        _, plan, _ = self._route(win, ["speakers"], sm_target="speakers")
        self.assertEqual(
            plan.call_args.kwargs["session_manager_target"], "speakers"
        )

    def test_the_move_is_wired_to_the_plans_primary(self):
        """The move has to happen between the removals and the creations."""
        win = make_window()
        win.capture_route_listbox._items = ["headphones"]
        win.capture_route_listbox._selected = (0,)
        captured = {}

        def fake_apply(plan, logger=None, move=None):
            captured["move"] = move
            return (0, 0)

        with _Server() as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.plan_route"
            ) as plan:
                plan.return_value = mock.Mock(
                    primary="headphones", create=[], remove=[],
                    needs_move=True,
                )
                with mock.patch(
                    "pulsar.ui.main_window.pw_app_route.apply_plan",
                    side_effect=fake_apply,
                ):
                    win._on_capture_route_to_outputs_clicked()

            # The move callback must be exercised INSIDE the mocked
            # server context -- outside it, PactlRunner.move_sink_input
            # is the real function again and the mock sees no call.
            self.assertIsNotNone(captured.get("move"))
            captured["move"]("headphones")
            srv.move.assert_called_once()
            args, kwargs = srv.move.call_args
            self.assertEqual(args[0], "2507")
            self.assertEqual(args[1], "headphones")

    def test_the_status_line_reports_the_new_routing(self):
        win = make_window()
        win.capture_route_listbox._items = ["speakers", "headphones"]
        win.capture_route_listbox._selected = (0, 1)
        with _Server():
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.plan_route"
            ) as plan:
                plan.return_value = mock.Mock(
                    primary="speakers", create=[], remove=[],
                    needs_move=False,
                )
                with mock.patch(
                    "pulsar.ui.main_window.pw_app_route.apply_plan",
                    return_value=(0, 0),
                ):
                    win._on_capture_route_to_outputs_clicked()
        self.assertIn("speakers", win.status_var.get())
        self.assertIn("headphones", win.status_var.get())

    def test_a_server_refusal_is_surfaced(self):
        """The session manager refused the move -- say so, do not claim
        success."""
        win = make_window()
        win.capture_route_listbox._items = ["headphones"]
        win.capture_route_listbox._selected = (0,)
        with _Server(move_ok=False) as srv:
            with mock.patch(
                "pulsar.ui.main_window.pw_app_route.plan_route"
            ) as plan:
                plan.return_value = mock.Mock(
                    primary="headphones", create=[], remove=[],
                    needs_move=True,
                )
                with mock.patch(
                    "pulsar.ui.main_window.pw_app_route.apply_plan",
                    return_value=(0, 0),
                ):
                    win._on_capture_route_to_outputs_clicked()
        self.assertTrue(srv.msgbox.showerror.called)


class TestRouteButtonEnablement(unittest.TestCase):
    """The button follows the selection, like the other capture controls."""

    def test_disabled_with_no_selection(self):
        win = make_window()
        win.capture_tree.selection = lambda: ()
        win._on_capture_tree_select()
        self.assertEqual(
            win.capture_route_button.config.call_args_list[-1].kwargs.get("state"),
            "disabled",
        )

    def test_disabled_when_no_output_is_selected(self):
        win = make_window()
        win.capture_route_listbox._items = ["speakers"]
        win.capture_route_listbox._selected = ()
        win._on_capture_tree_select()
        self.assertEqual(
            win.capture_route_button.config.call_args_list[-1].kwargs.get("state"),
            "disabled",
        )

    def test_enabled_when_an_output_is_selected(self):
        win = make_window()
        win.capture_route_listbox._items = ["speakers"]
        win.capture_route_listbox._selected = (0,)
        win._on_capture_tree_select()
        self.assertEqual(
            win.capture_route_button.config.call_args_list[-1].kwargs.get("state"),
            "normal",
        )


if __name__ == "__main__":
    unittest.main()
