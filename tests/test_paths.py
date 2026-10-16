"""
Tests for XDG data-path resolution and legacy migration.

The bug these exist for: both managers defaulted to ``presets_dir="presets"``,
resolved against the working directory. On a packaged install the CWD is a
read-only bundle and the app died on startup with PermissionError before the
window appeared. Every test here sets $XDG_DATA_HOME and asserts on the
resolved path, because a test that ran in the developer's checkout would pass
either way.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pulsar.utils.paths import (
    legacy_presets_dir,
    user_data_dir,
    user_presets_dir,
)
from pulsar.utils.preset_manager import PresetManager
from pulsar.utils.profile_manager import ProfileManager


class _XDGTestBase(unittest.TestCase):
    """Runs each test with $XDG_DATA_HOME pointed at a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.xdg = self._tmp.name
        self._cwd = os.getcwd()
        self._old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.xdg

    def tearDown(self):
        os.chdir(self._cwd)
        if self._old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old
        self._tmp.cleanup()


class TestUserDataDir(_XDGTestBase):
    def test_honours_xdg_data_home(self):
        self.assertEqual(user_data_dir(), Path(self.xdg) / "pulsar")

    def test_creates_the_directory(self):
        user_data_dir()
        self.assertTrue((Path(self.xdg) / "pulsar").is_dir())

    def test_falls_back_to_local_share_when_unset(self):
        os.environ.pop("XDG_DATA_HOME", None)
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}):
            self.assertEqual(
                user_data_dir(), Path("/home/someone/.local/share/pulsar")
            )

    def test_presets_dir_is_under_data_dir(self):
        self.assertEqual(
            user_presets_dir(), Path(self.xdg) / "pulsar" / "presets"
        )

    def test_unwritable_data_home_does_not_raise(self):
        """A read-only XDG home must not stop the app from starting.

        Reads of bundled presets still need to work; only writes fail, and
        they fail with a real path in the error.
        """
        with mock.patch("pulsar.utils.paths.Path.mkdir", side_effect=OSError("read-only")):
            self.assertEqual(user_data_dir(), Path(self.xdg) / "pulsar")


class TestManagersUseXDGNotCwd(_XDGTestBase):
    def setUp(self):
        super().setUp()
        # Somewhere unwritable, to prove nothing is created there.
        self.workdir = Path(tempfile.mkdtemp())
        self.workdir.chmod(0o555)

    def test_preset_manager_writes_under_xdg(self):
        os.chdir(self.workdir)
        pm = PresetManager()
        self.assertEqual(Path(pm.presets_dir), user_presets_dir())

    def test_profile_manager_writes_under_xdg(self):
        os.chdir(self.workdir)
        pm = ProfileManager()
        self.assertEqual(Path(pm.presets_dir), user_presets_dir())

    def test_no_directory_created_in_cwd(self):
        os.chdir(self.workdir)
        PresetManager()
        ProfileManager()
        self.assertEqual(list(self.workdir.iterdir()), [])

    def test_explicit_argument_still_wins(self):
        """Tests and embedders pass their own dir; that must not change."""
        target = tempfile.mkdtemp()
        pm = PresetManager(presets_dir=target)
        self.assertEqual(Path(pm.presets_dir), Path(target))

    def test_the_two_managers_do_not_share_a_file(self):
        """Different schemas behind one filename lost data on every save."""
        pm = PresetManager()
        fm = ProfileManager()
        self.assertNotEqual(pm.presets_file, fm.presets_file)

    def test_saving_presets_and_profiles_both_succeed(self):
        os.chdir(self.workdir)
        pm = PresetManager()
        fm = ProfileManager()
        self.assertTrue(pm.save_preset("p", {"channels": "2"}))
        self.assertTrue(
            fm.save_profile("f", {"devices": [], "routing": []})
        )
        self.assertTrue(Path(pm.presets_file).exists())
        self.assertTrue(Path(fm.presets_file).exists())


class TestLegacyMigration(_XDGTestBase):
    def _make_legacy(self, content):
        workdir = tempfile.mkdtemp()
        legacy = Path(workdir) / "presets"
        legacy.mkdir()
        (legacy / "user_presets.json").write_text(json.dumps(content))
        return workdir, legacy

    def test_migrates_existing_legacy_file(self):
        workdir, _legacy = self._make_legacy({"Old": {"channels": "6"}})
        os.chdir(workdir)
        pm = PresetManager()
        self.assertIsNotNone(pm.get_preset("Old"))

    def test_migrated_file_lands_in_xdg(self):
        workdir, _ = self._make_legacy({"Old": {"channels": "6"}})
        os.chdir(workdir)
        PresetManager()
        migrated = user_presets_dir() / "audio_presets.json"
        self.assertTrue(migrated.exists())
        self.assertIn("Old", json.loads(migrated.read_text()))

    def test_legacy_file_is_kept_as_a_backup(self):
        """Deleting the user's only copy is not an acceptable migration."""
        workdir, legacy = self._make_legacy({"Old": {"channels": "6"}})
        os.chdir(workdir)
        PresetManager()
        self.assertTrue((legacy / "user_presets.json.migrated").exists())

    def test_does_not_overwrite_a_newer_xdg_file(self):
        """Second launch must not undo the user's edits."""
        workdir, _ = self._make_legacy({"Old": {"channels": "6"}})
        os.chdir(workdir)
        PresetManager()
        current = user_presets_dir() / "audio_presets.json"
        current.write_text(json.dumps({"Edited": {"channels": "8"}}))
        PresetManager()
        self.assertIn("Edited", json.loads(current.read_text()))
        self.assertNotIn("Old", json.loads(current.read_text()))

    def test_no_legacy_dir_is_not_an_error(self):
        """Construction must succeed whether or not there is anything to migrate."""
        pm = PresetManager()
        self.assertTrue(Path(pm.presets_dir).is_dir())
        # Nothing saved yet, so no file should exist yet either.
        self.assertFalse(Path(pm.presets_file).exists())
        self.assertTrue(pm.save_preset("first", {"channels": "2"}))
        self.assertTrue(Path(pm.presets_file).exists())

    def test_legacy_presets_dir_finds_nothing_when_absent(self):
        # Point HOME at an empty tree too: the legacy lookup checks both the
        # CWD and ~/.local/share/pulsar/presets, so only an isolated HOME
        # proves neither candidate matched.
        empty = tempfile.mkdtemp()
        home = tempfile.mkdtemp()
        os.chdir(empty)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertIsNone(legacy_presets_dir())

    def test_profile_manager_migrates_too(self):
        workdir, _ = self._make_legacy({"OldProfile": {"devices": []}})
        os.chdir(workdir)
        ProfileManager()
        migrated = user_presets_dir() / "routing_profiles.json"
        self.assertTrue(migrated.exists())


if __name__ == "__main__":
    unittest.main()
