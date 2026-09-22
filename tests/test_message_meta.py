"""L1 — messages/message_meta 拆分的行为契约（uuid 主键重构的回归防线）：

- meta round-trip: rewrite 落库 → load 带回 _reasoning/_usage/_attachments
- approval 剥离: tool 结果 JSON 里的 approval_record 落库时摘到 meta，
  content 保持纯工具输出；模型面向 load 不回带，展示 load 经 meta 还原
- truncate 级联: 主行删除后 meta 无孤儿（foreign_keys 关闭，显式清扫）
- ord: 会话内严格递增，跨 reset 轮继续增长（transcript 排序的唯一锚）
"""
import json

from core.session import SessionDB, SessionSource


def _mk_db():
    return SessionDB(config={})


def _src(conv="c1"):
    return SessionSource(platform="serve", chat_id=conv, user_id="u", chat_type="dm")


def test_meta_roundtrip_through_rewrite():
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    db.rewrite_messages(sid, [
        {"role": "user", "content": "看附件", "_attachments":
            [{"name": "a.csv", "path": "/tmp/a.csv", "size": 10}]},
        {"role": "assistant", "content": "thinking...", "_reasoning": "内心独白"},
        {"role": "assistant", "content": "结论", "_usage":
            {"prompt": 7, "completion": 3, "total": 10, "calls": 1}},
    ])
    loaded = db.load_messages(sid)
    assert loaded[0]["_attachments"][0]["name"] == "a.csv"
    assert loaded[1]["_reasoning"] == "内心独白"
    assert loaded[2]["_usage"]["total"] == 10
    # 再 rewrite 一次（模拟下一轮），meta 仍从 load 回来（不清空）
    loaded.append({"role": "user", "content": "继续"})
    db.rewrite_messages(sid, loaded)
    loaded2 = db.load_messages(sid)
    assert loaded2[0]["_attachments"][0]["name"] == "a.csv"
    assert loaded2[1]["_reasoning"] == "内心独白"
    assert loaded2[2]["_usage"]["total"] == 10


def test_approval_record_stripped_to_meta():
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    tool_result = json.dumps({
        "exit_code": 0, "output": "done",
        "approval_record": {"decision": "approved", "summary": "危险命令（recursive delete）",
                            "reason": "用户批准", "always": True},
    })
    db.rewrite_messages(sid, [
        {"role": "user", "content": "删了它"},
        {"role": "assistant", "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "terminal", "arguments": '{"command": "rm -r /tmp/x"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": tool_result},
    ])
    # 库里主行 content 无 approval_record
    raw = db._execute(
        "SELECT m.content, mt.approval FROM messages m "
        "JOIN message_meta mt ON mt.message_id = m.id "
        "WHERE m.role = 'tool'").fetchone()
    content, approval = raw
    assert "approval_record" not in content
    assert json.loads(content)["exit_code"] == 0
    assert json.loads(approval)["decision"] == "approved"
    # 模型面向 load 不带 approval
    loaded = db.load_messages(sid)
    assert "approval" not in loaded[2]
    assert "approval_record" not in loaded[2]["content"]


def test_legacy_approval_summary_key_also_stripped():
    """dispatch 旧形态：拒绝路径只带 approval 摘要字符串（无完整 record）——
    持久化时同样剥离，并补成 record 形态入 meta。"""
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    tool_result = json.dumps({
        "error": "操作未获批准（用户拒绝），未执行。",
        "blocked": True,
        "approval": "危险命令（empty the recycle bin）：Clear-RecycleBin -Force",
    })
    db.rewrite_messages(sid, [
        {"role": "user", "content": "清回收站"},
        {"role": "tool", "tool_call_id": "t9", "content": tool_result},
    ])
    raw = db._execute(
        "SELECT m.content, mt.approval FROM messages m "
        "JOIN message_meta mt ON mt.message_id = m.id "
        "WHERE m.role = 'tool'").fetchone()
    content, approval = raw
    data = json.loads(content)
    assert "approval" not in data          # 摘要键剥离
    assert data["blocked"] is True         # 模型面向的错误标记保留
    assert "操作未获批准" in data["error"]  # 模型面向的错误文本保留
    rec = json.loads(approval)
    assert rec["decision"] == "rejected"
    assert "recycle bin" in rec["summary"]
    # 展示 load 经 meta 还原（模型面向 load_messages 不还原 approval）
    shown = db.load_transcript(db.build_key(_src()))
    assert shown[1]["approval"]["decision"] == "rejected"
    loaded = db.load_messages(sid)
    assert "approval" not in loaded[1]


def test_fts_holds_cleaned_content_not_dirty_json():
    """剥离必须在 INSERT 之前（先洗再插）：INSERT 触发器把 content 写进 FTS，
    若落库后再 UPDATE 洗净，FTS 留脏值且行数对账发现不了（索引漂移）。"""
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    dirty = json.dumps({"exit_code": 0, "output": "SECRET_OUTPUT_MARK",
                        "approval_record": {"decision": "approved",
                                            "summary": "danger op"}})
    db.rewrite_messages(sid, [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "t1", "content": dirty},
    ])
    fts_contents = [r[0] for r in db._execute(
        "SELECT content FROM messages_fts").fetchall()]
    assert any("SECRET_OUTPUT_MARK" in c for c in fts_contents)   # 净输出已索引
    assert not any("approval_record" in c for c in fts_contents)  # 脏键不入索引


def test_truncate_unknown_id_returns_zero_with_no_side_effect():
    """truncate 不存在的 id：返回 0（不误删），不抛错。"""
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    db.rewrite_messages(sid, [{"role": "user", "content": "q1"}])
    assert db.truncate_messages_from(db.build_key(_src()), sid, "nonexistent") == 0
    assert len(db.load_messages_with_id(sid)) == 1
    db = _mk_db()
    sid = db.get_or_create_session(_src())
    db.rewrite_messages(sid, [
        {"role": "user", "content": "q", "_attachments":
            [{"name": "x.txt", "path": "/tmp/x", "size": 1}]},
        {"role": "assistant", "content": "a", "_reasoning": "r"},
    ])
    rows = db.load_messages_with_id(sid)
    deleted = db.truncate_messages_from(db.build_key(_src()), sid, rows[0]["id"])
    assert deleted == 2
    assert db.load_transcript(db.build_key(_src())) == []
    orphans = db._execute("SELECT count(*) FROM message_meta").fetchone()[0]
    assert orphans == 0


def test_ord_monotonic_within_and_across_rounds():
    db = _mk_db()
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    db.rewrite_messages(sid1, [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])
    sid2 = db.reset_session(key)  # new round
    db.rewrite_messages(sid2, [{"role": "user", "content": "q2"}])

    # transcript 顺序即 ord 顺序，且跨轮严格递增
    assert [m["content"] for m in db.load_transcript(key)] == ["q1", "a1", "q2"]
    raw_ords = db._execute(
        "SELECT ord FROM messages WHERE session_key = ? ORDER BY ord", (key,)
    ).fetchall()
    vals = [r[0] for r in raw_ords]
    assert vals == sorted(vals) and len(set(vals)) == 3
