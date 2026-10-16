"""ContextCompressor per-session summary isolation.

SharedContext shares one compressor across every chat — the progressive
summary (PREVIOUS SUMMARY) must be scoped per session or chat A's compaction
prompt carries chat B's summary. The source of truth is the compactions
table (via the db handle); a compressor without a db (tests, legacy callers)
has no progressive memory.
"""
from types import SimpleNamespace

from core.agent.compressor import ContextCompressor


class FakeDB:
    """Minimal SessionDB stand-in: entries + compactions in memory."""

    def __init__(self):
        self._entries = {}
        self._compactions = {}

    def add_entry(self, session_key, session_id):
        self._entries[session_key] = SimpleNamespace(session_id=session_id)

    def get_entry(self, session_key):
        return self._entries.get(session_key)

    def add_compaction(self, session_id, head_end_id, tail_start_id,
                       summary, summary_role="user"):
        self._compactions.setdefault(session_id, []).append({
            "id": f"c{len(self._compactions)}",
            "head_end_id": head_end_id, "tail_start_id": tail_start_id,
            "summary": summary, "summary_role": summary_role,
        })

    def latest_compaction(self, session_id):
        rows = self._compactions.get(session_id)
        return rows[-1] if rows else None


class FakeAux:
    def __init__(self):
        self.prompts = []

    def call_llm(self, task, messages, max_tokens):
        self.prompts.append(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=f"summary#{len(self.prompts)}"))])


def _msgs(n=20):
    out = [{"role": "system", "content": "s", "_id": "m0"},
           {"role": "user", "content": "hi", "_id": "m1"}]
    for i in range(n):
        out.append({"role": "assistant", "content": f"answer {i} " * 50,
                    "_id": f"a{i}"})
        out.append({"role": "user", "content": f"next {i}", "_id": f"u{i}"})
    return out


def test_previous_summary_isolated_per_session():
    aux = FakeAux()
    db = FakeDB()
    db.add_entry("chat:A", "sid:A")
    db.add_entry("chat:B", "sid:B")
    c = ContextCompressor(context_length=4000, threshold_percent=0.05,
                          aux=aux, db=db)

    c.compress(_msgs(), session_key="chat:A", session_id="sid:A")
    assert "PREVIOUS SUMMARY" not in aux.prompts[0]

    c.compress(_msgs(), session_key="chat:B", session_id="sid:B")
    # B's first compaction must NOT see A's summary
    assert "PREVIOUS SUMMARY" not in aux.prompts[1]

    c.compress(_msgs(), session_key="chat:A", session_id="sid:A")
    # A's second compaction resumes from A's own summary only
    assert "summary#1" in aux.prompts[2]
    assert "summary#2" not in aux.prompts[2]


def test_no_db_means_no_progressive_summary():
    aux = FakeAux()
    c = ContextCompressor(context_length=4000, threshold_percent=0.05, aux=aux)
    c.compress(_msgs())
    c.compress(_msgs())
    assert "PREVIOUS SUMMARY" not in aux.prompts[1]
