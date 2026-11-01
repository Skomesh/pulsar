"""
Regression tests for the legacy-migration race (issue #14).

`PresetManager._migrate_legacy` and `ProfileManager._migrate_legacy` both
migrated the SAME legacy `user_presets.json`, and both ended with:

    shutil.copyfile(old_file, self.presets_file)
    os.replace(old_file, old_file + ".migrated")

Whichever manager was constructed first copied the whole legacy file into
its own new file and renamed the source away. The second found nothing
left and returned early.

Reproduced in both orders:

    PresetManager first  -> routing_profiles.json never created, ALL
                           profiles silently lost
    ProfileManager first -> audio_presets.json never created, ALL presets
                           silently lost

Plus two more defects in the same code: the winner copied the other
manager's entries into its own file, and the legacy file's top-level keys
("presets", "profiles") were treated as entry names, so the user saw
devices called "presets" and "profiles".

These tests pin the contract: each manager takes its OWN section, and
neither destroys what it did not consume.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

# The pre-split format: one file holding both kinds of data.
LEGACY = {
    "presets": {
        "MyGame": {"channels": "2", "channel_map": "front-left,front-right",
                   "description": "My game preset"},
    },
    "profiles": {
        "Work Setup": {
            "schema_version": 2,
            "description": "work",
            "devices": [{"name": "work_sink", "type": "sink", "channels": 2,
                         "description": "work"}],
            "routing": [{"from": "work_sink", "to": "<AUTO_DEFAULT>",
                         "latency_msec": 1}],
        },
    },
}


class MigrationRaceTestCase(unittest.TestCase):
    """Builds a sandbox with a legacy CWD-relative presets directory."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pulsar_mig_")
        self.cwd = os.path.join(self._tmp, "cwd")
        # legacy_presets_dir() looks for a DIRECTORY named presets/ under
        # the working directory -- not the working directory itself.
        legacy_dir = os.path.join(self.cwd, "presets")
        os.makedirs(legacy_dir)
        with open(os.path.join(legacy_dir, "user_presets.json"), "w") as fh:
            json.dump(LEGACY, fh, indent=1)

        self.xdg = os.path.join(self._tmp, "xdg")
        self._env = mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": self.xdg,
            "HOME": self._tmp,
        })
        self._env.start()
        # os.chdir rather than patching os.getcwd. legacy_presets_dir() asks
        # pathlib for the CWD, and how that resolves is a pathlib
        # implementation detail that has moved between the versions this
        # project supports -- a getcwd mock passed on 3.14 and failed on
        # every version CI runs. Every other migration test in this repo
        # chdirs; so does this one.
        self._real_cwd = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(self._restore)

    def _restore(self):
        os.chdir(self._real_cwd)
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _read(self, name):
        path = os.path.join(self.xdg, "pulsar", "presets", name)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            return json.load(fh)


class TestPresetManagerFirst(MigrationRaceTestCase):
    def test_profiles_still_migrate(self):
        from pulsar.utils.preset_manager import PresetManager
        from pulsar.utils.profile_manager import ProfileManager

        PresetManager()
        pm = ProfileManager()

        self.assertIn("Work Setup", pm.get_profile_names(),
                      "profiles were silently lost by the preset manager "
                      "consuming the legacy file")
        # The list is exactly what the user saved. Nothing else is merged
        # in, so a migration can neither lose nor invent an entry.
        self.assertEqual(pm.get_profile_names(), ["Work Setup"])

    def test_the_preset_file_does_not_contain_profiles(self):
        from pulsar.utils.preset_manager import PresetManager

        PresetManager()
        presets = self._read("audio_presets.json")
        self.assertIsNotNone(presets)
        self.assertNotIn("profiles", presets,
                         "the preset file swallowed the profile section")

    def test_top_level_keys_do_not_become_entry_names(self):
        from pulsar.utils.preset_manager import PresetManager

        pm = PresetManager()
        names = pm.get_preset_names()
        self.assertNotIn("presets", names,
                         "the legacy wrapper key leaked in as a preset name")
        self.assertNotIn("profiles", names)
        self.assertIn("MyGame", names)


class TestProfileManagerFirst(MigrationRaceTestCase):
    def test_presets_still_migrate(self):
        from pulsar.utils.preset_manager import PresetManager
        from pulsar.utils.profile_manager import ProfileManager

        ProfileManager()
        pm = PresetManager()

        self.assertIn("MyGame", pm.get_preset_names(),
                      "presets were silently lost by the profile manager "
                      "consuming the legacy file")

    def test_the_profile_file_does_not_contain_presets(self):
        from pulsar.utils.profile_manager import ProfileManager

        ProfileManager()
        profiles = self._read("routing_profiles.json")
        self.assertIsNotNone(profiles)
        self.assertNotIn("presets", profiles)

    def test_top_level_keys_do_not_become_entry_names(self):
        from pulsar.utils.profile_manager import ProfileManager

        pm = ProfileManager()
        names = pm.get_profile_names()
        self.assertNotIn("presets", names)
        self.assertNotIn("profiles", names)
        self.assertIn("Work Setup", names)
        # Exactly the migrated profile: no wrapper key and nothing else.
        self.assertEqual(names, ["Work Setup"])


class TestFlatLegacyShape(MigrationRaceTestCase):
    """The other legacy shape: no wrapper, every top-level key an entry.

    The existing tests in tests/test_paths.py use this form, and
    test_profile_manager_migrates_too asserts a flat file can hold PROFILES
    -- so "flat means presets only" is wrong. Each manager claims only the
    entries that look like its own.

    Written without the shared setUp because that one seeds the sectioned
    shape.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pulsar_flat_")
        self.cwd = os.path.join(self._tmp, "cwd")
        legacy_dir = os.path.join(self.cwd, "presets")
        os.makedirs(legacy_dir)
        with open(os.path.join(legacy_dir, "user_presets.json"), "w") as fh:
            json.dump({
                "MyGame": {"channels": "2", "channel_map": "mono"},
                "OldProfile": {"schema_version": 2, "devices": [], "routing": []},
            }, fh)
        self.xdg = os.path.join(self._tmp, "xdg")
        self._env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.xdg,
                                                  "HOME": self._tmp})
        self._env.start()
        self._real_cwd = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(self._restore)

    def _restore(self):
        os.chdir(self._real_cwd)
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _read(self, name):
        path = os.path.join(self.xdg, "pulsar", "presets", name)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def test_each_manager_takes_only_its_own_entries(self):
        from pulsar.utils.preset_manager import PresetManager
        from pulsar.utils.profile_manager import ProfileManager

        PresetManager()
        ProfileManager()

        presets = self._read("audio_presets.json")
        profiles = self._read("routing_profiles.json")

        self.assertIsNotNone(presets, "flat presets were not migrated")
        self.assertIn("MyGame", presets)
        self.assertNotIn("OldProfile", presets,
                         "a profile leaked into the presets file")

        self.assertIsNotNone(profiles, "flat profiles were not migrated")
        self.assertIn("OldProfile", profiles)
        self.assertNotIn("MyGame", profiles,
                         "a preset leaked into the profiles file")


class TestLegacyIsNotDestroyed(MigrationRaceTestCase):
    def test_the_legacy_file_survives_both_managers_running(self):
        """The user's data must never vanish, whatever the fix does.

        Renaming it to `.migrated` is acceptable -- that is recoverable and
        points at itself. Deleting it is not.
        """
        from pulsar.utils.preset_manager import PresetManager
        from pulsar.utils.profile_manager import ProfileManager

        PresetManager()
        ProfileManager()

        legacy_dir = os.path.join(self.cwd, "presets")
        survivors = [f for f in os.listdir(legacy_dir)
                     if f.startswith("user_presets.json")]
        self.assertTrue(survivors,
                        "both sections were consumed and nothing was kept")


if __name__ == "__main__":
    unittest.main()
