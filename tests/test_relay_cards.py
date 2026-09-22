"""L0/L1 — 中继卡路由表 + 企微卡片协议面。

卡片路由（approvals.register_card / card_click / unregister_card）是纯逻辑；
企微卡片构造与事件解析用 WeComAdapter 的静态/模块级函数，不打真连接。
"""

import pytest

from core.support.approvals import (
    card_click,
    card_meta,
    parse_approval_reply,
    register_card,
    unregister_card,
)
from gateway.platforms.wecom import (
    build_approval_card,
    build_approval_result_card,
    build_clarify_card,
    build_clarify_result_card,
)


@pytest.fixture(autouse=True)
def _clean_routes():
    from core.support.approvals import _card_routes
    _card_routes.clear()
    yield
    _card_routes.clear()


# ---- L0: 卡片构造 -----------------------------------------------------------


def test_approval_card_shape():
    card = build_approval_card("rm -rf /tmp/x", "approval-abc123")
    assert card["card_type"] == "button_interaction"
    # 实测（2026-09 spike 二分）：task_id 含冒号则整卡静默不渲染——编码只用
    # 字母数字与连字符；source 字段同理致不渲染，不得出现
    assert ":" not in card["task_id"]
    assert "source" not in card
    keys = [b["key"] for b in card["button_list"]]
    assert keys == ["y", "n", "a"]
    for b in card["button_list"]:
        assert set(b) == {"text", "style", "key"}  # 官方文档字段，无 type
        assert 0 <= b["style"] <= 3
        assert parse_approval_reply(b["key"]) is not None  # 按钮语义可被 parse 还原


def test_approval_result_card_shape():
    ok = build_approval_result_card("s", "y", task_id="approval-x1")
    assert ok["card_type"] == "button_interaction"  # 更新卡保持原卡类型
    assert ok["task_id"] == "approval-x1"  # 原样回传——服务端靠它匹配目标卡
    assert "已批准" in ok["main_title"]["title"]  # 终态读 title
    # 三按钮布局保留：选中的 style 1 高亮，其余 style 3 灰态。按钮文字
    # 不含 emoji（实测 ✅ 前缀渲染不出）；语义完整优先于显示完整。
    keys = [b["key"] for b in ok["button_list"]]
    assert keys == ["y", "n", "a"]
    y_btn = ok["button_list"][0]
    n_btn = ok["button_list"][1]
    assert y_btn["text"] == "已批准" and y_btn["style"] == 1
    assert n_btn["text"] == "拒绝" and n_btn["style"] == 3
    assert all("✅" not in b["text"] for b in ok["button_list"])
    no = build_approval_result_card("s", "n", task_id="t")
    assert "已拒绝" in no["main_title"]["title"]
    assert no["button_list"][1]["text"] == "已拒绝"
    always = build_approval_result_card("s", "a", task_id="t")
    assert "总是允许" in always["main_title"]["title"]
    assert always["button_list"][2]["text"] == "总是允许"


# ---- L0/L1: 路由表语义 ------------------------------------------------------


def test_card_click_resolves():
    got = []
    register_card("approval", "a1", lambda choice: got.append(choice) or True)
    assert card_click("approval", "a1", "y") is True
    assert got == [True]


def test_card_click_translate_choices():
    got = {}
    register_card("approval", "a2",
                  lambda c: got.setdefault("c", c) or True)
    card_click("approval", "a2", "n")
    assert got["c"] is False
    register_card("approval", "a3", lambda c: got.setdefault("d", c) or True)
    card_click("approval", "a3", "a")
    assert got["d"] == "always"


def test_unregister_reports_disposition():
    # 未登记（纯文本路径）
    assert unregister_card("approval", "x") is None
    # 登记后未点（文本/超时了结）
    register_card("approval", "a4", lambda c: True)
    assert unregister_card("approval", "a4") == "unclicked"
    # 登记后点击裁决
    register_card("approval", "a5", lambda c: True)
    card_click("approval", "a5", "y")
    assert unregister_card("approval", "a5") == "clicked"
    # 清理后迟到点击被拒
    assert card_click("approval", "a5", "y") is False


def test_double_click_collapses_to_single_resolve():
    calls = []
    register_card("approval", "a6", lambda c: calls.append(c) or True)
    assert card_click("approval", "a6", "y") is True
    assert card_click("approval", "a6", "n") is False  # 第二击：已裁决
    assert calls == [True]


def test_unknown_card_rejected():
    assert card_click("approval", "nope", "y") is False


def test_clarify_kind_routes_option_text():
    got = []
    register_card("clarify", "q1", lambda c: got.append(c) or True)
    assert card_click("clarify", "q1", "用生产环境配置") is True
    assert got == ["用生产环境配置"]


def test_resolve_exception_still_counts_as_handled():
    def _boom(_choice):
        raise RuntimeError("agent gone")
    register_card("approval", "a7", _boom)
    assert card_click("approval", "a7", "y") is True


def test_card_meta_roundtrip():
    """meta 随卡存取（终态卡回填 summary 用），弹出后即消失。"""
    register_card("approval", "m1", lambda c: True, meta={"summary": "rm -rf /x"})
    assert card_meta("approval", "m1") == {"summary": "rm -rf /x"}
    assert card_meta("approval", "nope") == {}
    unregister_card("approval", "m1")
    assert card_meta("approval", "m1") == {}


# ---- clarify 选择卡 ---------------------------------------------------------


def test_clarify_card_shape():
    card = build_clarify_card("用哪个环境？", ["生产环境配置呀呀呀", "测试环境"], "clarify-q1")
    # 官方 vote schema（2026-09 spike 实测渲染）：选项在 checkbox 子对象 +
    # submit_button；顶层 option_list 是无效结构（静默不渲染的冤案根源）
    assert card["card_type"] == "vote_interaction"
    assert card["task_id"] == "clarify-q1"
    assert ":" not in card["task_id"]
    assert "source" not in card
    assert card["submit_button"]["key"] == "submit"
    cb = card["checkbox"]
    assert cb["mode"] == 0  # 单选（clarify 默认单问单答）
    ids = [o["id"] for o in cb["option_list"]]
    assert ids == ["o1", "o2"]
    # 选项文案不截断（用户确认语义完整优先）
    assert cb["option_list"][0]["text"] == "生产环境配置呀呀呀"
    assert cb["option_list"][1]["text"] == "测试环境"
    # desc 只放问题——选项清单不再拼入（与 checkbox 重复渲染，已撤）
    assert card["main_title"]["desc"] == "用哪个环境？"
    assert "选项：" not in card["main_title"]["desc"]


def test_clarify_card_option_cap():
    """checkbox.option_list 上限 20（官方）。"""
    card = build_clarify_card("q", [f"选项{i}" for i in range(25)], "clarify-q4")
    assert len(card["checkbox"]["option_list"]) == 20


def test_clarify_card_empty_options():
    card = build_clarify_card("开放问题", [], "clarify-q2")
    assert card["checkbox"]["option_list"] == []  # 无选项走文本兜底，不发卡


def test_clarify_choice_decodes_via_handler_contract():
    """选项点击的语义契约：提交事件携带 oN 序号 id（selected_items），
    点击路径折回选项原文再交路由层（bot.py _handle_card_event 职责）。"""
    got = []
    opts = ["生产环境配置", "测试环境"]
    register_card("clarify", "q3", lambda c: got.append(c) or True)
    card_click("clarify", "q3", "生产环境配置")  # 折算后路由层收到原文
    assert got == ["生产环境配置"]


# ---- task_id 编码契约 + 通道健康 ---------------------------------------------


def test_task_id_roundtrip_and_malformed():
    from gateway.bot import _parse_task_id, encode_task_id
    tid = encode_task_id("approval", "abc123")
    assert tid == "approval-abc123"
    assert ":" not in tid  # 毒字段红线
    assert _parse_task_id(tid) == ("approval", "abc123")
    assert _parse_task_id("clarify-q1") == ("clarify", "q1")
    # 未知 kind / 缺分隔符 → 拒绝（防含连字符 id 被截断出错误路由）
    assert _parse_task_id("bogus-abc") == (None, "")
    assert _parse_task_id("noseparator") == (None, "")


def test_card_channel_health_probe_and_disable(tmp_path):
    """探测期双发（events==0 → fallback 分支条件）→ 事件回流证实 → 停用
    判定不再触发。"""
    from gateway.bot import CardChannelHealth
    health = CardChannelHealth(tmp_path / "cards.json")
    assert health.alive(type("A", (), {"send_card": staticmethod(lambda *a, **k: None)})())
    health.note_sent()
    health.note_settled()
    assert not health.disabled  # 探测卡未满 PROBE_MIN
    health.note_sent()
    health.note_settled()
    assert health.disabled  # 2 张 0 事件 → 停用
    # 事件回流后落盘；新实例读回后不再停用（通道已证实）
    health2 = CardChannelHealth(tmp_path / "cards2.json")
    health2.note_sent()
    health2.note_sent()
    health2.note_event()
    health2.note_settled()
    assert not health2.disabled
    health3 = CardChannelHealth(tmp_path / "cards2.json")
    assert health3.events == 1  # 持久化读回


def test_clarify_result_card_shape():
    card = build_clarify_result_card("生产环境配置", "clarify-q1")
    assert card["card_type"] == "vote_interaction"  # 与选项卡同族
    assert card["task_id"] == "clarify-q1"  # 原样回传
    assert "已收到回答" in card["main_title"]["title"]


def test_extract_selected_ids():
    """vote/multiple 提交事件的选中项抽取（实测 XML 风格嵌套结构）。"""
    from gateway.platforms.wecom import _extract_selected_ids
    tc = {"selected_items": {"selected_item": [
        {"question_key": "q", "option_ids": {"option_id": ["o1", "o2"]}},
    ]}}
    assert _extract_selected_ids(tc) == ["o1", "o2"]
    # 按钮卡事件无此字段
    assert _extract_selected_ids({}) == []
    assert _extract_selected_ids({"selected_items": {}}) == []


def test_non_choice_event_key_swallowed():
    """未知 event_key（如卡面其他交互）不触发裁决也不误报失效。"""
    got = []
    register_card("approval", "a8", lambda c: got.append(c) or True)
    assert card_click("approval", "a8", "random_key") is True
    assert got == []  # 无决议送达，但卡不算失效（仍是 True）
    # 条目保持可再点
    assert card_click("approval", "a8", "y") is True
    assert got == [True]
