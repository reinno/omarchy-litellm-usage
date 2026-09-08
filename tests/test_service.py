"""Run the QML service with fake commands and isolated XDG directories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("quickshell"), "Quickshell is not installed")
class ServiceSmokeTest(unittest.TestCase):
    def test_service_starts_collector_and_requests_native_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy(ROOT / "Service.qml", root / "Service.qml")
            (root / "collector.py").write_text('print("synthetic refresh complete")\n')
            command = root / "omarchy-shell"
            command.write_text('#!/bin/sh\n[ "$1 $2" = "omarchy.agents refresh" ] || exit 1\necho discovered > "$XDG_STATE_HOME/discovery"\n')
            command.chmod(0o755)
            config = root / "config/omarchy/agents"
            config.mkdir(parents=True)
            (config / "litellm.json").write_text(json.dumps({"refreshIntervalSec": 120}))
            for name in ("state", "runtime", "cache"):
                (root / name).mkdir(mode=0o700)
            (root / "shell.qml").write_text('''import QtQuick
import Quickshell
ShellRoot {
    Service { id: service }
    Timer {
        interval: 2000
        running: true
        onTriggered: {
            console.log("SMOKE", service.lastStatus, service.refreshIntervalSec, service.discoveryRequested)
            Qt.quit()
        }
    }
}
''')
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(root / "config"),
                       XDG_STATE_HOME=str(root / "state"), XDG_RUNTIME_DIR=str(root / "runtime"),
                       XDG_CACHE_HOME=str(root / "cache"), PATH=str(root) + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["quickshell", "--no-color", "-p", str(root / "shell.qml")],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("SMOKE synthetic refresh complete 120 true", result.stdout + result.stderr)
            self.assertEqual((root / "state/discovery").read_text().strip(), "discovered")


if __name__ == "__main__":
    unittest.main()
