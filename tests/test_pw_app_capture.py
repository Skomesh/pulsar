"""
Tests for the Phase 5a app audio capture backend.

These tests don't require an active app audio stream — they exercise the
parsing logic, the gdbus wrappers (mocked), and the node discovery
filter. The portal flow is tested via monkey-patched subprocess calls
since triggering a real picker dialog from a test isn't possible.
"""

import shutil
import unittest
from unittest import mock

from utils.pw_app_capture import (
    SOURCE_TYPE_MONITOR,
    SOURCE_TYPE_WINDOW,
    PortalCapture,
    PortalCaptureError,
    PwRecordError,
    _parse_object_path,
    discover_app_audio_nodes,
    portal_available,
    portal_available_source_types,
    portal_screencast_version,
    pw_record_available,
    start_pw_record,
    stop_pw_record,
    supports_app_capture,
)


def pactl_or_pw_dump_available() -> bool:
    """Skip pw-dump tests if the tool isn't installed."""
    return shutil.which("pw-dump") is not None


def gdbus_available() -> bool:
    return shutil.which("gdbus") is not None


class TestPortalHealthChecks(unittest.TestCase):
    """gdbus subprocess wrapper, with subprocess mocked."""

    def test_portal_available(self):
        # The function is just shutil.which('gdbus')
        self.assertIsInstance(portal_available(), bool)

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_screencast_version_parses_uint32(self, mock_run):
        mock_run.return_value = b"(<uint32 5>,)"
        self.assertEqual(portal_screencast_version(), 5)

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_screencast_version_unreachable_returns_none(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, "gdbus")
        self.assertIsNone(portal_screencast_version())

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_available_source_types_parses(self, mock_run):
        mock_run.return_value = b"(<uint32 7>,)"
        self.assertEqual(portal_available_source_types(), 7)

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_available_source_types_unreachable_returns_none(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("gdbus", 5)
        self.assertIsNone(portal_available_source_types())

    @mock.patch(
        "utils.pw_app_capture.portal_available_source_types",
        return_value=0x7,
    )
    def test_supports_app_capture_true(self, _):
        self.assertTrue(supports_app_capture())

    @mock.patch(
        "utils.pw_app_capture.portal_available_source_types",
        return_value=SOURCE_TYPE_MONITOR | SOURCE_TYPE_WINDOW,  # no app
    )
    def test_supports_app_capture_false_when_app_bit_missing(self, _):
        self.assertFalse(supports_app_capture())

    @mock.patch(
        "utils.pw_app_capture.portal_available_source_types",
        return_value=None,
    )
    def test_supports_app_capture_false_when_unreachable(self, _):
        self.assertFalse(supports_app_capture())


class TestObjectPathParser(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            _parse_object_path("(objectpath '/foo/bar',)"),
            "/foo/bar",
        )

    def test_nested(self):
        self.assertEqual(
            _parse_object_path(
                "(objectpath '/org/freedesktop/portal/desktop/session/1/2',)"
            ),
            "/org/freedesktop/portal/desktop/session/1/2",
        )

    def test_root(self):
        self.assertEqual(_parse_object_path("(objectpath '/',)"), "/")

    def test_garbage(self):
        self.assertIsNone(_parse_object_path("not a dbus reply"))
        self.assertIsNone(_parse_object_path(""))


class TestPortalCapture(unittest.TestCase):
    """PortalCapture drives the gdbus subprocess. Mock subprocess to test."""

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_create_session_returns_handle(self, mock_run):
        mock_run.return_value = (
            b"(objectpath '/org/freedesktop/portal/desktop/session/99/1',)"
        )
        # supports_app_capture is called inside; mock it
        with mock.patch(
            "utils.pw_app_capture.supports_app_capture", return_value=True
        ):
            pc = PortalCapture()
            handle = pc.create_session()
        self.assertEqual(handle, "/org/freedesktop/portal/desktop/session/99/1")
        self.assertEqual(pc._session_handle, handle)

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_create_session_raises_when_app_capture_unsupported(self, mock_run):
        # The error message includes the available source types bit flags,
        # so subprocess.check_output is called for the diagnostic. We just
        # verify that no CreateSession DBus call was made (it would be
        # subprocess.check_output called with the CreateSession method).
        mock_run.return_value = b"(<uint32 3>,)"  # MONITOR | WINDOW, no app
        with mock.patch(
            "utils.pw_app_capture.supports_app_capture", return_value=False
        ):
            pc = PortalCapture()
            with self.assertRaises(PortalCaptureError):
                pc.create_session()
        # No subprocess call should be for the CreateSession method itself
        called_methods = [
            c.args[0]
            for c in mock_run.call_args_list
            if c.args and isinstance(c.args[0], list)
        ]
        create_session_called = any(
            "CreateSession" in (cmd[5] if len(cmd) > 5 else "")
            for cmd in called_methods
        )
        self.assertFalse(
            create_session_called,
            "CreateSession should not be called when app capture is unsupported",
        )

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_select_sources_succeeds_on_zero_response(self, mock_run):
        mock_run.return_value = b"(uint32 0,)"
        pc = PortalCapture()
        pc._session_handle = "/session/test"
        pc.select_sources()  # should not raise

    @mock.patch("utils.pw_app_capture.subprocess.check_output")
    def test_select_sources_raises_on_nonzero(self, mock_run):
        mock_run.return_value = b"(uint32 1,)"
        pc = PortalCapture()
        pc._session_handle = "/session/test"
        with self.assertRaises(PortalCaptureError):
            pc.select_sources()

    def test_select_sources_requires_session(self):
        pc = PortalCapture()
        with self.assertRaises(PortalCaptureError):
            pc.select_sources()

    def test_start_requires_session(self):
        pc = PortalCapture()
        with self.assertRaises(PortalCaptureError):
            pc.start()


@unittest.skipUnless(
    pactl_or_pw_dump_available(), "pw-dump not available"
)
class TestDiscoverAppAudioNodes(unittest.TestCase):
    """Filter logic on a synthetic pw-dump JSON."""

    def _fake_pw_dump(self, nodes):
        """Return a list shaped like pw-dump output: a list of
        PipeWire:Interface:Node objects plus a couple of unrelated
        objects (clients, factories) to make sure the filter ignores them.
        """
        return [
            # unrelated
            {"type": "PipeWire:Interface:Client", "id": 1, "info": {"props": {}}},
            {"type": "PipeWire:Interface:Factory", "id": 2, "info": {"props": {}}},
            # real nodes
            *nodes,
        ]

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_returns_empty_when_no_pw_dump(self, mock_run):
        mock_run.return_value = None
        self.assertEqual(discover_app_audio_nodes(), [])

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_filters_hardware_sinks(self, mock_run):
        mock_run.return_value = self._fake_pw_dump([
            {
                "type": "PipeWire:Interface:Node",
                "id": 100,
                "info": {
                    "props": {
                        "media.class": "Audio/Sink",
                        "node.name": "alsa_output.foo",
                        "application.name": "PulseAudio",
                    }
                },
            },
        ])
        result = discover_app_audio_nodes()
        self.assertEqual(result, [])

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_finds_app_output_stream(self, mock_run):
        mock_run.return_value = self._fake_pw_dump([
            {
                "type": "PipeWire:Interface:Node",
                "id": 200,
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "node.name": "librewolf_output",
                        "application.name": "LibreWolf",
                        "application.process.binary": "librewolf",
                        "application.process.id": "12345",
                    }
                },
            },
        ])
        result = discover_app_audio_nodes()
        self.assertEqual(len(result), 1)
        n = result[0]
        self.assertEqual(n["id"], 200)
        self.assertEqual(n["node_name"], "librewolf_output")
        self.assertEqual(n["application_name"], "LibreWolf")
        self.assertEqual(n["binary"], "librewolf")
        self.assertEqual(n["pid"], 12345)
        self.assertEqual(n["media_class"], "Stream/Output/Audio")

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_finds_app_input_stream(self, mock_run):
        mock_run.return_value = self._fake_pw_dump([
            {
                "type": "PipeWire:Interface:Node",
                "id": 300,
                "info": {
                    "props": {
                        "media.class": "Stream/Input/Audio",
                        "node.name": "discord_input",
                        "application.name": "Discord",
                        "application.process.binary": "Discord",
                        "application.process.id": "99",
                    }
                },
            },
        ])
        result = discover_app_audio_nodes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["media_class"], "Stream/Input/Audio")

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_skips_streams_without_app_metadata(self, mock_run):
        """Some streams (e.g. easyeffects internal) lack application.name
        or application.process.binary. We can't show them meaningfully
        in the UI, so skip them.
        """
        mock_run.return_value = self._fake_pw_dump([
            {
                "type": "PipeWire:Interface:Node",
                "id": 400,
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "node.name": "orphan_stream",
                    }
                },
            },
        ])
        self.assertEqual(discover_app_audio_nodes(), [])

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_results_sorted(self, mock_run):
        mock_run.return_value = self._fake_pw_dump([
            {
                "type": "PipeWire:Interface:Node",
                "id": 1,
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "zulu",
                    }
                },
            },
            {
                "type": "PipeWire:Interface:Node",
                "id": 2,
                "info": {
                    "props": {
                        "media.class": "Stream/Input/Audio",
                        "application.name": "alpha",
                    }
                },
            },
            {
                "type": "PipeWire:Interface:Node",
                "id": 3,
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "alpha",
                    }
                },
            },
        ])
        result = discover_app_audio_nodes()
        # input streams first (alpha Stream/Input), then output streams
        # sorted by app name (alpha, alpha, zulu)
        self.assertEqual([n["application_name"] for n in result],
                         ["alpha", "alpha", "zulu"])
        # And each entry has a sink_name field (may be None if no Link)
        for n in result:
            self.assertIn("sink_name", n)

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_includes_sink_name_from_links(self, mock_run):
        """sink_name is populated from pw-dump Links — the link's
        input-node-id is the sink (where audio goes), output-node-id
        is the source (e.g. the app's Stream/Output/Audio node)."""
        mock_run.return_value = [
            # Sink node
            {"type": "PipeWire:Interface:Node", "id": 79,
             "info": {"props": {"node.name": "easyeffects_sink",
                                "media.class": "Audio/Sink"}}},
            # App stream node
            {"type": "PipeWire:Interface:Node", "id": 113,
             "info": {"props": {"node.name": "LibreWolf",
                                "media.class": "Stream/Output/Audio",
                                "application.name": "LibreWolf",
                                "application.process.binary": "librewolf",
                                "application.process.id": "1234"}}},
            # Link: app -> sink
            {"type": "PipeWire:Interface:Link", "id": 241,
             "info": {"output-node-id": 113, "input-node-id": 79,
                      "state": "active"}},
        ]
        result = discover_app_audio_nodes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["application_name"], "LibreWolf")
        self.assertEqual(result[0]["sink_name"], "easyeffects_sink")

    @mock.patch("utils.pw_app_capture._run_pw_dump")
    def test_sink_name_none_when_no_link(self, mock_run):
        """If the app isn't connected to any sink (e.g. paused), sink_name is None."""
        mock_run.return_value = [
            {"type": "PipeWire:Interface:Node", "id": 113,
             "info": {"props": {"node.name": "App",
                                "media.class": "Stream/Output/Audio",
                                "application.name": "App",
                                "application.process.binary": "app",
                                "application.process.id": "1"}}},
        ]
        result = discover_app_audio_nodes()
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["sink_name"])


class TestPwRecord(unittest.TestCase):
    """Tests for the pw-record wrapper functions."""

    def test_pw_record_available(self):
        # Just a wrapper around shutil.which, but verify the function
        # exists and returns a sensible type.
        result = pw_record_available()
        self.assertIsInstance(result, bool)

    @mock.patch("utils.pw_app_capture.shutil.which", return_value=None)
    def test_start_pw_record_raises_when_not_installed(self, _):
        with self.assertRaises(PwRecordError) as ctx:
            start_pw_record(123, "/tmp/output.wav")
        self.assertIn("pw-record not found", str(ctx.exception))

    @mock.patch("utils.pw_app_capture.shutil.which", return_value="/usr/bin/pw-record")
    @mock.patch("utils.pw_app_capture.subprocess.Popen")
    def test_start_pw_record_builds_correct_command(self, mock_popen, _):
        mock_popen.return_value = mock.MagicMock()
        start_pw_record(456, "/tmp/x.wav", sample_rate=44100, channels=1)
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/pw-record")
        self.assertIn("--target", cmd)
        self.assertIn("456", cmd)
        self.assertIn("--rate", cmd)
        self.assertIn("44100", cmd)
        self.assertIn("--channels", cmd)
        self.assertIn("1", cmd)
        self.assertEqual(cmd[-1], "/tmp/x.wav")

    def test_stop_pw_record_returns_exit_info(self):
        fake_proc = mock.MagicMock()
        fake_proc.poll.return_value = 0  # Already exited
        fake_proc.returncode = 0
        fake_proc.communicate.return_value = (b"", b"")
        rc, out, err = stop_pw_record(fake_proc)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
