"""The native app supervisor: startup proof, readiness record and kill-on-close (Windows only)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects and the Desktop supervisor are Windows-only")

SUPERVISOR = Path(__file__).resolve().parents[1] / "desktop_app_supervisor.py"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(path, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.2)
    raise AssertionError(f"{path.name} was not written")


def start(folder, launch):
    (folder / "launch.json").write_text(json.dumps(launch), encoding="utf-8")
    return subprocess.Popen([sys.executable, str(SUPERVISOR), str(folder)])


def test_the_app_starts_is_proven_and_dies_with_its_supervisor(tmp_path):
    # A stand-in app: Python's own HTTP server on loopback, no download involved.
    port = free_port()
    supervisor = start(tmp_path, {"name": "Stand-in", "executablePath": sys.executable, "port": port, "health": "/",
                                  "args": ["-m", "http.server", str(port), "--bind", "127.0.0.1"], "env": {},
                                  "data": str(tmp_path / "data")})
    try:
        ready = wait_for(tmp_path / "ready.json")
        assert ready["Port"] == port and ready["ProcessId"] > 0
        app = ready["ProcessId"]
        assert subprocess.run(["tasklist", "/FI", f"PID eq {app}", "/NH"], capture_output=True, text=True).stdout.count(str(app))
    finally:
        supervisor.kill()
        supervisor.wait(10)
    time.sleep(1)
    listing = subprocess.run(["tasklist", "/FI", f"PID eq {app}", "/NH"], capture_output=True, text=True).stdout
    assert str(app) not in listing, "the app outlived its supervisor"


def test_an_app_that_exits_at_startup_is_reported(tmp_path):
    supervisor = start(tmp_path, {"name": "Broken", "executablePath": sys.executable, "port": free_port(), "health": "/",
                                  "args": ["-c", "raise SystemExit(3)"], "env": {}, "data": str(tmp_path / "data")})
    supervisor.wait(60)
    ready = wait_for(tmp_path / "ready.json")
    assert "exit code 3" in ready["Error"] and supervisor.returncode == 1
