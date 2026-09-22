"""Harness plan mode (core/agent/turn_layers.py + XiheAgent.chat(plan_mode)):
roster is physically restricted to the base read-only floor for the turn and
restored afterwards. Approval is STRUCTURED — the model submits the plan via
clarify, the user's click on 批准执行 resolves it, and resolve_clarification
records agent.plan_approved. No text parsing of model output.
"""

import json

import pytest

from core.agent.turn_layers import (
    PLAN_APPROVED_ANSWER,
    PLAN_SUBMIT_OPTIONS,
    execution_message,
    save_plan,
)


class TestPlanPersistence:
    def test_save_and_execution_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.config.AGENT_HOME", tmp_path)
        p = save_plan("# My Feature Plan\nstep 1")
        assert p.exists() and p.parent.name == "plans"
        assert "My Feature Plan" in p.read_text(encoding="utf-8")
        msg = execution_message("step 1", p)
        assert "step 1" in msg and str(p) in msg

    def test_submit_options_shape(self):
        assert PLAN_APPROVED_ANSWER == PLAN_SUBMIT_OPTIONS[0]


class TestRosterEnforcement:
    def test_plan_turn_restricts_tools_and_restores(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "plan body"}])
        # Plan mode is a main-agent feature — clarify is subagent_blocked.
        agent = make_agent(client, enabled_toolsets=["files", "terminal"],
                           is_subagent=False)
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-plan")

        result = agent.chat(src, "plan something", plan_mode=True)

        # The API call saw ONLY read-only faces (+ clarify): no write/exec tool.
        sent = {t["function"]["name"] for t in client.calls[0].get("tools") or []}
        assert "read_file" in sent and "clarify" in sent
        assert "write_file" not in sent and "terminal" not in sent
        assert "plan body" in result
        # Roster restored after the turn.
        assert agent.enabled_toolsets >= {"files", "terminal", "base"}

    def test_plan_layer_in_prompt(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "ok"}])
        agent = make_agent(client, enabled_toolsets=["files"])
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-plan2")

        agent.chat(src, "x", plan_mode=True)
        sys_text = json.dumps(client.calls[0].get("messages") or [], ensure_ascii=False)
        assert "PLAN MODE" in sys_text and "clarify" in sys_text

    def test_approval_recorded_on_structured_answer(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "plan body"}])
        agent = make_agent(client, enabled_toolsets=["files"], is_subagent=False)
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-approve")

        agent.chat(src, "x", plan_mode=True)
        assert not agent.plan_approved
        # The real click lands mid-turn while the clarify blocks the loop; the
        # unit test drives the registration path directly with the turn live.
        agent._plan_mode_live = True
        agent.resolve_clarification(answer=PLAN_APPROVED_ANSWER)
        assert agent.plan_approved

    def test_free_text_answer_does_not_approve(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "plan body"}])
        agent = make_agent(client, enabled_toolsets=["files"], is_subagent=False)
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-revise")

        agent.chat(src, "x", plan_mode=True)
        agent._plan_mode_live = True
        agent.resolve_clarification(answer="把风险节再展开一些")
        assert not agent.plan_approved  # revision feedback, not approval

    def test_normal_turn_unaffected(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "hi"}])
        agent = make_agent(client, enabled_toolsets=["files", "terminal"])
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-norm")

        agent.chat(src, "hello")
        sent = {t["function"]["name"] for t in client.calls[0].get("tools") or []}
        assert "write_file" in sent and "terminal" in sent

    def test_plan_layer_text_never_persisted(self, make_agent):
        from tests.fakes import FakeChatClient

        client = FakeChatClient(script=[{"content": "my plan"}])
        agent = make_agent(client, enabled_toolsets=["files"], is_subagent=False)
        from core.session import SessionSource
        src = SessionSource(platform="cli", chat_id="t-hist")

        agent.chat(src, "plan x", plan_mode=True)
        sid = agent.db.get_or_create_session(src)
        rows = agent.db.load_messages(sid)
        assert not any("PLAN MODE" in (r.get("content") or "") for r in rows)
