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
    def test_a_corrupt_file_is_not_overwritten_by_the_next_save(self):
        path = self.seed_preset_file({"Existing": {"channels": "2"}})
        with open(path, "w") as fh:
            fh.write("{ this is not json")

        pm = PresetManager()
        pm.get_preset_names()  # must not raise

        # Loading must not have quietly emptied anything.
        with open(path) as fh:
            still_corrupt = fh.read()
        self.assertIn("this is not json", still_corrupt,
                      "reading a corrupt file rewrote it")

        # And saving must not report success while destroying the only copy.
        ok = pm.save_preset("New", {"channels": "2"})
        with open(path) as fh:
            after = fh.read()
        if ok:
            self.assertNotEqual(after, still_corrupt)
        else:
            self.assertEqual(after, still_corrupt,
                             "a refused save must not modify the file")


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
