"""Regression tests for install.sh / uninstall.sh data handling.

A previous change moved user presets from a relative ``presets/`` path to
``$HOME/.local/share/pulsar/presets`` -- which is INSIDE the directory
``install.sh`` copies into and ``uninstall.sh`` deletes with ``rm -rf``. That
turned an ordinary upgrade into silent destruction of the user's saved audio
routing, and uninstalling into permanent data loss. Both were reproduced by
hand before being fixed; these tests keep them fixed.

The shell scripts are exercised for real against a throwaway ``HOME`` under
the workspace. Nothing here needs pactl, a display, or an audio server.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

# A profile worth destroying: the whole point is that it is irreplaceable.
PRECIOUS = {
    "MY-PRECIOUS-SETUP": {
        "schema_version": 2,
        "created": "2026-01-01T00:00:00",
        "description": "three sinks, two loopbacks, hours of setup",
        "devices": [
            {"name": "game_sink", "type": "sink", "channels": 2},
            {"name": "voice_sink", "type": "sink", "channels": 2},
        ],
        "routing": [
            {"from": "game_sink.monitor", "to": "alsa_output.usb-headset"},
        ],
    }
}


def _fake_home(base: Path, name: str = "home") -> Path:
    """Create an isolated HOME so the scripts never touch a real one."""
    home = base / name
    home.mkdir(parents=True, exist_ok=True)
    return home


def _seed_profiles(home: Path, data=None) -> Path:
    """Write a user_presets.json where the installed app would keep it."""
    presets = home / ".local/share/pulsar/presets"
    presets.mkdir(parents=True, exist_ok=True)
    target = presets / "user_presets.json"
    target.write_text(json.dumps(data if data is not None else PRECIOUS))
    return target


def _read_profiles(home: Path):
    target = home / ".local/share/pulsar/presets/user_presets.json"
    if not target.is_file():
        return None
    return json.loads(target.read_text())


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _run(script: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    # Strip XDG override so the script's own defaults are exercised.
    env.pop("XDG_DATA_HOME", None)
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # install.sh colourises its output with raw ANSI escapes. Strip them so
    # assertions on printed paths don't compare against escape sequences.
    proc.stdout = _ANSI_RE.sub("", proc.stdout)
    proc.stderr = _ANSI_RE.sub("", proc.stderr)
    return proc


class TestReinstallPreservesProfiles(unittest.TestCase):
    """install.sh must not overwrite the user's saved profiles."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_install_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_reinstall_keeps_user_profiles(self):
        home = _fake_home(self.tmp, "reinstall")
        _seed_profiles(home)

        first = _run(INSTALL_SH, home, REPO_ROOT)
        self.assertEqual(first.returncode, 0, first.stderr)

        after_first = _read_profiles(home)
        self.assertIsNotNone(after_first, "install.sh removed the presets dir")
        self.assertIn("MY-PRECIOUS-SETUP", after_first)

        # Second run is the upgrade case: this is what used to clobber.
        second = _run(INSTALL_SH, home, REPO_ROOT)
        self.assertEqual(second.returncode, 0, second.stderr)

        after_second = _read_profiles(home)
        self.assertIsNotNone(after_second, "upgrade removed user_presets.json")
        self.assertIn(
            "MY-PRECIOUS-SETUP",
            after_second,
            "upgrade overwrote the user's saved profiles",
        )
        self.assertEqual(
            after_second["MY-PRECIOUS-SETUP"]["devices"],
            PRECIOUS["MY-PRECIOUS-SETUP"]["devices"],
            "profile contents were altered",
        )

    def test_reinstall_preserves_profiles_when_repo_ships_different_ones(self):
        """The repo's own presets/ must not win over the user's file.

        This is the exact shape of the original bug: install.sh copied the
        repository's presets/user_presets.json straight over the installed one.
        """
        home = _fake_home(self.tmp, "repo-clobber")
        _seed_profiles(home)

        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)

        repo_copy = REPO_ROOT / "presets/user_presets.json"
        # Simulate the clobber by writing the repo's version over the user's.
        if repo_copy.is_file():
            shutil.copy(repo_copy, home / ".local/share/pulsar/presets/user_presets.json")

        # Re-running install.sh must NOT reinstate that clobbered content over
        # whatever the user has now; it must preserve what's on disk.
        _seed_profiles(home)  # user re-saved their work
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        self.assertIn("MY-PRECIOUS-SETUP", _read_profiles(home))


class TestUninstallPreservesProfiles(unittest.TestCase):
    """uninstall.sh must not `rm -rf` the user's saved profiles."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_uninstall_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_uninstall_keeps_user_profiles(self):
        home = _fake_home(self.tmp, "uninstall")
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        _seed_profiles(home)

        app_dir = home / ".local/share/pulsar"
        self.assertTrue(app_dir.is_dir(), "app dir missing before uninstall")

        result = _run(app_dir / "uninstall.sh", home, app_dir)
        self.assertEqual(result.returncode, 0, result.stderr)

        surviving = _read_profiles(home)
        self.assertIsNotNone(surviving, "uninstall destroyed the user's profiles")
        self.assertIn("MY-PRECIOUS-SETUP", surviving)

        # The app's own files are gone. Note the presets dir is deliberately
        # re-created to hold the restored user data, so assert on the app code
        # rather than on APP_DIR being wholly absent.
        self.assertFalse(
            (app_dir / "src/main.py").is_file(),
            "uninstall should remove the application source",
        )
        self.assertFalse(
            (app_dir / "uninstall.sh").is_file(),
            "uninstall should remove its own script",
        )
        self.assertFalse(
            (home / ".local/bin/pulsar").is_symlink(),
            "uninstall should remove the launcher symlink",
        )

    def test_uninstall_then_reinstall_roundtrip(self):
        """Profiles must survive a full remove/reinstall cycle."""
        home = _fake_home(self.tmp, "roundtrip")
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        _seed_profiles(home)

        app_dir = home / ".local/share/pulsar"
        self.assertEqual(_run(app_dir / "uninstall.sh", home, app_dir).returncode, 0)
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)

        restored = _read_profiles(home)
        self.assertIsNotNone(restored, "profiles lost across uninstall+reinstall")
        self.assertIn("MY-PRECIOUS-SETUP", restored)


class TestInstallScriptCwdIndependence(unittest.TestCase):
    """install.sh must install Pulsar, not the caller's working directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_cwd_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_run_from_unrelated_directory_installs_pulsar(self):
        home = _fake_home(self.tmp, "cwd")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "my-tax-return.pdf").write_text("not pulsar")

        result = _run(INSTALL_SH, home, elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)

        app_dir = home / ".local/share/pulsar"
        self.assertTrue(
            (app_dir / "src/main.py").is_file(),
            "install.sh did not copy the Pulsar source tree",
        )
        self.assertFalse(
            (app_dir / "my-tax-return.pdf").exists(),
            "install.sh copied the caller's cwd instead of its own directory",
        )


class TestDesktopFile(unittest.TestCase):
    """The generated .desktop file must be launchable, spaces included."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_desktop_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _exec_line(self, home: Path) -> str:
        desktop = home / ".local/share/applications/pulsar.desktop"
        self.assertTrue(desktop.is_file(), "no .desktop file generated")
        for line in desktop.read_text().splitlines():
            if line.startswith("Exec="):
                return line[len("Exec="):]
        self.fail("no Exec= line in generated .desktop")

    def test_exec_is_absolute_and_resolvable(self):
        home = _fake_home(self.tmp, "plain")
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        exec_path = self._exec_line(home)
        self.assertTrue(exec_path.startswith("/"), f"Exec not absolute: {exec_path}")
        self.assertTrue(Path(exec_path).is_file(), f"Exec points nowhere: {exec_path}")

    def test_exec_quoted_when_home_contains_a_space(self):
        home = _fake_home(self.tmp, "home with space")
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        exec_path = self._exec_line(home)

        if " " in home.name:
            self.assertTrue(
                exec_path.startswith('"') and exec_path.endswith('"'),
                f"Exec with a space in the path must be quoted per the "
                f"desktop-entry spec, got: {exec_path}",
            )
        unquoted = exec_path.strip('"')
        self.assertTrue(
            Path(unquoted).is_file(),
            f"Exec does not resolve even after stripping quotes: {unquoted}",
        )


class TestUninstallerIsShipped(unittest.TestCase):
    """install.sh used to delete uninstall.sh, then told users to run it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulsar_uninstship_", dir="/workspace"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_uninstall_sh_exists_after_install(self):
        home = _fake_home(self.tmp, "shipped")
        self.assertEqual(_run(INSTALL_SH, home, REPO_ROOT).returncode, 0)
        self.assertTrue(
            (home / ".local/share/pulsar/uninstall.sh").is_file(),
            "install.sh deleted the uninstaller it points users at",
        )

    def test_completion_message_points_at_an_existing_path(self):
        home = _fake_home(self.tmp, "msgpath")
        result = _run(INSTALL_SH, home, REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            if "To uninstall" in line:
                path = line.split("run:", 1)[1].strip()
                self.assertTrue(
                    Path(path).is_file(),
                    f"install.sh advertises an uninstall path that does not "
                    f"exist after install: {path}",
                )
                break
        else:
            self.skipTest("no 'To uninstall' line emitted")


if __name__ == "__main__":
    unittest.main()