"""Reset-round contracts: a conversation's transcript and title survive a
round switch (reset), while the agent context stays scoped to the current
round. Delete/truncate are conversation-scoped (all rounds)."""

from core.session import SessionDB, SessionSource


def _src(conv="c1"):
    return SessionSource(platform="serve", chat_id=conv, user_id="desktop", chat_type="dm")


def test_reset_preserves_title_and_model():
    db = SessionDB({})
    key = db.build_key(_src())
    sid = db.get_or_create_session(_src())
    db.set_session_title(sid, "调研")
    db.set_session_model(key, "glm-flash")

    new_sid = db.reset_session(key)

    assert new_sid and new_sid != sid
    # INSERT OR REPLACE here used to wipe both columns on every reset
    assert db.get_session_title(new_sid) == "调研"
    assert db.get_session_model(key) == "glm-flash"


def test_transcript_spans_reset_rounds():
    db = SessionDB({})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    db.rewrite_messages(sid1, [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])

    sid2 = db.reset_session(key)
    db.rewrite_messages(sid2, [{"role": "user", "content": "q2"}])

    rows = db.load_transcript(key)
    assert [r["content"] for r in rows] == ["q1", "a1", "q2"]
    # the model's context is per-round, not per-conversation
    assert [m["content"] for m in db.load_messages(sid2)] == ["q2"]


def test_delete_removes_all_rounds():
    db = SessionDB({})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    db.rewrite_messages(sid1, [{"role": "user", "content": "q1"}])
    sid2 = db.reset_session(key)
    db.rewrite_messages(sid2, [{"role": "user", "content": "q2"}])

    assert db.delete_session(key) is True

    assert db.load_transcript(key) == []
    assert db.get_entry(key) is None
    assert db.reset_marks(key) == {}


def test_reset_marks_persist_reasons():
    db = SessionDB({})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    assert db.reset_marks(key) == {}  # the first round is creation, not a reset

    sid2 = db.reset_session(key)
    assert db.reset_marks(key) == {sid2: "manual"}


def test_auto_reset_marks_reason():
    from datetime import datetime, timedelta

    db = SessionDB({"session": {"default_reset": "idle"}})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    # _now() truncates to seconds — rewind the timestamp past the 24h idle
    # deadline instead of relying on wall-clock time.
    entry = db.get_entry(key)
    entry.updated_at = (
        datetime.fromisoformat(entry.updated_at) - timedelta(minutes=1500)
    ).isoformat()

    sid2 = db.get_or_create_session(_src())

    assert sid2 != sid1
    assert db.reset_marks(key) == {sid2: "idle"}


def test_pending_auto_reset_prediction():
    from datetime import datetime, timedelta

    db = SessionDB({"session": {"default_reset": "idle"}})
    src = _src()
    assert db.pending_auto_reset(src) is None  # no entry yet → nothing to reset

    sid = db.get_or_create_session(src)
    assert db.pending_auto_reset(src) is None  # inside the idle window

    entry = db.get_entry(db.build_key(src))
    entry.updated_at = (
        datetime.fromisoformat(entry.updated_at) - timedelta(minutes=1500)
    ).isoformat()
    assert db.pending_auto_reset(src) == "idle"

    sid2 = db.get_or_create_session(src)  # the predicted reset fires
    assert sid2 != sid
    assert db.reset_marks(db.build_key(src)) == {sid2: "idle"}


def test_reshape_marks_round_boundaries():
    from gateway.serve.conversations import _reshape_history

    rows = [
        {"id": 1, "role": "user", "content": "q1", "session_id": "A",
         "created_at": "2026-09-15T10:00:00"},
        {"id": 2, "role": "assistant", "content": "a1", "session_id": "A"},
        {"id": 3, "role": "user", "content": "q2", "session_id": "B"},
        {"id": 4, "role": "assistant", "content": "a2", "session_id": "B"},
    ]
    out = _reshape_history(rows, {"B": "manual"})
    assert "round_start" not in out[0]
    assert out[2]["round_start"] is True
    assert out[2]["reset_reason"] == "manual"

    # a boundary without a recorded reason still flags the round
    out2 = _reshape_history(rows)
    assert out2[2]["round_start"] is True
    assert "reset_reason" not in out2[2]


def test_truncate_spans_rounds():
    db = SessionDB({})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    db.rewrite_messages(sid1, [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])
    sid2 = db.reset_session(key)
    db.rewrite_messages(sid2, [{"role": "user", "content": "q2"}])
    q1_id = db.load_transcript(key)[0]["id"]

    deleted = db.truncate_messages_from(key, sid2, q1_id)

    assert deleted == 3
    assert db.load_transcript(key) == []


def test_search_spans_reset_rounds():
    db = SessionDB({})
    key = db.build_key(_src())
    sid1 = db.get_or_create_session(_src())
    db.rewrite_messages(sid1, [{"role": "user", "content": "唯一标记词 zebra"}])
    sid2 = db.reset_session(key)
    db.rewrite_messages(sid2, [{"role": "user", "content": "current round"}])

    hits = db.search("zebra")

    assert any("zebra" in (h["content"] or "") for h in hits)


def test_old_integer_key_schema_rejected(tmp_path, monkeypatch):
    """The pre-uuid messages table (integer autoincrement pk, no ord column)
    is incompatible with current read/write paths — opening it must fail
    loudly (a schema mismatch surfaces as SQL errors), never half-work."""
    import sqlite3
    import core.session as session_mod
    import pytest

    monkeypatch.setattr(session_mod, "_DB_PATH", tmp_path / "old.db")
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT, tool_calls TEXT, tool_call_id TEXT,
            created_at TEXT);
    """)
    conn.commit()
    conn.close()

    with pytest.raises(Exception):
        session_mod.SessionDB()
