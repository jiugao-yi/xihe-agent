"""Unit probes for core.services.diagnostics (no network, no registry load needed)."""
from core.services.diagnostics import (capability_matrix, check_connectivity,
                              platform_missing_fields)


def test_platform_missing_fields():
    assert platform_missing_fields("wecom", {"bot_id": "x"}) == ["secret"]
    assert platform_missing_fields("wecom", {}) == ["bot_id", "secret"]
    assert platform_missing_fields("feishu", {"app_id": "a", "app_secret": "s"}) == []
    assert platform_missing_fields("unknown-platform", {}) == []
    # placeholder-looking values count as present (truthy) — preflight only
    # reports genuinely absent fields
    assert platform_missing_fields("wecom", {"bot_id": " ", "secret": None}) == ["bot_id", "secret"]


def test_check_connectivity_refused():
    r = check_connectivity("http://127.0.0.1:1/v1", "k", timeout=2)
    assert r["ok"] is False and r["error"]


def test_check_connectivity_probes_chat_completions():
    """The probe is a minimal chat completion, not GET /models — the verdict
    must reflect the capability a conversation actually needs."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["payload"] = body.decode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices": [{"message": {"content": "pong"}}]}')

        def do_GET(self):
            # A /models-less gateway: any GET 404s. Must be irrelevant.
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = check_connectivity(f"http://127.0.0.1:{srv.server_address[1]}/v1",
                               "key-1", model="glm-test")
        assert r["ok"] is True and r["error"] is None
        assert seen["path"] == "/v1/chat/completions"
        assert seen["auth"] == "Bearer key-1"
        assert "glm-test" in seen["payload"]
        assert "ping" in seen["payload"]
    finally:
        srv.shutdown()


def test_check_connectivity_bad_key_reports_401():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "invalid key"}}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = check_connectivity(f"http://127.0.0.1:{srv.server_address[1]}/v1",
                               "bad-key", model="m")
        assert r["ok"] is False
        assert "api_key" in r["error"]
    finally:
        srv.shutdown()


def test_capability_matrix_off_reasons_name_the_fix():
    caps = capability_matrix({}, toolsets=None, names=set())
    assert caps["vision"]["ready"] is False
    assert "vision_model" in caps["vision"]["reason"]
    assert caps["web_search"]["ready"] is False
    assert "搜索 key" in caps["web_search"]["reason"]


def test_capability_matrix_all_on():
    names = {"browser_navigate", "vision_analyze", "image_ocr",
             "web_search", "run_sandbox_code"}
    caps = capability_matrix({}, toolsets=None, names=names)
    assert all(c["ready"] for c in caps.values())
    assert all(c["reason"] is None for c in caps.values())


def test_capability_matrix_roster_reason():
    caps = capability_matrix({}, toolsets=["files"], names=set())
    assert "toolsets 未包含 web" in caps["browser"]["reason"]
