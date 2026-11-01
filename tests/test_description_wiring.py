"""Does the Description field actually reach PulseAudio?

It used to be accepted by `_create_null_sink` and dropped: the docstring
said "unused in command, kept for API compat". So the GUI's Description
field appeared to do nothing, and user feedback reasonably asked whether
it should be removed.

Verified end to end against a real server in test_description_reaches_pactl.
Here we pin the property merge without needing one.
"""

import unittest
from unittest import mock

from pulsar.utils.pactl_runner import PactlRunner


def built_command(**kwargs):
    """Run create_sink_only and return the argv pactl was given."""
    captured = {}

    def fake_run(cmd, logger=None):
        captured["cmd"] = list(cmd)
        return "", 0

    with mock.patch.object(PactlRunner, "run_command", staticmethod(fake_run)):
        PactlRunner.create_sink_only("test_sink", **kwargs)
    return captured["cmd"]


def sink_properties(cmd):
    for part in cmd:
        if part.startswith("sink_properties="):
            return part.split("=", 1)[1]
    return ""


class TestDescriptionReachesPulse(unittest.TestCase):
    def test_description_is_passed_as_device_description(self):
        props = sink_properties(
            built_command(description="My Rig"))
        # PulseAudio cannot represent a space in device.description --
        # the parser splits on whitespace with no quoting or escaping --
        # so the word is joined with underscores rather than silently
        # truncated to "My".
        self.assertIn("device.description=My_Rig", props)

    def test_the_marker_still_survives_the_description(self):
        """The ownership marker must not be displaced by the new property."""
        props = sink_properties(built_command(description="My Rig"))
        self.assertIn(PactlRunner.MANAGED_PROPERTY, props)

    def test_spaces_become_underscores_not_truncation(self):
        """Truncating at the first space would silently lose the rest."""
        props = sink_properties(
            built_command(description="Game Output For Streaming"))
        self.assertIn("device.description=Game_Output_For_Streaming", props)

    def test_no_description_means_no_empty_property(self):
        props = sink_properties(built_command(description=""))
        self.assertNotIn("device.description=", props)

    def test_a_caller_property_is_not_overwritten_by_the_description(self):
        """An explicit device.description in sink_properties wins.

        Otherwise this change would silently clobber a caller who had
        already set one. The caller's value is passed through untouched --
        rewriting a caller's own data is not this function's job, even
        when that caller has the same space problem.
        """
        props = sink_properties(built_command(
            description="From the form",
            sink_properties="device.description=Callers_own_value"))
        self.assertIn("device.description=Callers_own_value", props)
        self.assertNotIn("From_the_form", props)


if __name__ == "__main__":
    unittest.main()
