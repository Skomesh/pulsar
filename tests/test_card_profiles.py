"""
Unit tests for the card profile helpers added in Phase 5c.

End-to-end tests that exercise list_cards and set_card_profile against
the running system. The profile-line parser is tested via mocked pactl
output so we don't depend on a specific system's exact profile set.
"""

import unittest
from unittest import mock

from utils.pactl_runner import PactlRunner


def pactl_available() -> bool:
    import shutil
    return shutil.which("pactl") is not None


class TestParseCardProfileLine(unittest.TestCase):
    """Test the profile-line parser on representative inputs."""

    def test_simple_profile(self):
        line = "off: Off (sinks: 0, sources: 0, priority: 0, available: yes)"
        p = PactlRunner._parse_card_profile_line(line)
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "off")
        self.assertEqual(p["description"], "Off")
        self.assertEqual(p["sinks"], 0)
        self.assertEqual(p["sources"], 0)
        self.assertEqual(p["priority"], 0)
        self.assertTrue(p["available"])

    def test_composite_profile_name_with_colons(self):
        # The tricky case: name has colons, description follows
        line = (
            "output:analog-stereo+input:mono-fallback: Analog Stereo "
            "Output + Mono Input (sinks: 1, sources: 1, priority: 6501, "
            "available: yes)"
        )
        p = PactlRunner._parse_card_profile_line(line)
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "output:analog-stereo+input:mono-fallback")
        self.assertEqual(p["description"], "Analog Stereo Output + Mono Input")
        self.assertEqual(p["sinks"], 1)
        self.assertEqual(p["sources"], 1)
        self.assertEqual(p["priority"], 6501)
        self.assertTrue(p["available"])

    def test_profile_unavailable(self):
        line = "pro-audio: Pro Audio (sinks: 1, sources: 1, priority: 1, available: no)"
        p = PactlRunner._parse_card_profile_line(line)
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "pro-audio")
        self.assertFalse(p["available"])

    def test_profile_without_parens(self):
        # Some profiles lack the parenthesized metadata
        line = "minimal: Minimal Profile"
        p = PactlRunner._parse_card_profile_line(line)
        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "minimal")
        self.assertEqual(p["description"], "Minimal Profile")
        self.assertEqual(p["sinks"], 0)
        self.assertEqual(p["available"], False)

    def test_garbage_returns_none(self):
        self.assertIsNone(PactlRunner._parse_card_profile_line(""))
        self.assertIsNone(PactlRunner._parse_card_profile_line("no_colon"))
        # Has a colon but no description and no parens — minimal parse OK
        self.assertIsNotNone(PactlRunner._parse_card_profile_line("name: desc"))


class TestListCards(unittest.TestCase):
    """Test list_cards with mocked pactl output."""

    MOCK_OUTPUT = """\
Card #50
\tName: alsa_card.usb-test
\tDriver: alsa
\tOwner Module: n/a
\tProperties:
\t\tdevice.description = "Test USB Audio"
\t\tdevice.product.name = "Test Audio"
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)
\t\toutput:analog-stereo+input:analog-stereo: Analog Stereo Duplex (sinks: 1, sources: 1, priority: 6565, available: yes)
\tActive Profile: output:analog-stereo+input:analog-stereo
\tPorts:

Card #51
\tName: alsa_card.pci-test
\tDriver: alsa
\tOwner Module: n/a
\tProperties:
\t\tdevice.description = "Test Motherboard"
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\tActive Profile: off
\tPorts:
"""

    @mock.patch("utils.pactl_runner.PactlRunner.run_command")
    def test_parses_two_cards(self, mock_run):
        # Both list cards calls return the same mock data
        mock_run.return_value = (self.MOCK_OUTPUT, 0)
        cards = PactlRunner.list_cards()
        self.assertEqual(len(cards), 2)
        # First card
        c0 = cards[0]
        self.assertEqual(c0["name"], "alsa_card.usb-test")
        self.assertEqual(c0["driver"], "alsa")
        self.assertEqual(c0["description"], "Test USB Audio")
        self.assertEqual(c0["active_profile"], "output:analog-stereo+input:analog-stereo")
        self.assertEqual(len(c0["profiles"]), 3)
        # Composite profile name parsed correctly
        composite = next(
            p for p in c0["profiles"]
            if "+" in p["name"]
        )
        self.assertEqual(
            composite["name"],
            "output:analog-stereo+input:analog-stereo",
        )
        # Second card has description from first card (device.description)
        c1 = cards[1]
        self.assertEqual(c1["name"], "alsa_card.pci-test")
        self.assertEqual(c1["description"], "Test Motherboard")
        self.assertEqual(c1["active_profile"], "off")
        self.assertEqual(len(c1["profiles"]), 1)

    @mock.patch("utils.pactl_runner.PactlRunner.run_command")
    def test_empty_when_pactl_fails(self, mock_run):
        mock_run.return_value = ("", 1)
        self.assertEqual(PactlRunner.list_cards(), [])


@unittest.skipUnless(pactl_available(), "pactl not available")
class TestSetCardProfile(unittest.TestCase):
    """End-to-end test that requires pactl."""

    def test_set_and_restore_card_profile(self):
        # Find a card with at least 2 profiles
        cards = PactlRunner.list_cards()
        target_card = None
        target_profile = None
        original_profile = None
        for card in cards:
            if len(card["profiles"]) < 2:
                continue
            for p in card["profiles"]:
                if (
                    p["name"] != "off"
                    and p["name"] != card["active_profile"]
                    and p["available"]
                ):
                    target_card = card
                    target_profile = p
                    original_profile = card["active_profile"]
                    break
            if target_card:
                break
        if target_card is None:
            self.skipTest("no card with 2+ available profiles to switch")

        # Switch
        ok = PactlRunner.set_card_profile(
            target_card["name"], target_profile["name"]
        )
        self.assertTrue(ok)
        # Verify
        new_cards = PactlRunner.list_cards()
        new_card = next(
            c for c in new_cards if c["name"] == target_card["name"]
        )
        self.assertEqual(new_card["active_profile"], target_profile["name"])

        # Restore
        PactlRunner.set_card_profile(target_card["name"], original_profile)


if __name__ == "__main__":
    unittest.main()
