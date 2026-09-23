"""serve assembly smoke: the real run_serve must boot with all business
modules wired — a module rename breaking add_routes dies at first boot, and
this test catches it (it had no coverage before)."""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def serve_proc():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main", "serve", "--port", str(port)],
        cwd=SRC.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={"PYTHONIOENCODING": "utf-8", **__import__("os").environ},
    )
    base = f"http://127.0.0.1:{port}"
    # wait for /health
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        pytest.fail(f"serve did not come up:\n{out[-2000:]}")
    yield base
    proc.kill()


def test_serve_boots_with_all_business_routes(serve_proc):
    base = serve_proc
    # One representative GET per business module: a rename that breaks an
    # import inside run_serve/add_routes makes these 404 (route never
    # registered) or the boot itself fails.
    checks = {
        "/health": 200,                          # system
        "/convs/x/messages": 200,                # conversations (empty transcript ok)
        "/sessions": 200,                        # conversations/admin
        "/browser/status": 200,                  # browser
    }
    for path, want in checks.items():
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                assert r.status == want, (path, r.status)
        except urllib.error.HTTPError as e:
            pytest.fail(f"{path} -> {e.code} (route missing or handler broken)")
