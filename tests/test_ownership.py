"""
Tests for ownership-scoped removal of virtual devices.

The bug these exist for: `unload_all_null_sinks` unloaded every
module-null-sink on the machine. On a desktop that is not a minor
overreach -- a user's Steam Streaming sink, OBS's dummy output and
anything module-null-sink itself loaded all disappeared, and because
null sinks are not persistent they did not come back.

The fix is a marker property on every sink Pulsar creates, and removal
scoped to modules carrying it.

Every test here runs against the live audio server. The subject is pactl
argument handling, and a mock would agree with whatever the code
assumed -- which is how the original bug survived in the first place.
Each test creates its own uniquely-named sinks and removes exactly those
in tearDown, so running the suite cannot destroy anything else.
"""

import unittest

from pulsar.utils.pactl_runner import PactlRunner

#: Unique-ish per run so a crashed previous run cannot collide.
SUFFIX = "ownertest"

MANAGED = f"ownr_managed_{SUFFIX}"
FOREIGN = f"ownr_foreign_{SUFFIX}"


def module_args_for(name):
    """The raw argument string pactl reports for a sink, or None."""
    for m in PactlRunner.list_modules():
        if f"sink_name={name} " in m.get("argument", "") or \
                m.get("argument", "").strip().endswith(f"sink_name={name}"):
            return m.get("argument", "")
    return None


class TestOwnershipMarker(unittest.TestCase):
    def setUp(self):
        self.cleanup()

    def tearDown(self):
        self.cleanup()

    def cleanup(self):
        for name in (MANAGED, FOREIGN):
            args = module_args_for(name)
            if not args:
                continue
            for m in PactlRunner.list_modules():
                if f"sink_name={name} " in m.get("argument", "") or \
                        m.get("argument", "").strip().endswith(f"sink_name={name}"):
                    PactlRunner.unload_module(m.get("id", ""))

    # --- creating ------------------------------------------------------

    def test_created_sink_carries_the_marker(self):
        self.assertTrue(PactlRunner.create_sink_only(MANAGED, "managed"))
        args = module_args_for(MANAGED)
        self.assertIsNotNone(args, "sink was not created")
        self.assertIn(PactlRunner.MANAGED_PROPERTY, args)

    def test_marker_survives_alongside_caller_supplied_properties(self):
        """A caller passing their own sink_properties must not lose the marker,
        or the device becomes un-removable by this program."""
        self.assertTrue(PactlRunner.create_sink_only(
            MANAGED, "managed", sink_properties="device.api=game"))
        args = module_args_for(MANAGED)
        self.assertIn(PactlRunner.MANAGED_PROPERTY, args)
        self.assertIn("device.api=game", args)

    def test_source_only_sink_is_marked_too(self):
        self.assertTrue(PactlRunner.create_source_only(MANAGED, "managed"))
        args = module_args_for(MANAGED)
        self.assertIsNotNone(args)
        self.assertIn(PactlRunner.MANAGED_PROPERTY, args)

    def test_duplex_sink_is_marked_too(self):
        self.assertTrue(PactlRunner.create_duplex_sink(MANAGED, "managed"))
        args = module_args_for(MANAGED)
        self.assertIsNotNone(args)
        self.assertIn(PactlRunner.MANAGED_PROPERTY, args)

    # --- removing ------------------------------------------------------

    def test_removal_spares_a_sink_pulsar_did_not_create(self):
        """The whole point: someone else's null sink is not ours to delete."""
        # Loaded by hand, exactly as another program would.
        out, rc = PactlRunner.run_command([
            "load-module", "module-null-sink",
            "media.class=Audio/Sink", f"sink_name={FOREIGN}", "channels=2",
        ])
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(module_args_for(FOREIGN), "setup failed")

        count, errors = PactlRunner.unload_all_null_sinks()

        self.assertEqual(errors, [])
        self.assertIsNotNone(
            module_args_for(FOREIGN),
            "a null sink Pulsar did not create was unloaded anyway",
        )

    def test_removal_still_removes_our_own_sink(self):
        self.assertTrue(PactlRunner.create_sink_only(MANAGED, "managed"))
        self.assertIsNotNone(module_args_for(MANAGED))

        count, errors = PactlRunner.unload_all_null_sinks()

        self.assertEqual(errors, [])
        self.assertGreaterEqual(count, 1)
        self.assertIsNone(module_args_for(MANAGED), "our own sink survived")

    def test_explicit_override_still_removes_foreign_sinks(self):
        """The old behaviour stays reachable, but only when asked for."""
        PactlRunner.run_command([
            "load-module", "module-null-sink",
            "media.class=Audio/Sink", f"sink_name={FOREIGN}", "channels=2",
        ])
        self.assertIsNotNone(module_args_for(FOREIGN))

        count, errors = PactlRunner.unload_all_null_sinks(only_managed=False)

        self.assertEqual(errors, [])
        self.assertIsNone(module_args_for(FOREIGN))


class TestProfileApplyScoping(unittest.TestCase):
    """Applying a profile used to wipe everyone's null sinks on the way in."""

    def test_profile_apply_leaves_foreign_sinks_alone(self):
        PactlRunner.run_command([
            "load-module", "module-null-sink",
            "media.class=Audio/Sink", f"sink_name={FOREIGN}", "channels=2",
        ])
        try:
            from pulsar.utils.profile_manager import ProfileManager

            profile = {
                "schema_version": 2,
                "description": "owner scope probe",
                "devices": [],
                "routing": [],
            }
            ProfileManager().apply_profile(profile)
            self.assertIsNotNone(
                module_args_for(FOREIGN),
                "apply_profile destroyed a sink Pulsar did not create",
            )
        finally:
            args = module_args_for(FOREIGN)
            if args:
                for m in PactlRunner.list_modules():
                    if f"sink_name={FOREIGN} " in m.get("argument", ""):
                        PactlRunner.unload_module(m.get("id", ""))


if __name__ == "__main__":
    unittest.main()
