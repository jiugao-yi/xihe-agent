"""append_messages persistence contract (append-only history).

The agent loop persists the full transcript after every iteration. Rows
loaded from the DB carry ``_id`` and are skipped on re-append — history is
never rewritten or deleted here (only truncate/delete remove rows). The
compaction summary lives in the compactions table, never in messages.
"""
import json

from core.session import SessionDB, SessionSource


def _mk_db():
    return SessionDB(config={})


def _src(conv="c1"):
    return SessionSource(platform="serve", chat_id=conv, user_id="u", chat_type="dm")


def _sid(db, conv="c1"):
    return db.get_or_create_session(_src(conv))


def _rows(db, sid):
    """All persisted columns in row order — the ground truth for equivalence."""
    return db._execute(
        "SELECT m.role, m.content, m.tool_calls, m.tool_call_id, "
        "mt.reasoning, mt.usage "
        "FROM messages m LEFT JOIN message_meta mt ON mt.message_id = m.id "
        "WHERE m.session_id = ? ORDER BY m.ord", (sid,)).fetchall()


def _ids(db, sid):
    return [r[0] for r in db._execute(
        "SELECT id FROM messages WHERE session_id = ? ORDER BY ord",
        (sid,)).fetchall()]


def _turn(n=3):
    msgs = [{"role": "user", "content": "q"}]
    for i in range(n):
        msgs.append({"role": "assistant",
                     "tool_calls": [{"id": f"t{i}", "type": "function",
                                     "function": {"name": "f", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"r{i}"})
    msgs.append({"role": "assistant", "content": "done"})
    return msgs


def test_repeat_append_is_noop_and_ids_stable():
    """The every-iteration call pattern: re-appending loaded rows (with _id)
    must write nothing new."""
    db = _mk_db()
    sid = _sid(db)
    db.rewrite_messages(sid, _turn())
    ids_full = _ids(db, sid)

    loaded = db.load_messages(sid)
    assert all("_id" in m for m in loaded)
    db.rewrite_messages(sid, loaded)          # identical re-append → no-op
    assert _ids(db, sid) == ids_full

    db.rewrite_messages(sid, loaded + [{"role": "user", "content": "again"}])
    db.rewrite_messages(sid, db.load_messages(sid))  # converges, no duplicates
    assert len(_ids(db, sid)) == len(ids_full) + 1


def test_new_tail_rows_append_with_stable_prefix():
    db = _mk_db()
    sid = _sid(db)
    base = _turn(1)
    db.rewrite_messages(sid, base)
    ids_base = _ids(db, sid)

    db.rewrite_messages(sid, base + [{"role": "user", "content": "next"},
                                     {"role": "assistant", "content": "reply"}])
    assert _ids(db, sid)[:len(ids_base)] == ids_base  # prefix untouched
    assert len(_ids(db, sid)) == len(base) + 2


def test_compaction_shrink_appends_nothing_and_keeps_all_rows():
    """Compression replaces the middle of the in-memory list with a synthetic
    summary row — no messages row may be removed, the summary row is skipped."""
    db = _mk_db()
    sid = _sid(db)
    msgs = _turn(2)
    db.rewrite_messages(sid, msgs)
    ids_before = _ids(db, sid)

    compressed = [dict(msgs[0]),
                  {"role": "user", "content": "(summary)", "_compaction": True},
                  dict(msgs[-1])]
    db.rewrite_messages(sid, compressed)

    assert _ids(db, sid) == ids_before        # rows all still there
    assert len(_rows(db, sid)) == len(msgs)


def test_unpersisted_rows_after_compaction_append_exactly_once():
    """Post-compaction the loop appends the compacted list: historical rows
    (with _id) skipped, only genuinely new rows INSERTed — once."""
    db = _mk_db()
    sid = _sid(db)
    msgs = _turn(2)
    db.rewrite_messages(sid, msgs)

    loaded = db.load_messages(sid)
    compacted = [loaded[0],
                 {"role": "user", "content": "(summary)", "_compaction": True},
                 *loaded[-2:]]
    compacted.append({"role": "user", "content": "post-compaction turn"})
    db.rewrite_messages(sid, compacted)
    db.rewrite_messages(sid, db.load_messages(sid))  # repeat converges

    contents = [r[1] for r in _rows(db, sid)]
    assert contents.count("post-compaction turn") == 1
    assert len(_ids(db, sid)) == len(msgs) + 1


def test_repair_rows_append_in_order():
    """_repair_dangling_tool_calls-style insertion: a synthesized tool row
    (no _id) appended after existing history gets the next ord."""
    db = _mk_db()
    sid = _sid(db)
    db.rewrite_messages(sid, [{"role": "user", "content": "q"},
                              {"role": "assistant", "content": "a"}])
    ids_before = _ids(db, sid)

    db.rewrite_messages(sid, db.load_messages(sid) +
                        [{"role": "tool", "tool_call_id": "tx",
                          "content": "recovered"}])
    ords = [r[0] for r in db._execute(
        "SELECT ord FROM messages WHERE session_id = ? ORDER BY ord", (sid,))]
    assert _ids(db, sid)[:2] == ids_before
    assert ords == sorted(ords) and len(set(ords)) == 3


def test_truncate_invalidates_compaction_and_removes_rows():
    db = _mk_db()
    sid = _sid(db)
    db.rewrite_messages(sid, _turn(2))
    loaded = db.load_messages(sid)
    db.add_compaction(sid, loaded[0]["_id"], loaded[-1]["_id"], "(summary)")

    key = db.build_key(_src())
    cut = loaded[-1]["_id"]
    db.truncate_messages_from(key, sid, cut)

    assert len(_ids(db, sid)) == len(loaded) - 1
    assert db.latest_compaction(sid) is None  # anchor deleted → record dropped
    orphans = db._execute("SELECT count(*) FROM message_meta").fetchone()[0]
    assert orphans == 0


def test_delete_session_sweeps_compactions_all_rounds():
    db = _mk_db()
    key = db.build_key(_src())
    sid1 = _sid(db)
    db.rewrite_messages(sid1, [{"role": "user", "content": "q1"}])
    loaded = db.load_messages(sid1)
    db.add_compaction(sid1, loaded[0]["_id"], loaded[0]["_id"], "(summary)")
    sid2 = db.reset_session(key)
    db.rewrite_messages(sid2, [{"role": "user", "content": "q2"}])

    assert db.delete_session(key) is True
    for sid in (sid1, sid2):
        assert db._execute(
            "SELECT count(*) FROM compactions WHERE session_id = ?",
            (sid,)).fetchone()[0] == 0
    assert db._execute("SELECT count(*) FROM messages WHERE session_key = ?",
                       (key,)).fetchone()[0] == 0


def test_reasoning_and_usage_roundtrip_through_append():
    db = _mk_db()
    sid = _sid(db)
    msgs = [{"role": "assistant", "content": "think",
             "_reasoning": "because", "_usage": {"total": 9, "calls": 1}}]
    db.rewrite_messages(sid, msgs)
    msgs.append({"role": "user", "content": "more"})
    db.rewrite_messages(sid, msgs)

    rows = _rows(db, sid)
    assert rows[0][4] == "because"
    assert json.loads(rows[0][5])["total"] == 9


def test_compaction_view_composes_head_summary_tail():
    db = _mk_db()
    sid = _sid(db)
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    db.rewrite_messages(sid, msgs)
    loaded = db.load_messages(sid)
    db.add_compaction(sid, loaded[2]["_id"], loaded[4]["_id"],
                      "(the middle is summarized)", summary_role="assistant")

    view = db.load_context_messages(sid)
    assert [m["content"] for m in view] == ["m0", "m1", "m2",
                                            "(the middle is summarized)",
                                            "m4", "m5"]
    assert view[3]["_compaction"] is True

    # raw rows untouched — the desktop view stays complete
    assert len(db.load_messages(sid)) == 6


def test_compaction_stale_anchor_falls_back_to_full_view():
    db = _mk_db()
    sid = _sid(db)
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(4)]
    db.rewrite_messages(sid, msgs)
    loaded = db.load_messages(sid)
    db.add_compaction(sid, loaded[0]["_id"], loaded[-1]["_id"], "(summary)")

    # delete the tail anchor row directly (truncate path)
    db._execute("DELETE FROM messages WHERE id = ?", (loaded[-1]["_id"],))
    db._commit()

    view = db.load_context_messages(sid)
    assert len(view) == 3
    assert db.latest_compaction(sid) is None  # stale record dropped
