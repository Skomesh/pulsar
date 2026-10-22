"""
Data-safety tests: a failed or hostile write must not destroy saved data.

Found by review, three related defects in how the managers persist JSON:

1. `_save_raw` / `_save_user_presets` open the target with mode 'w', which
   TRUNCATES before `json.dump` writes a single byte. A write that fails
   partway (ENOSPC, EIO, a quota, an NFS remount) leaves unparseable
   garbage where the user's presets used to be, and there is no .bak to
   restore from.

2. The loaders return `{}` on a decode error, so the corruption is
   invisible -- until the next save, which writes `{}` plus the new entry
   over the only surviving copy and reports success.

3. Neither loader checks that the parsed top-level object is a dict. A
   JSON list where a dict belongs propagates an uncaught exception into
   the UI, and MainWindow.__init__ calls refresh_preset_list() with no
   try/except, so the GUI does not start at all.

Every test here uses its own XDG_DATA_HOME under a temp dir.
"""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from pulsar.utils.preset_manager import PresetManager
from pulsar.utils.profile_manager import ProfileManager


class DataDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pulsar_data_")
        self.data_home = os.path.join(self._tmp, "xdg")
        os.makedirs(self.data_home, exist_ok=True)

        self._patchers = [
            mock.patch.dict(os.environ, {
                "XDG_DATA_HOME": self.data_home,
                "HOME": self._tmp,
            }),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        # Restore write permission before removing, or a read-only-dir test
        # leaves a tree that resists cleanup.
        for root, dirs, _files in os.walk(self._tmp):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), stat.S_IRWXU)
                except OSError:
                    pass
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def seed_preset_file(self, data):
        path = os.path.join(self.data_home, "pulsar", "presets",
                            "audio_presets.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def seed_profile_file(self, data):
        path = os.path.join(self.data_home, "pulsar", "presets",
                            "routing_profiles.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path


class TestFailedWritePreservesData(DataDirTestCase):
    def test_a_failed_save_leaves_the_old_file_intact(self):
        self.seed_preset_file({"Existing": {"channels": "6"}})
        pm = PresetManager()


        def exploding_dump(obj, fp, **kwargs):
            fp.write('{"Existing": {"chan')   # partial write, then die
            raise OSError(28, "No space left on device")

        with mock.patch("json.dump", side_effect=exploding_dump):
            ok = pm.save_preset("New", {"channels": "2"})

        self.assertFalse(ok, "a failed save must not report success")

        # The point of the test: the ORIGINAL data is still readable.
        with open(os.path.join(self.data_home, "pulsar", "presets",
                               "audio_presets.json")) as fh:
            saved = json.load(fh)
        self.assertIn("Existing", saved,
                      "the previous contents were destroyed by a failed write")

    def test_failed_profile_save_preserves_data_too(self):
        self.seed_profile_file({"Old": {"schema_version": 2, "devices": [],
                                        "routing": []}})
        pm = ProfileManager()

        def exploding_dump(obj, fp, **kwargs):
            fp.write('{"Old": {"schema')
            raise OSError(28, "No space left on device")

        with mock.patch("json.dump", side_effect=exploding_dump):
            ok = pm.save_profile("New", {"schema_version": 2, "devices": [],
                                          "routing": []})
        self.assertFalse(ok)
        with open(os.path.join(self.data_home, "pulsar", "presets",
                               "routing_profiles.json")) as fh:
            saved = json.load(fh)
        self.assertIn("Old", saved)


class TestCorruptFileIsNotSilentlyDestroyed(DataDirTestCase):
    def _corrupt(self):
        path = self.seed_preset_file({"Existing": {"channels": "2"}})
        with open(path, "w") as fh:
            fh.write("{ this is not json")
        return path

    def test_a_corrupt_file_is_reported_not_silently_ignored(self):
        """Silently dropping the user's data is worse than crashing.

        The loader used to print `Error loading presets: ...`; replacing
        it with a bare read_json_dict dropped that, so a corrupt file
        vanished from the list with nothing said at all.
        """
        self._corrupt()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            PresetManager().get_preset_names()
        said = buf.getvalue()
        self.assertIn("WARNING", said,
                      "the broken file was not flagged to the user")

    def test_the_path_is_in_the_report_so_the_file_can_be_found(self):
        """The user has to be able to find it. This is the fix for 'unsure
        where the profiles are saved'."""
        path = self._corrupt()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            PresetManager().get_preset_names()
        self.assertIn(str(path), buf.getvalue(),
                      "the report does not say WHICH file is broken")

    def test_the_original_contents_are_recoverable_after_a_save(self):
        """THE contract: a save must never destroy unreadable data.

        Two acceptable routes, and the test permits only these:
          * the save refuses, leaving the file untouched, or
          * the save moves the broken file aside first, then writes.
        What is NOT acceptable is overwriting it -- which is what happened
        until user testing caught it, and what an earlier version of this
        test permitted because it accepted either outcome.

        Checked by asserting on the BYTES, not on the return value: the
        return value is an implementation detail, the surviving data is
        the promise.
        """
        self._corrupt()
        pm = PresetManager()
        pm.get_preset_names()
        pm.save_preset("New", {"channels": "2"})

        presets_dir = os.path.join(self.data_home, "pulsar", "presets")
        survivors = [
            name for name in os.listdir(presets_dir)
            if os.path.isfile(os.path.join(presets_dir, name))
            and "this is not json" in open(os.path.join(presets_dir, name)).read()
        ]
        self.assertTrue(
            survivors,
            f"the corrupt data was destroyed; {presets_dir} now holds "
            f"{sorted(os.listdir(presets_dir))}")

    def test_the_app_is_usable_after_a_corrupt_file(self):
        """Quarantine must leave a working app, not a wedged one."""
        self._corrupt()
        pm = PresetManager()
        pm.get_preset_names()
        pm.save_preset("New", {"channels": "2"})
        # These must not raise, and the new preset must be listed.
        names = pm.get_preset_names()
        self.assertIn("New", names)
        self.assertIsNotNone(pm.get_preset("New"))


class TestHostileShapes(DataDirTestCase):
    def test_a_json_list_does_not_crash_the_preset_api(self):
        self.seed_preset_file(["Stereo", "Mono"])
        pm = PresetManager()
        # Must not raise.
        pm.get_all_presets()
        pm.get_preset_names()

    def test_a_json_list_does_not_crash_the_profile_api(self):
        self.seed_profile_file(["Desktop", "Gaming"])
        pm = ProfileManager()
        pm.load_all_profiles()

    def test_saving_over_a_list_shaped_file_does_not_raise(self):
        self.seed_preset_file(["Stereo"])
        pm = PresetManager()
        pm.save_preset("New", {"channels": "2"})

    def test_saving_over_a_list_shaped_profile_file_does_not_raise(self):
        self.seed_profile_file(["Desktop"])
        pm = ProfileManager()
        pm.save_profile("New", {"schema_version": 2, "devices": [],
                                "routing": []})


if __name__ == "__main__":
    unittest.main()
