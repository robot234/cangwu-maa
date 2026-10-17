import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import launcher
from cangwu.device import DeviceError


class LauncherStartup(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        (self.root / "app").mkdir()
        (self.root / "app/mxu.exe").touch()
        (self.root / "config.example.json").write_text(
            json.dumps({"device": {"adb_server_port": 15037}}), encoding="utf-8"
        )
        self.enterContext(patch.object(launcher, "ROOT", self.root))
        self.enterContext(patch.object(sys, "argv", ["launcher.py"]))
        self.running = self.enterContext(patch.object(launcher, "ui_running", return_value=False))
        self.admin = self.enterContext(
            patch.object(launcher.ctypes.windll.shell32, "IsUserAnAdmin", return_value=True)
        )
        self.elevate = self.enterContext(
            patch.object(launcher.ctypes.windll.shell32, "ShellExecuteW", return_value=42)
        )
        self.prepare = self.enterContext(
            patch.object(launcher, "prepare_adb", side_effect=DeviceError("ADB unavailable"))
        )
        self.popen = self.enterContext(patch.object(launcher.subprocess, "Popen"))
        self.popen.return_value.pid = 4242
        self.check = self.enterContext(patch.object(launcher.subprocess, "call", return_value=0))

    def test_unavailable_adb_does_not_block_window_and_port_is_preserved(self):
        with patch.dict(launcher.os.environ, {"MAAFW_BINARY_PATH": "stale-location"}):
            self.assertEqual(launcher.main(), 0)

        self.prepare.assert_not_called()
        self.check.assert_not_called()
        self.popen.assert_called_once()
        args, kwargs = self.popen.call_args
        self.assertEqual(args, ([str(self.root / "app/mxu.exe")],))
        self.assertEqual(kwargs["cwd"], self.root / "app")
        self.assertEqual(kwargs["env"]["ANDROID_ADB_SERVER_PORT"], "15037")
        self.assertEqual(kwargs["env"]["ADB_SERVER_SOCKET"], "tcp:15037")
        self.assertNotIn("MAAFW_BINARY_PATH", kwargs["env"])
        self.assertEqual((self.root / "runtime/ui.pid").read_text(), "4242")

    def test_existing_window_is_reused_without_reconnecting(self):
        self.running.return_value = True
        self.assertEqual(launcher.main(), 0)
        self.prepare.assert_not_called()
        self.popen.assert_not_called()
        self.elevate.assert_not_called()

    def test_elevation_starts_host_without_an_adb_precheck(self):
        self.admin.return_value = False
        self.assertEqual(launcher.main(), 0)
        self.prepare.assert_not_called()
        self.popen.assert_not_called()
        self.elevate.assert_called_once_with(
            None,
            "runas",
            str(self.root / ".venv/Scripts/pythonw.exe"),
            launcher.subprocess.list2cmdline([str(self.root / "launcher.py"), "--host"]),
            str(self.root),
            0,
        )

    def test_explicit_diagnostic_reports_adb_failure_without_opening_ui(self):
        with patch.object(sys, "argv", ["launcher.py", "--check"]):
            with self.assertRaisesRegex(DeviceError, "ADB unavailable"):
                launcher.main()
        self.prepare.assert_called_once()
        self.popen.assert_not_called()
        self.check.assert_not_called()

    def test_successful_diagnostic_runs_maa_check_on_dedicated_port(self):
        self.prepare.side_effect = None
        self.prepare.return_value = "adb.exe"
        self.check.return_value = 7
        with patch.object(sys, "argv", ["launcher.py", "--check"]):
            self.assertEqual(launcher.main(), 7)
        self.prepare.assert_called_once()
        self.popen.assert_not_called()
        self.check.assert_called_once()
        args, kwargs = self.check.call_args
        self.assertEqual(args[0][-2:], ["--tasks", "check"])
        self.assertEqual(kwargs["env"]["ADB_SERVER_SOCKET"], "tcp:15037")


if __name__ == "__main__":
    unittest.main()
