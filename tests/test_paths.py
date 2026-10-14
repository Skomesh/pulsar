"""Regression tests for preset path resolution.

These cover a class of bug that is completely silent at runtime: the built-in
profile list came up empty, or user profiles landed somewhere unreachable,
because the code resolved a *relative* ``presets`` path against whatever the
current working directory happened to be.

That is why every test here runs without ``pactl`` -- the failures were in path
resolution and packaging, not in the audio layer. A test that needs a running
audio server cannot catch them.

Two behaviours are pinned:

1. ``user_presets_dir()`` is absolute and honours ``PULSAR_PRESETS_DIR`` /
   ``XDG_DATA_HOME``, so a profile saved from any cwd is findable again.
2. ``builtin_profiles_path()`` resolves the packaged starter profiles, so they
   survive installation rather than only working from a source checkout.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config.paths import (
    PRESETS_DIR_ENV,
    builtin_presets_path,
    user_presets_dir,
)
from utils.profile_manager import ProfileManager


class TestUserPresetsDir(unittest.TestCase):
    """The writable presets dir must not depend on the cwd."""

    def test_default_is_absolute(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("pathlib.Path.home", return_value=Path("/home/tester")):
                result = Path(user_presets_dir())
        self.assertTrue(result.is_absolute(), f"{result} should be absolute")
        self.assertEqual(result.parts[:4], ("/", "home", "tester", ".local"))

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {PRESETS_DIR_ENV: tmp}, clear=True):
                self.assertEqual(Path(user_presets_dir()), Path(tmp))

    def test_xdg_data_home_respected(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}, clear=True):
            result = Path(user_presets_dir())
        self.assertEqual(result, Path("/xdg/pulsar/presets"))

    def test_env_override_beats_xdg(self):
        with mock.patch.dict(
            os.environ,
            {PRESETS_DIR_ENV: "/override", "XDG_DATA_HOME": "/xdg"},
            clear=True,
        ):
            self.assertEqual(Path(user_presets_dir()), Path("/override"))

    def test_independent_of_cwd(self):
        """Two different cwds must resolve to the same directory.

        This is the regression that matters: the old default was the relative
        string "presets", so the answer changed with the working directory and
        saved profiles went wherever the app happened to be launched from.
        """
        here = Path.cwd()
        first = Path(tempfile.mkdtemp(prefix="pulsar_cwd_a_"))
        second = Path(tempfile.mkdtemp(prefix="pulsar_cwd_b_"))
        with mock.patch.dict(os.environ, {PRESETS_DIR_ENV: "/stable"}, clear=True):
            os.chdir(first)
            a = user_presets_dir()
            os.chdir(second)
            b = user_presets_dir()
        os.chdir(here)
        self.assertEqual(a, b)


class TestBuiltinPresetsPath(unittest.TestCase):
    """The bundled starter profiles must resolve in any install layout."""

    def test_finds_packaged_builtin_profiles(self):
        path = builtin_presets_path()
        self.assertTrue(path.is_file(), f"expected {path} to exist")
        self.assertEqual(path.name, "builtin_profiles.json")

    def test_builtin_profiles_is_valid_non_empty_json(self):
        with open(builtin_presets_path()) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertTrue(data, "bundled builtin_profiles.json should not be empty")

    def test_profile_manager_loads_builtins_without_pactl(self):
        """Built-ins load through ProfileManager with no audio server present.

        Deliberately not gated on pactl: the original bug (walk up from
        __file__ to a presumed repo root) returned nothing under a real
        install, and no amount of audio-server testing would have caught it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProfileManager(presets_dir=tmp)
            profiles = pm.load_all_profiles()
        self.assertTrue(
            profiles, "built-in starter profiles should load with no user profiles"
        )


class TestUserPresetsRoundTrip(unittest.TestCase):
    """A profile saved from an arbitrary cwd must load back from another."""

    def test_round_trip_across_directories(self):
        here = Path.cwd()
        store = tempfile.mkdtemp(prefix="pulsar_store_")
        other = tempfile.mkdtemp(prefix="pulsar_cwd_")
        try:
            with mock.patch.dict(os.environ, {PRESETS_DIR_ENV: store}, clear=True):
                os.chdir(other)
                pm = ProfileManager()
                pm.save_profile(
                    "demo",
                    {
                        "description": "saved from an unrelated cwd",
                        "devices": [
                            {"name": "game_sink", "type": "sink", "channels": 2}
                        ],
                        "routing": [],
                    },
                )
                self.assertTrue(Path(pm.presets_file).is_file())

                os.chdir(here)
                reloaded = ProfileManager().load_all_profiles()
            self.assertIn("demo", reloaded)
        finally:
            os.chdir(here)
            import shutil

            shutil.rmtree(store, ignore_errors=True)
            shutil.rmtree(other, ignore_errors=True)


class TestLegacyMigration(unittest.TestCase):
    """Profiles saved at the old cwd-relative location must not be orphaned.

    Before the XDG move, presets were written to ``presets/user_presets.json``
    relative to the working directory. Moving the location without migrating
    silently hides a user's saved routing: the file survives on disk, but the
    app no longer reads it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_migrate_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _legacy_file(self, contents) -> Path:
        """Point the legacy-path probe at a temp file and write `contents`."""
        target = self.tmp / "legacy_user_presets.json"
        target.write_text(json.dumps(contents))
        return target

    def _patch_legacy(self, target: Path):
        return mock.patch(
            "utils.profile_manager.legacy_user_presets_paths",
            return_value=[target],
        )

    def test_adopts_legacy_profiles_when_new_location_is_empty(self):
        legacy = self._legacy_file({"MY-OLD-SETUP": {"devices": [], "routing": []}})
        store = self.tmp / "new"

        with self._patch_legacy(legacy):
            pm = ProfileManager(presets_dir=str(store))

        self.assertEqual(pm.last_migration_source, str(legacy))
        reloaded = json.loads(Path(pm.presets_file).read_text())
        self.assertIn("MY-OLD-SETUP", reloaded)

    def test_never_overwrites_existing_profiles(self):
        """A profile saved at the new location must win over a legacy file."""
        legacy = self._legacy_file({"LEGACY": {"devices": [], "routing": []}})
        store = self.tmp / "new"
        store.mkdir()
        (store / "user_presets.json").write_text(
            json.dumps({"CURRENT": {"devices": [], "routing": []}})
        )

        with self._patch_legacy(legacy):
            pm = ProfileManager(presets_dir=str(store))

        self.assertIsNone(pm.last_migration_source)
        reloaded = json.loads(Path(pm.presets_file).read_text())
        self.assertIn("CURRENT", reloaded)
        self.assertNotIn("LEGACY", reloaded)

    def test_leaves_the_original_file_in_place(self):
        """Migration copies; it must not delete or truncate the source."""
        legacy = self._legacy_file({"KEEP-ME": {"devices": [], "routing": []}})
        store = self.tmp / "new"

        with self._patch_legacy(legacy):
            ProfileManager(presets_dir=str(store))

        self.assertTrue(legacy.is_file(), "migration must not delete the original")
        self.assertIn("KEEP-ME", json.loads(legacy.read_text()))

    def test_ignores_missing_and_corrupt_legacy_files(self):
        store = self.tmp / "new"

        with self._patch_legacy(self.tmp / "does_not_exist.json"):
            pm = ProfileManager(presets_dir=str(store))
        self.assertIsNone(pm.last_migration_source)

        corrupt = self.tmp / "corrupt.json"
        corrupt.write_text("{not valid json")
        with self._patch_legacy(corrupt):
            pm = ProfileManager(presets_dir=str(store))
        self.assertIsNone(pm.last_migration_source)

    def test_ignores_an_empty_legacy_file(self):
        legacy = self._legacy_file({})
        store = self.tmp / "new"

        with self._patch_legacy(legacy):
            pm = ProfileManager(presets_dir=str(store))

        self.assertIsNone(pm.last_migration_source)


class TestPathsArePackaged(unittest.TestCase):
    """Guards the packaging fix that caused the built-ins to vanish.

    Checks pyproject declares main.py and the presets directory, since
    omitting either made the installed `pulsar` script die with
    ModuleNotFoundError and dropped builtin_profiles.json from the wheel.
    """

    def test_pyproject_declares_main_module_and_package_data(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        text = pyproject.read_text()
        self.assertIn('py-modules = ["main"]', text)
        self.assertIn("[tool.setuptools.package-data]", text)

    def test_packaged_builtin_profiles_lives_under_src(self):
        """The JSON must sit inside the package to end up in the wheel."""
        packaged = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "utils"
            / "presets"
            / "builtin_profiles.json"
        )
        self.assertTrue(
            packaged.is_file(),
            f"{packaged} must exist for the built-in profiles to ship",
        )


if __name__ == "__main__":
    unittest.main()