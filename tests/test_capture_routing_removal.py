"""Removing app audio routing, and seeing what has been customised.

The Capture tab could route an app to one or more outputs, but there was
no way back: nothing removed the links that routing created, and nothing
showed which apps had been routed. A user who routed an app to the wrong
output had no way to undo it from the GUI.

These tests pin the removal behaviour and the report of what is
customised. They need no display and no audio server.
"""

import unittest
from unittest import mock

from pulsar.ui.main_window import MainWindow
from pulsar.utils import pw_app_route
from pulsar.utils.pactl_runner import PactlRunner

# A small PipeWire graph:
#   node 94  vsink_a    a virtual sink
#   node 95  vsink_b    another virtual sink
#   node 96  speakers   the default sink
#   node 61  paplay     the app, with two output ports
#
# Links: paplay -> vsink_a (the session manager's own link) and
#        paplay -> vsink_b (a native link the routing created).
GRAPH = """
[
 {"type": "PipeWire:Interface:Node", "id": 94,
  "info": {"props": {"node.name": "vsink_a"}}},
 {"type": "PipeWire:Interface:Node", "id": 95,
  "info": {"props": {"node.name": "vsink_b"}}},
 {"type": "PipeWire:Interface:Node", "id": 96,
  "info": {"props": {"node.name": "speakers"}}},
 {"type": "PipeWire:Interface:Node", "id": 61,
  "info": {"props": {"node.name": "paplay"}}},
 {"type": "PipeWire:Interface:Port", "id": 45,
  "info": {"props": {"node.id": 94, "port.name": "playback_FL",
                     "port.direction": "in", "audio.channel": "FL"}}},
 {"type": "PipeWire:Interface:Port", "id": 52,
  "info": {"props": {"node.id": 94, "port.name": "playback_FR",
                     "port.direction": "in", "audio.channel": "FR"}}},
 {"type": "PipeWire:Interface:Port", "id": 55,
  "info": {"props": {"node.id": 95, "port.name": "playback_FL",
                     "port.direction": "in", "audio.channel": "FL"}}},
 {"type": "PipeWire:Interface:Port", "id": 57,
  "info": {"props": {"node.id": 95, "port.name": "playback_FR",
                     "port.direction": "in", "audio.channel": "FR"}}},
 {"type": "PipeWire:Interface:Port", "id": 65,
  "info": {"props": {"node.id": 96, "port.name": "playback_FL",
                     "port.direction": "in", "audio.channel": "FL"}}},
 {"type": "PipeWire:Interface:Port", "id": 66,
  "info": {"props": {"node.id": 96, "port.name": "playback_FR",
                     "port.direction": "in", "audio.channel": "FR"}}},
 {"type": "PipeWire:Interface:Port", "id": 68,
  "info": {"props": {"node.id": 61, "port.name": "output_FL",
                     "port.direction": "out", "audio.channel": "FL"}}},
 {"type": "PipeWire:Interface:Port", "id": 85,
  "info": {"props": {"node.id": 61, "port.name": "output_FR",
                     "port.direction": "out", "audio.channel": "FR"}}}
]
"""

# paplay -> vsink_a on FL, paplay -> vsink_b on FR.
LINKS = """  45 vsink_a:playback_FL
102   |<-   68 paplay:output_FL
  57 vsink_b:playback_FR
103   |<-   85 paplay:output_FR
  68 paplay:output_FL
102   |->   45 vsink_a:playback_FL
  85 paplay:output_FR
103   |->   57 vsink_b:playback_FR
"""

STREAMS = [
    {
        "index": "7", "node_id": "61", "application": "paplay",
        "sink": "vsink_a", "sink_index": "5",
    },
]


class FakeStringVar:
    def __init__(self, value=""):
        self._value = value

    def set(self, value):
        self._value = value

    def get(self):
        return self._value


class FakeListbox:
    def __init__(self, items=(), selected=()):
        self._items = list(items)
        self._selected = tuple(selected)

    def curselection(self):
        return self._selected

    def get(self, index):
        return self._items[index]

    def delete(self, *a):
        self._items = []

    def insert(self, end, name):
        self._items.append(name)
        return len(self._items) - 1

    def selection_set(self, index):
        self._selected = (index,)


class FakeTree:
    def __init__(self, values):
        self._values = values

    def selection(self):
        return ("row0",)

    def item(self, _i):
        return {"values": self._values}

    def index(self, _i):
        return 0


def make_window(node_id=61, application="paplay"):
    win = object.__new__(MainWindow)
    win._capture_nodes = [{
        "id": node_id, "application_name": application,
        "media_class": "Stream/Output/Audio", "node_name": application,
        "pid": 7, "sink_name": "vsink_a",
    }]
    win.capture_selected_var = FakeStringVar("")
    win.capture_route_button = mock.Mock()
    win.capture_route_remove_button = mock.Mock()
    win.capture_route_custom_button = mock.Mock()
    win.capture_route_listbox = FakeListbox()
    win.status_var = FakeStringVar("")
    win.add_output = mock.Mock()
    win._refresh_capture_tree = mock.Mock()
    win.capture_tree = FakeTree(
        (str(node_id), f"{application} (pid 7)",
         "Stream/Output/Audio", application)
    )
    return win


class Server:
    """The mocked server the removal path talks to."""

    def __init__(self, streams=None, default_sink="speakers", links=None):
        self.streams = STREAMS if streams is None else streams
        self.default_sink = default_sink
        self.links = LINKS if links is None else links
        self.moved = []
        self.stack = None

    def __enter__(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.object(
            pw_app_route, "_run_pw_dump", return_value=GRAPH
        ))
        self.stack.enter_context(mock.patch.object(
            pw_app_route, "_run_pw_link",
            return_value=mock.Mock(
                stdout=self.links, stderr="", returncode=0
            ),
        ))
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sink_inputs",
            return_value=[dict(s) for s in self.streams],
        ))
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "get_default_sink", return_value=self.default_sink
        ))
        # The real sink list. The removal refuses to move an app back to
        # a default sink that is not there, so the fixture's default has
        # to be in the list.
        self.stack.enter_context(mock.patch.object(
            PactlRunner, "list_sinks",
            return_value=[
                {"name": "speakers"}, {"name": "vsink_a"},
                {"name": "vsink_b"},
            ],
        ))
        # A modal messagebox blocks forever under Xvfb. Every path that
        # can reach one is stubbed here, so a failure reports instead of
        # hanging the suite.
        self.msgbox = self.stack.enter_context(
            mock.patch("pulsar.ui.main_window.messagebox")
        )
        self.move = self.stack.enter_context(mock.patch.object(
            PactlRunner, "move_sink_input", side_effect=self._move
        ))
        return self

    def _move(self, index, sink, logger=None):
        self.moved.append((index, sink))
        return True

    def __exit__(self, *exc):
        self.stack.close()
        return False


def fake_apply(plan, logger=None, move=None):
    """An apply_plan stand-in that behaves like the real one.

    The real apply_plan calls `move` between the removals and the
    creations. A stand-in that does not would leave the caller unable
    to tell whether the app was actually moved.
    """
    if move is not None and plan.primary and plan.needs_move:
        move(plan.primary)
    return (0, len(plan.remove))


# The same links WITHOUT the native link to vsink_b: the app is on the
# default sink with only the session manager's own link.
LINKS_ONLY_SESSION_MANAGER = """  45 vsink_a:playback_FL
102   |<-   68 paplay:output_FL
  68 paplay:output_FL
102   |->   45 vsink_a:playback_FL
"""


class TestStreamTargetSinks(unittest.TestCase):
    """The sinks a stream reaches through NATIVE links."""

    def test_it_finds_the_sinks_the_links_reach(self):
        ports = pw_app_route.parse_ports(GRAPH)
        names = pw_app_route.parse_node_names(GRAPH)
        links = pw_app_route.parse_links(LINKS)
        sinks = pw_app_route.stream_target_sinks(ports, names, 61, links)
        self.assertEqual(sinks, {"vsink_a", "vsink_b"})

    def test_another_streams_links_are_not_counted(self):
        """A link from a different app is not this app's routing."""
        ports = pw_app_route.parse_ports(GRAPH)
        names = pw_app_route.parse_node_names(GRAPH)
        links = pw_app_route.parse_links(LINKS)
        sinks = pw_app_route.stream_target_sinks(ports, names, 999, links)
        self.assertEqual(sinks, set())


class TestCustomizedAppRoutes(unittest.TestCase):
    """Which apps the user has routed, so they can be shown and undone."""

    def test_an_app_on_a_non_default_sink_is_customised(self):
        win = make_window()
        with Server():
            routes = win._customized_app_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["application"], "paplay")
        self.assertEqual(routes[0]["sink"], "vsink_a")
        self.assertEqual(routes[0]["extra_sinks"], ["vsink_b"])

    def test_an_app_on_the_default_sink_with_no_extra_links_is_not(self):
        """The default state: on the default sink, only its own link."""
        win = make_window()
        with Server(default_sink="vsink_a",
                    links=LINKS_ONLY_SESSION_MANAGER):
            routes = win._customized_app_routes()
        self.assertEqual(routes, [])

    def test_an_app_with_an_extra_link_is_customised(self):
        """Even on the default sink, an extra native link is customised.

        Both links the routing created count: the app is on the default
        sink AND on two others, and neither of those is the session
        manager's.
        """
        win = make_window()
        streams = [dict(STREAMS[0], sink="speakers")]
        with Server(streams=streams):
            routes = win._customized_app_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["extra_sinks"], ["vsink_a", "vsink_b"])


class TestRemoveAppRouting(unittest.TestCase):
    """Removing an app's routing puts it back on the default sink."""

    def test_it_removes_the_extra_links_and_moves_to_the_default(self):
        win = make_window()
        with Server() as srv:
            with mock.patch.object(
                pw_app_route, "apply_plan", side_effect=fake_apply
            ) as apply:
                ok, message = win._remove_app_routing(61, "paplay")
        self.assertTrue(ok, message)
        plan = apply.call_args.args[0]
        self.assertEqual(plan.remove, [(85, 57)],
                         "the native link to vsink_b must be broken")
        self.assertEqual(plan.primary, "speakers")
        self.assertTrue(plan.needs_move)
        self.assertEqual(srv.moved, [("7", "speakers")],
                         "the stream must be moved back to the default sink")

    def test_the_session_managers_own_link_is_left_alone(self):
        """The link to the sink the session manager has it on is its own.

        Cutting it by hand makes a later move to that sink a no-op, so
        the route is silently lost.
        """
        win = make_window()
        with Server():
            with mock.patch.object(
                pw_app_route, "apply_plan", side_effect=fake_apply
            ) as apply:
                win._remove_app_routing(61, "paplay")
        plan = apply.call_args.args[0]
        self.assertNotIn((68, 45), plan.remove)

    def test_a_failure_is_reported_not_swallowed(self):
        win = make_window()
        with Server():
            with mock.patch.object(
                pw_app_route, "apply_plan",
                side_effect=pw_app_route.PwLinkUnavailable("pw-link failed"),
            ):
                ok, message = win._remove_app_routing(61, "paplay")
        self.assertFalse(ok)
        self.assertIn("pw-link failed", message)

    def test_without_a_default_sink_it_says_so(self):
        win = make_window()
        with Server(default_sink=None):
            ok, message = win._remove_app_routing(61, "paplay")
        self.assertFalse(ok)
        self.assertIn("default", message.lower())


class TestRemoveAllAppRouting(unittest.TestCase):
    """Removing ALL app routing, in one action."""

    def test_every_customised_app_is_reset(self):
        win = make_window()
        with Server() as srv:
            with mock.patch.object(
                pw_app_route, "apply_plan", side_effect=fake_apply
            ) as apply:
                count = win._remove_all_app_routing()
        self.assertEqual(count, 1)
        apply.assert_called_once()
        self.assertEqual(srv.moved, [("7", "speakers")])

    def test_nothing_to_remove_is_reported_as_zero(self):
        win = make_window()
        with Server(default_sink="vsink_a",
                    links=LINKS_ONLY_SESSION_MANAGER):
            with mock.patch.object(
                pw_app_route, "apply_plan", side_effect=fake_apply
            ) as apply:
                count = win._remove_all_app_routing()
        self.assertEqual(count, 0)
        apply.assert_not_called()


class TestRemoveRoutingButton(unittest.TestCase):
    """The button that starts the removal, and the one that shows the
    customised routes."""

    def test_the_button_needs_a_selection(self):
        win = make_window()
        win.capture_tree = FakeTree(("", "", "", ""))
        win.capture_tree.selection = lambda: ()
        win._update_capture_route_button_state()
        win.capture_route_remove_button.config.assert_called_with(
            state="disabled"
        )

    def test_the_button_is_enabled_with_a_selection(self):
        win = make_window()
        win._update_capture_route_button_state()
        win.capture_route_remove_button.config.assert_called_with(
            state="normal"
        )

    def test_clicking_it_removes_the_selected_apps_routing(self):
        win = make_window()
        with Server() as srv:
            with mock.patch.object(
                pw_app_route, "apply_plan", side_effect=fake_apply
            ):
                win._on_capture_route_remove_clicked()
        self.assertEqual(srv.moved, [("7", "speakers")])

    def test_clicking_it_with_no_selection_says_so(self):
        win = make_window()
        win.capture_tree.selection = lambda: ()
        with Server() as srv:
            win._on_capture_route_remove_clicked()
        srv.msgbox.showinfo.assert_called_once()
        self.assertIn("Select an app", srv.msgbox.showinfo.call_args[0][1])


class TestCustomRoutingDialog(unittest.TestCase):
    """The dialog that shows what has been customised."""

    def test_it_opens_without_a_display_of_its_own(self):
        """The dialog is a thin shell over testable methods."""
        win = make_window()
        with Server():
            with mock.patch.object(
                MainWindow, "_show_custom_routing_dialog", return_value=None
            ) as show:
                win._show_custom_routing_dialog()
        show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
