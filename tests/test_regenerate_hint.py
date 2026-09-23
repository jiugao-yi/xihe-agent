"""regenerate hint (XiheAgent.chat(regenerate=True)): turn-scoped system
injection, never persisted, cleared after the turn."""
import json

import pytest

from core.agent.turn_layers import REGENERATE_HINT
from core.session import SessionSource

from tests.fakes import FakeChatClient


def test_regenerate_hint_injected_and_cleared(make_agent):
    client = FakeChatClient(script=[{"content": "fresh answer"}])
    agent = make_agent(client, enabled_toolsets=["files"], is_subagent=False)
    src = SessionSource(platform="cli", chat_id="t-regen")

    result = agent.chat(src, "same question", turn_layer=REGENERATE_HINT)

    sent = client.calls[0]
    system = (sent.get("messages") or [{}])[0].get("content") or ""
    assert REGENERATE_HINT in system
    assert "fresh answer" in result
    # Turn-scoped: cleared after the turn.
    assert agent._turn_layer is None

    # A follow-up normal turn must NOT carry the hint.
    client._script.append({"content": "normal"})
    agent.chat(src, "next question")
    system2 = (client.calls[1].get("messages") or [{}])[0].get("content") or ""
    assert REGENERATE_HINT not in system2


def test_regenerate_hint_survives_persistence_check(make_agent):
    # Unlike PLAN_APPROVED, the hint rides the SYSTEM message which is rebuilt
    # per turn — nothing to strip from history. Assert the assistant reply and
    # history carry no trace of the hint.
    client = FakeChatClient(script=[{"content": "fresh answer"}])
    agent = make_agent(client, enabled_toolsets=["files"], is_subagent=False)
    src = SessionSource(platform="cli", chat_id="t-regen2")

    agent.chat(src, "q", turn_layer=REGENERATE_HINT)
    sid = agent.db.get_or_create_session(src)
    rows = agent.db.load_messages(sid)
    assert not any(REGENERATE_HINT in (r.get("content") or "") for r in rows)
