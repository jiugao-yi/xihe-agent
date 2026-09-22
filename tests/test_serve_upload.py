"""serve 附件上传：POST /convs/{id}/upload 落盘契约 + 附件元数据持久化。

三模式硬规则的 serve 侧落点：桌面选文件 → upload 落 AGENT_HOME/uploads/
<conv_id>/ → send 帧携带 attachments → 元数据（含图片视觉描述）随用户行
落 attachments 列，content 保持用户原文，模型面向的提示块由 agent 在
API 边界注入。L1：aiohttp TestClient，不碰真实模型。
"""
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.session import SessionSource
from gateway.serve import ServeApp


class FakeDb:
    def build_key(self, source):
        return f"agent:main:serve:dm:{source.chat_id}"

    def pending_auto_reset(self, source):
        return None


class FakeCtx:
    def __init__(self, config):
        self.config = config
        self.db = FakeDb()
        self.main_toolsets = []
        self.main_skills = []

    def create_agent(self, **kwargs):
        raise Exception("not exercised by these tests")


def _client(tmp_path, monkeypatch):
    # Redirect uploads off the real AGENT_HOME — ChatMixin._upload_dir imports
    # AGENT_HOME from core.config, so patch that import target's attribute.
    import core.config as cfg
    monkeypatch.setattr(cfg, "AGENT_HOME", Path(tmp_path))
    from gateway.serve import conversations as conv_mod
    app_obj = ServeApp(FakeCtx({"api_key": "sk-x", "model": "m"}), version="test")
    app = web.Application()
    conv_mod.add_routes(app.router, app_obj)
    return TestClient(TestServer(app))


def test_upload_roundtrip(tmp_path, monkeypatch):
    async def drive():
        import aiohttp
        c = _client(tmp_path, monkeypatch)
        await c.start_server()
        try:
            form = aiohttp.FormData()
            form.add_field("file", b"helloworld", filename="note.txt",
                           content_type="text/plain")
            r = await c.post("/convs/t1/upload", data=form)
            assert r.status == 200
            body = await r.json()
            assert body["name"] == "note.txt" and body["size"] == 10
            p = Path(body["path"])
            assert p.is_file() and p.parent.name == "t1"
            assert p.read_bytes() == b"helloworld"
            assert str(tmp_path) in str(p)
        finally:
            await c.close()

    asyncio.run(drive())


def test_upload_strips_path_traversal(tmp_path, monkeypatch):
    async def drive():
        import aiohttp
        c = _client(tmp_path, monkeypatch)
        await c.start_server()
        try:
            form = aiohttp.FormData()
            form.add_field("file", b"x", filename="../../evil.txt")
            r = await c.post("/convs/t2/upload", data=form)
            assert r.status == 200
            body = await r.json()
            assert ".." not in body["path"]
            assert body["name"] == "evil.txt"
        finally:
            await c.close()

    asyncio.run(drive())


def test_upload_rejects_missing_field(tmp_path, monkeypatch):
    async def drive():
        import aiohttp
        c = _client(tmp_path, monkeypatch)
        await c.start_server()
        try:
            form = aiohttp.FormData()
            form.add_field("other", b"x", filename="x.txt")
            r = await c.post("/convs/t3/upload", data=form)
            assert r.status == 400
        finally:
            await c.close()

    asyncio.run(drive())


def test_describe_attachments_metadata(tmp_path, monkeypatch):
    """附件元数据构建：文件条目带 name/path/size；文本不在此拼接（content
    保持用户原文，提示块由 agent 在 API 边界注入）。图片分支走 vision。"""
    app_obj = ServeApp(FakeCtx({"api_key": "sk-x", "model": "m"}), version="test")
    atts = app_obj._describe_attachments([
        {"path": r"C:\data\uploads\t1\doc.pdf", "name": "doc.pdf", "size": 2_500_000},
    ])
    assert atts == [{"name": "doc.pdf", "path": r"C:\data\uploads\t1\doc.pdf",
                     "size": 2_500_000}]
    assert app_obj._describe_attachments([]) is None


def test_attachments_roundtrip_and_reshape(tmp_path):
    """行为契约（用户气泡不变形的回归）：带附件的用户消息落库 →
    load_transcript 还原 attachments、content 仍是用户原文；
    _reshape_history 输出的用户气泡带 attachments、content 为原文；
    _prepare_api_messages 注入提示块且不污染持久化行。"""
    from core.session import SessionDB
    from core.agent.agent import XiheAgent
    from gateway.serve.conversations import _reshape_history

    db = SessionDB({})
    src = SessionSource(platform="serve", chat_id="t-rt", chat_type="dm")
    sid = db.get_or_create_session(src)
    atts = [{"name": "a.csv", "path": "/tmp/a.csv", "size": 100}]
    db.rewrite_messages(sid, [
        {"role": "user", "content": "帮我分析", "_attachments": atts},
    ])

    # model-facing reload keeps the internal key + literal content
    loaded = db.load_messages(sid)
    assert loaded[0]["content"] == "帮我分析"
    assert loaded[0]["_attachments"] == atts

    # display transcript + reshape expose attachments, content stays clean
    rows = db.load_transcript(db.build_key(src))
    assert rows[0]["attachments"] == atts
    assert rows[0]["content"] == "帮我分析"
    out = _reshape_history(rows)
    assert out[0]["role"] == "user"
    assert out[0]["content"] == "帮我分析"
    assert out[0]["attachments"] == atts

    # API boundary injects the hint without mutating the persisted dict
    agent = XiheAgent.__new__(XiheAgent)  # only the fields read below are needed
    agent._turn_memory_inject = None
    agent._turn_layer = None
    agent.config = {}
    api_msgs = XiheAgent._prepare_api_messages(agent, loaded)
    assert api_msgs[0]["content"].startswith("[用户发送了文件 a.csv")
    assert "服务器路径: /tmp/a.csv" in api_msgs[0]["content"]
    assert api_msgs[0]["content"].endswith("帮我分析")
    assert "_attachments" not in api_msgs[0]
    # source untouched
    assert loaded[0]["content"] == "帮我分析"
