"""
Module ids are reused, so unloading by a captured id removes the wrong
thing.

Found while chasing an unrelated symptom: the machine's default output
(auto_null, provided by module-always-sink) disappeared during a test
run, and several profile tests then skipped themselves because there was
no hardware output left to route to.

PipeWire frees a module id when a module is unloaded, and later reuses
it. Observed inside one test run:

    UNLOAD id=536870920 -> module-null-sink | sink_name=cap_opt
    UNLOAD id=536870920 -> module-null-sink | sink_name=cap_dup
    UNLOAD id=536870920 -> module-null-sink | sink_name=cap_lb
    UNLOAD id=536870920 -> module-null-sink | sink_name=cap_sink
    UNLOAD id=536870920 -> module-null-sink | sink_name=cap_src

Five different modules, one id. An id recorded at creation time
therefore points at something else entirely by the time it is used, and
`pactl unload-module <id>` removes THAT.

These tests pin the identity-based unload: a null-sink is found by its
sink_name, a loopback by its two endpoints, and neither relies on an id
that may since have been reused.
"""

import shutil
import unittest

from pulsar.utils.pactl_runner import PactlRunner


def pactl_available() -> bool:
    return shutil.which("pactl") is not None


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestUnloadByIdentity(unittest.TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        for name in self._created:
            PactlRunner.unload_null_sink_by_name(name, require_managed=False)

    def _make_sink(self, name, managed=True):
        props = "pulsar.managed=true" if managed else ""
        cmd = ["pactl", "load-module", "module-null-sink",
               "media.class=Audio/Sink", f"sink_name={name}", "channels=2"]
        if props:
            cmd.append(f"sink_properties={props}")
        import subprocess
        subprocess.run(cmd, capture_output=True, check=False)
        self._created.append(name)
        return name

    def _sink_exists(self, name):
        return name in {s.get("name") for s in PactlRunner.list_sinks()}

    def test_unload_by_name_removes_the_named_sink(self):
        self._make_sink("ident_a")
        self.assertTrue(self._sink_exists("ident_a"))
        self.assertTrue(
            PactlRunner.unload_null_sink_by_name("ident_a", require_managed=False))
        self.assertFalse(self._sink_exists("ident_a"))

    def test_unload_by_name_leaves_other_sinks_alone(self):
        """The whole point: one sink goes, nothing else does."""
        self._make_sink("ident_b")
        self._make_sink("ident_c")
        before = {s.get("name") for s in PactlRunner.list_sinks()}
        PactlRunner.unload_null_sink_by_name(
            "ident_b", require_managed=False)
        after = {s.get("name") for s in PactlRunner.list_sinks()}
        self.assertEqual(before - after, {"ident_b"})
        self.assertIn("ident_c", after)

    def test_unload_by_name_refuses_an_unmanaged_sink(self):
        """The ownership marker still applies to this path."""
        self._make_sink("ident_foreign", managed=False)
        self.assertTrue(self._sink_exists("ident_foreign"))
        self.assertFalse(
            PactlRunner.unload_null_sink_by_name("ident_foreign"))
        self.assertTrue(
            self._sink_exists("ident_foreign"),
            "an unmanaged sink was removed by the managed-only path")

    def test_unload_by_name_does_not_match_a_prefix(self):
        """'ident_x' must not match a sink called 'ident_xy'."""
        self._make_sink("ident_xy")
        PactlRunner.unload_null_sink_by_name("ident_x", require_managed=False)
        self.assertTrue(
            self._sink_exists("ident_xy"),
            "a substring match removed the wrong sink")

    def test_unload_by_name_survives_id_reuse(self):
        """The id captured at creation may be reused; the name is not."""
        self._make_sink("ident_d")
        # Free some ids by creating and removing other sinks, so the id
        # that ident_d was given is likely to be handed out again.
        for i in range(6):
            name = f"ident_churn_{i}"
            self._make_sink(name)
            PactlRunner.unload_null_sink_by_name(name, require_managed=False)
        # ident_d must still be removable, and by name.
        self.assertTrue(self._sink_exists("ident_d"))
        self.assertTrue(
            PactlRunner.unload_null_sink_by_name("ident_d", require_managed=False))
        self.assertFalse(self._sink_exists("ident_d"))

    def test_the_default_output_survives_a_removal(self):
        """The bug's real symptom: the default output vanished.

        module-always-sink provides auto_null. If a stale id pointed at
        it, unloading took the user's default output with it.
        """
        default = PactlRunner.get_default_sink()
        if not default:
            self.skipTest("no default sink on this machine")
        self._make_sink("ident_e")
        PactlRunner.unload_null_sink_by_name("ident_e", require_managed=False)
        self.assertTrue(
            self._sink_exists(default),
            f"the default output {default!r} was removed by an unrelated "
            f"cleanup")


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestLoopbackUnloadByEndpoints(unittest.TestCase):
    def test_unload_by_endpoints_removes_the_right_loopback(self):
        import subprocess
        subprocess.run(
            ["pactl", "load-module", "module-null-sink", "media.class=Audio/Sink",
             "sink_name=lb_ident", "channels=2"],
            capture_output=True, check=False)
        try:
            default = PactlRunner.get_default_sink()
            if not default:
                self.skipTest("no default sink")
            out = subprocess.run(
                ["pactl", "load-module", "module-loopback",
                 "source=lb_ident.monitor", f"sink={default}",
                 "latency_msec=1", "sink_dont_move=true"],
                capture_output=True, text=True)
            if not out.stdout.strip():
                self.skipTest("could not create a loopback here")

            found = [
                lb for lb in PactlRunner.list_loopbacks()
                if lb.get("source") == "lb_ident.monitor"
            ]
            self.assertEqual(len(found), 1)
            self.assertTrue(
                PactlRunner.unload_loopback_by_endpoints(
                    "lb_ident.monitor", default))
            self.assertEqual(
                [lb for lb in PactlRunner.list_loopbacks()
                 if lb.get("source") == "lb_ident.monitor"],
                [],
                "the loopback was not removed")
        finally:
            PactlRunner.unload_null_sink_by_name("lb_ident", require_managed=False)

    def test_unload_by_endpoints_leaves_other_loopbacks_alone(self):
        import subprocess
        subprocess.run(
            ["pactl", "load-module", "module-null-sink", "media.class=Audio/Sink",
             "sink_name=lb_keep", "channels=2"],
            capture_output=True, check=False)
        try:
            default = PactlRunner.get_default_sink()
            if not default:
                self.skipTest("no default sink")
            made = []
            for name in ("lb_keep", "lb_drop"):
                out = subprocess.run(
                    ["pactl", "load-module", "module-loopback",
                     f"source={name}.monitor", f"sink={default}",
                     "latency_msec=1", "sink_dont_move=true"],
                    capture_output=True, text=True)
                if out.stdout.strip():
                    made.append(name)
            if len(made) < 2:
                self.skipTest("could not create two loopbacks here")

            PactlRunner.unload_loopback_by_endpoints(
                "lb_drop.monitor", default)
            remaining = {
                lb.get("source") for lb in PactlRunner.list_loopbacks()
            }
            self.assertNotIn("lb_drop.monitor", remaining)
            self.assertIn("lb_keep.monitor", remaining)
        finally:
            PactlRunner.unload_null_sink_by_name("lb_keep", require_managed=False)


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestRollbackUsesIdentityNotStaleIds(unittest.TestCase):
    """The rollback must not use a module id captured at creation time.

    The id-reuse hazard is timing-dependent, so a test that exercises it
    end to end is unreliable -- it may pass simply because nothing
    reused the id at that moment. This pins the behaviour directly
    instead: the rollback is asked to remove things BY IDENTITY.

    Reverting this to the id-based call is caught here immediately,
    without depending on the server's id allocation.
    """

    def test_rollback_removes_devices_by_name(self):
        from unittest import mock

        from pulsar.utils.profile_manager import ProfileManager

        pm = ProfileManager(presets_dir="/tmp")
        result = {
            "created_devices": [{"name": "rb_sink", "module_id": "12345"}],
            "created_loopbacks": [],
        }
        with mock.patch.object(
                PactlRunner, "unload_null_sink_by_name") as by_name, \
                mock.patch.object(
                    PactlRunner, "unload_module") as by_id:
            pm._rollback(result)

        self.assertTrue(
            by_name.called, "the rollback did not remove the sink by name")
        self.assertEqual(by_name.call_args[0][0], "rb_sink")
        self.assertFalse(
            by_id.called,
            "the rollback still unloads by the captured module id, which "
            "PipeWire may have reused for an unrelated module")

    def test_rollback_removes_loopbacks_by_endpoints(self):
        from unittest import mock

        from pulsar.utils.profile_manager import ProfileManager

        pm = ProfileManager(presets_dir="/tmp")
        result = {
            "created_devices": [],
            "created_loopbacks": [
                {"from": "rb_sink", "to": "speakers", "module_id": "999"},
            ],
        }
        with mock.patch.object(
                PactlRunner, "unload_loopback_by_endpoints") as by_endpoints, \
                mock.patch.object(
                    PactlRunner, "unload_loopback") as by_id:
            pm._rollback(result)

        self.assertTrue(
            by_endpoints.called,
            "the rollback did not remove the loopback by its endpoints")
        self.assertEqual(by_endpoints.call_args[0][0], "rb_sink.monitor")
        self.assertEqual(by_endpoints.call_args[0][1], "speakers")
        self.assertFalse(
            by_id.called,
            "the rollback still unloads the loopback by the captured module "
            "id, which PipeWire may have reused")


if __name__ == "__main__":
    unittest.main()
