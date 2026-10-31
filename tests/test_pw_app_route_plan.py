"""
Tests for the per-app routing planner and applier.

The Capture tab's "Route Here" used to route the app's SINK, not the
app: it took the sink's .monitor and looped it to the target. A monitor
carries every stream on that sink, so the route was a property of the
device, not of the app -- routing one app silently destroyed another
app's route, and an app could never be heard on two outputs at once.

Native PipeWire links fix that: a stream's output port belongs to that
one app, so linking it to a sink's input port addresses the app
precisely.

THE DESIGN, and why each part is there:

  * The session manager owns the link to the PRIMARY target, via
    `move-sink-input`. The PulseAudio compat layer then reports the
    app's real sink, so other tools (and the app's own Manage tab)
    see the truth.
  * Native links own the ADDITIONAL targets, because the compat layer
    cannot express "one stream, two sinks" at all.
  * A native link is NEVER created for the sink the session manager
    currently reports, because the subsequent move would leave two
    links to the same port pair.

Measured on this host: audio on every target, exclusive otherwise,
idempotent on re-run, and the route survives the session manager
re-evaluating (default-sink change, a new sink appearing).

These tests use recorded pw-dump / pw-link output, so they need no
live server.
"""

import unittest
from unittest import mock

from pulsar.utils import pw_app_route
from pulsar.utils.pw_app_route import (
    RoutePlan,
    parse_links,
    parse_ports,
    plan_route,
)

# Two streams of the SAME binary (node.name "paplay") and two sinks.
# This is the case the old monitor-based routing could not express.
PW_DUMP_OUTPUT = """[
  {
    "id": 94,
    "type": "PipeWire:Interface:Node",
    "info": {"props": {"node.name": "tgt_a"}}
  },
  {
    "id": 95,
    "type": "PipeWire:Interface:Node",
    "info": {"props": {"node.name": "tgt_b"}}
  },
  {
    "id": 61,
    "type": "PipeWire:Interface:Node",
    "info": {"props": {"node.name": "paplay", "media.class": "Stream/Output/Audio"}}
  },
  {
    "id": 62,
    "type": "PipeWire:Interface:Node",
    "info": {"props": {"node.name": "paplay", "media.class": "Stream/Output/Audio"}}
  },
  {
    "id": 45,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 94, "port.name": "playback_FL", "port.direction": "in"}}
  },
  {
    "id": 72,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 94, "port.name": "playback_FR", "port.direction": "in"}}
  },
  {
    "id": 80,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 95, "port.name": "playback_FL", "port.direction": "in"}}
  },
  {
    "id": 88,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 95, "port.name": "playback_FR", "port.direction": "in"}}
  },
  {
    "id": 68,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 61, "port.name": "output_FL", "port.direction": "out"}}
  },
  {
    "id": 85,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 61, "port.name": "output_FR", "port.direction": "out"}}
  },
  {
    "id": 91,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 62, "port.name": "output_FL", "port.direction": "out"}}
  },
  {
    "id": 92,
    "type": "PipeWire:Interface:Port",
    "info": {"props": {"node.id": 62, "port.name": "output_FR", "port.direction": "out"}}
  }
]
"""

# The session manager's own link: stream 61 -> tgt_a.
PW_LINK_OUTPUT = """  45 tgt_a:playback_FL
 100   |<-   68 paplay:output_FL
  72 tgt_a:playback_FR
 101   |<-   85 paplay:output_FR
  68 paplay:output_FL
100   |->   45 tgt_a:playback_FL
  85 paplay:output_FR
101   |->   72 tgt_a:playback_FR
"""


def _ports():
    return parse_ports(PW_DUMP_OUTPUT)


def _names():
    return {
        94: "tgt_a",
        95: "tgt_b",
        61: "paplay",
        62: "paplay",
    }


class TestPlanRoute(unittest.TestCase):
    """The whole routing decision, in one function."""

    def _plan(self, targets, sm_target="tgt_a", stream_node_id=61,
              existing=None):
        if existing is None:
            existing = parse_links(PW_LINK_OUTPUT)
        return plan_route(
            ports=_ports(),
            node_names=_names(),
            stream_node_id=stream_node_id,
            target_sinks=targets,
            existing_links=existing,
            session_manager_target=sm_target,
        )

    def test_single_target_creates_no_native_link(self):
        """The session manager owns the primary, so no native link is made."""
        plan = self._plan(["tgt_b"])
        self.assertEqual(plan.create, [])
        self.assertEqual(plan.remove, [])

    def test_two_targets_creates_one_native_link_to_the_extra(self):
        """The session manager's CURRENT sink stays primary if it is a target.

        The session manager drops its own link when the stream moves, so
        the sink it vacates would be left with no link at all. Keeping
        it primary means it is never one of the "additional" targets.
        """
        plan = self._plan(["tgt_b", "tgt_a"])
        # sm_target is tgt_a, which is among the targets -> tgt_a is primary
        # and tgt_b is the additional target.
        self.assertEqual(plan.primary, "tgt_a")
        self.assertEqual(sorted(plan.create), [(68, 80), (85, 88)])

    def test_the_first_target_is_primary_when_the_current_sink_is_not_a_target(self):
        """When the app is on a sink that is NOT a target, the first
        target becomes primary and the rest get native links."""
        plan = self._plan(["tgt_b", "tgt_a"], sm_target="elsewhere",
                          existing=set())
        self.assertEqual(plan.primary, "tgt_b")
        # tgt_a is the additional target -> its input ports are 45 and 72.
        self.assertEqual(sorted(plan.create), [(68, 45), (85, 72)])
        self.assertEqual(plan.remove, [])

    def test_native_link_is_not_created_for_the_session_managers_sink(self):
        """A native link there would duplicate the session manager's link."""
        plan = self._plan(["tgt_a", "tgt_b"], sm_target="tgt_a")
        # tgt_a is primary; tgt_b is extra and has no link yet.
        self.assertEqual(sorted(plan.create), [(68, 80), (85, 88)])

    def test_a_stale_native_link_to_a_dropped_target_is_removed(self):
        existing = parse_links(PW_LINK_OUTPUT) | {(68, 80), (85, 88)}
        plan = self._plan(["tgt_a"], existing=existing)
        self.assertEqual(sorted(plan.remove), [(68, 80), (85, 88)])

    def test_another_apps_links_are_never_touched(self):
        """The core bug: routing app B must not destroy app A's route."""
        # A second stream (node 62) also links to tgt_a.
        existing = parse_links(PW_LINK_OUTPUT) | {(91, 45), (92, 72)}
        plan = self._plan(["tgt_b"], stream_node_id=61, existing=existing)
        self.assertEqual(plan.remove, [])
        self.assertNotIn((91, 45), plan.remove)
        self.assertNotIn((92, 72), plan.remove)

    def test_re_routing_to_the_same_targets_changes_nothing(self):
        """Idempotency: the end state must match the selection exactly."""
        existing = parse_links(PW_LINK_OUTPUT) | {(68, 80), (85, 88)}
        plan = self._plan(["tgt_a", "tgt_b"], sm_target="tgt_a",
                          existing=existing)
        self.assertEqual(plan.create, [])
        self.assertEqual(plan.remove, [])

    def test_no_targets_means_no_links_at_all(self):
        """Un-routing: the app should go back to the default sink."""
        plan = self._plan([], sm_target="tgt_a")
        # Nothing to create; the session manager's link is not ours to cut.
        self.assertEqual(plan.create, [])

    def test_unknown_target_is_ignored(self):
        """A target that does not exist must be ignored COMPLETELY.

        The assertion used to check only that no links were created. The
        planner picked the vanished sink as the PRIMARY and set
        needs_move, so it planned a move to a sink that does not exist.
        """
        plan = self._plan(["tgt_gone"])
        self.assertEqual(plan.create, [])
        self.assertIsNone(plan.primary)
        self.assertFalse(plan.needs_move)

    def test_a_sink_with_no_input_ports_cannot_be_a_target(self):
        plan = self._plan(["paplay"])
        self.assertEqual(plan.create, [])


class TestApplyPlan(unittest.TestCase):
    """Applying the plan: the right pw-link calls, in the right order."""

    def _apply(self, plan):
        with mock.patch.object(
            pw_app_route, "_run_pw_link", return_value=mock.Mock(returncode=0)
        ) as run:
            created, removed = pw_app_route.apply_plan(plan)
        return run, created, removed

    def test_removes_before_creating(self):
        """A stale link to the new primary must go before the move."""
        plan = RoutePlan(create=[(68, 80)], remove=[(68, 45)])
        run, created, removed = self._apply(plan)
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls, [["-d", "68", "45"], ["68", "80"]])

    def test_counts_what_it_did(self):
        plan = RoutePlan(create=[(68, 80), (85, 88)], remove=[(68, 45)])
        _, created, removed = self._apply(plan)
        self.assertEqual(created, 2)
        self.assertEqual(removed, 1)

    def test_a_failed_link_is_reported_not_swallowed(self):
        """A link that failed must not be reported as a link that was made.

        The counts used to come back as (0, 0) and the caller took the
        "already routed" branch, telling the user the app was on the
        new sink when it was on none of them. A failed link now raises,
        so the caller reports the failure.
        """
        with mock.patch.object(
            pw_app_route, "_run_pw_link",
            return_value=mock.Mock(returncode=1, stderr="boom"),
        ):
            with self.assertRaises(pw_app_route.PwLinkUnavailable):
                pw_app_route.apply_plan(RoutePlan(create=[(68, 80)]))

    def test_a_failure_is_logged(self):
        log = mock.Mock()
        with mock.patch.object(
            pw_app_route, "_run_pw_link",
            return_value=mock.Mock(returncode=1, stderr="boom"),
        ):
            with self.assertRaises(pw_app_route.PwLinkUnavailable):
                pw_app_route.apply_plan(
                    RoutePlan(create=[(68, 80)]), logger=log
                )
        log.assert_called_once()
        self.assertIn("boom", log.call_args[0][0])

    def test_the_move_happens_between_the_removals_and_the_creations(self):
        """The order matters, and it is the whole design.

        A native link to the primary must be gone BEFORE the session
        manager adds its own (or the audio is doubled there), and a
        native link to the sink the session manager is vacating must be
        created AFTER the move (or it is doubled there too).
        """
        order = []
        plan = RoutePlan(create=[(68, 80)], remove=[(68, 45)], primary="tgt_b")

        def fake_link(args, logger=None):
            order.append(("link", args[0]))
            return mock.Mock(returncode=0)

        with mock.patch.object(pw_app_route, "_run_pw_link", side_effect=fake_link):
            pw_app_route.apply_plan(
                plan, move=lambda sink: order.append(("move", sink))
            )
        self.assertEqual(
            [o[0] for o in order], ["link", "move", "link"]
        )

    def test_no_move_when_the_primary_is_already_where_the_app_is(self):
        """Moving a stream to the sink it is already on is a no-op, and
        it can fail for unrelated reasons -- so do not do it."""
        plan = RoutePlan(primary="tgt_a", needs_move=False)
        moved = []
        with mock.patch.object(
            pw_app_route, "_run_pw_link", return_value=mock.Mock(returncode=0)
        ):
            pw_app_route.apply_plan(plan, move=lambda s: moved.append(s))
        self.assertEqual(moved, [])


if __name__ == "__main__":
    unittest.main()
