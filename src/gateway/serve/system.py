"""SystemMixin — the system-probe business (health / readiness /
test-connection) and its routes.

One business module like chat/admin/browser: a mixin on ServeApp, sharing its
state (capabilities cache lives on the shell — chat's stream hello uses it
too). The ServeApp class itself and the process assembly live in server.py,
the composition root.
"""

import asyncio
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


class SystemMixin:
    async def health(self, request):
        return web.json_response({
            "ok": True,
            "version": self.version,
            "mode": "serve",
            "model": self.config.get("model"),
            "capabilities": self.capabilities(),
        })

    async def readiness(self, request):
        """Structured readiness for onboarding UIs — what's missing and the
        fix, instead of the client waiting for a first send to fail.

        /health stays a lean liveness probe ({ok, version}); this endpoint
        costs a registry scan and answers "why not".
        """
        from core.services.diagnostics import readiness_report
        mode = request.query.get("mode", "chat")
        if mode not in ("chat", "gateway"):
            mode = "chat"
        report = readiness_report(self.config, mode=mode)
        report["version"] = self.version
        return web.json_response(report)

    async def test_connection(self, request):
        """Server-side model-connection test (api_key never crosses the API).

        Optional JSON body — {base_url?, api_key?, model?} — overrides the
        boot-time config so the desktop's「测试连接」can probe *unsaved* form
        edits ("test what I just typed"); absent fields fall back to the
        SAVED config.yaml values. The probe never mutates self.config.
        """
        from core.services.diagnostics import check_connectivity
        body = {}
        try:
            if request.can_read_body:
                body = await request.json() or {}
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        def _pick(v: str | None, fallback):
            v = str(v or "").strip()
            return v or fallback

        base_url = _pick(body.get("base_url"), self.config.get("base_url"))
        api_key = _pick(body.get("api_key"), self.config.get("api_key"))
        result = await asyncio.to_thread(check_connectivity, base_url, api_key)
        result["model_configured"] = _pick(body.get("model"), self.config.get("model"))
        return web.json_response(result)


def add_routes(router, app):
    """The system-probe routes. ``app`` is the ServeApp instance."""
    router.add_get("/health", app.health)
    router.add_get("/readiness", app.readiness)
    router.add_post("/test-connection", app.test_connection)
