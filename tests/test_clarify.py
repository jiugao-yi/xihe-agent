"""L1: clarify as a first-class mid-turn interaction — the MidturnHub
skeleton it now shares with approvals, plus the tool/agent glue."""

import json
import threading

from core.support.interaction import MidturnHub
from tools.clarify_tool import _clarify, try_resolve_clarify


# ---- MidturnHub outcomes -------------------------------------------------------

def test_hub_answered():
    hub = MidturnHub(lambda: False)

    def _ask(info):
        threading.Timer(0.05, lambda: hub.resolve(info["id"], "opt A")).start()

    q = hub.request("clarify", {"question": "q"}, on_request=_ask, timeout=5)
    assert q.status == "answered"
    assert q.answer == "opt A"
    assert hub.pending is None


def test_hub_no_callback_and_error():
    hub = MidturnHub(lambda: False)
    q = hub.request("clarify", {}, None, 5)
    assert q.status == "no_callback"

    def _boom(info):
        raise RuntimeError("channel down")

    q2 = hub.request("clarify", {}, on_request=_boom, timeout=5)
    assert q2.status == "callback_error"


def test_hub_timeout_and_interrupt():
    hub = MidturnHub(lambda: False)
    q = hub.request("clarify", {}, on_request=lambda info: None, timeout=0.2)
    assert q.status == "timeout"

    hub2 = MidturnHub(lambda: True)  # already interrupted
    q2 = hub2.request("clarify", {}, on_request=lambda info: None, timeout=30)
    assert q2.status == "interrupted"


def test_hub_busy_single_pending():
    hub = MidturnHub(lambda: False)
    release = threading.Event()

    def _hold(info):
        release.wait(5)

    t = threading.Thread(
        target=lambda: hub.request("clarify", {}, on_request=_hold, timeout=30))
    t.start()
    # wait until the holder's pending registers
    for _ in range(100):
        if hub.pending is not None:
            break
        threading.Event().wait(0.02)
    q = hub.request("approval", {}, on_request=lambda info: None, timeout=1)
    assert q.status == "busy"
    release.set()
    t.join(timeout=5)


# ---- agent glue ---------------------------------------------------------------

def test_request_clarification_answered(make_agent):
    from tests.fakes import FakeChatClient
    agent = make_agent(FakeChatClient())

    def _cb(info):
        agent.resolve_clarification(info["id"], "第二个选项")

    agent._clarify_shared["request_cb"] = _cb
    status, answer = agent.request_clarification("选哪个", ["a", "b"])
    assert (status, answer) == ("answered", "第二个选项")


def test_request_clarification_unattended(make_agent):
    from tests.fakes import FakeChatClient
    agent = make_agent(FakeChatClient())
    status, reason = agent.request_clarification("在吗", [])
    assert status == "no_callback"
    assert reason


# ---- tool face ----------------------------------------------------------------

def test_clarify_tool_unattended_falls_back_to_instruction():
    result = json.loads(_clarify(question="q?", options=["a"], reason="r"))
    assert result["action"] == "clarify"
    assert result["instruction"]
    assert result["options"] == ["a"]


def test_clarify_tool_delivers_answer(make_agent):
    from tests.fakes import FakeChatClient
    agent = make_agent(FakeChatClient())

    def _cb(info):
        agent.resolve_clarification(info["id"], "b")

    agent._clarify_shared["request_cb"] = _cb
    res = json.loads(_clarify(question="q?", options=["a", "b"],
                              parent_agent=agent))
    assert res["status"] == "answered"
    assert res["answer"] == "b"


def test_clarify_tool_unanswered(make_agent):
    from tests.fakes import FakeChatClient
    agent = make_agent(FakeChatClient())
    agent._clarify_shared["request_cb"] = lambda info: None  # never answered
    agent.config["clarify"] = {"timeout": 0.2}
    res = json.loads(_clarify(question="q?", parent_agent=agent))
    assert res["status"] == "unanswered"
    assert res["instruction"]


def test_try_resolve_clarify_routes_inbound(make_agent):
    from core.support.interaction import MidturnQuestion
    from tests.fakes import FakeChatClient
    agent = make_agent(FakeChatClient())
    q = MidturnQuestion("clarify", {"question": "q", "options": ["a"]})
    agent._midturn._pending = q

    assert try_resolve_clarify(agent, "随便输入的") is True
    assert q.answer == "随便输入的"
    # resolved once → a second inbound must NOT re-match
    assert try_resolve_clarify(agent, "再来一句") is False
    # and a clean agent matches nothing
    agent2 = make_agent(FakeChatClient())
    assert try_resolve_clarify(agent2, "y") is False
