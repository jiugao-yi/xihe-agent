"""Context compression — summarize middle turns when approaching the model limit.

L1 compression is a pure view operation: it writes one row to the
``compactions`` table (head anchor, tail anchor, summary) and never touches
messages rows. The model's context is assembled per turn by
``SessionDB.load_context_messages`` from raw rows + the newest compaction.
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.agent.auxiliary_client import AuxiliaryClient

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier conversation turns were compacted. "
    "The summary below describes completed work. Continue from where "
    "things left off and avoid repeating work:\n"
)

PRUNED_PLACEHOLDER = "[Old tool output cleared to save context space]"
CHARS_PER_TOKEN = 4

SUMMARY_PROMPT = """Create a structured handoff summary for a later assistant.

TURNS TO SUMMARIZE:
{content}

Use this structure:
## Goal / Intent
## User Messages
## Progress (Done / In Progress / Blocked)
## Key Decisions
## Key Facts & State
## Pending Tasks
## Next Steps

Key Facts & State records every fact a successor must not forget: file paths
and the nature of changes made, key commands run and their outcome, URLs and
endpoints, browser pages and login state, external-system interactions
(approvals / cards / pending replies), scheduled jobs, error messages. Record
facts and pointers only — never restate file contents verbatim; exact
content lives in the filesystem and can be re-read on demand.
"""

UPDATE_SUMMARY_PROMPT = """Update the context compaction summary below by incorporating new turns.

PREVIOUS SUMMARY:
{previous}

NEW TURNS:
{content}

Use the same structure:
## Goal / Intent
## User Messages
## Progress (Done / In Progress / Blocked)
## Key Decisions
## Key Facts & State
## Pending Tasks
## Next Steps

Key Facts & State records every fact a successor must not forget: file paths
and the nature of changes made, key commands run and their outcome, URLs and
endpoints, browser pages and login state, external-system interactions
(approvals / cards / pending replies), scheduled jobs, error messages. Record
facts and pointers only — never restate file contents verbatim.
"""


class ContextCompressor:
    """Compress conversation context when approaching the model's context limit."""

    def __init__(
        self,
        context_length: int,
        threshold_percent: float = 0.75,
        aux: Optional["AuxiliaryClient"] = None,
        db=None,
    ):
        self.context_length = context_length
        self.threshold_tokens = int(context_length * threshold_percent)
        self._aux = aux
        self._db = db
        self.compression_count = 0

    def should_compress(self, messages: list[dict]) -> bool:
        """Quick pre-flight check using rough estimate."""
        rough = self._estimate_tokens(self.lightweight_prune(messages))
        return rough >= self.threshold_tokens

    def compress(self, messages: list[dict],
                 session_key: Optional[str] = None,
                 session_id: Optional[str] = None) -> list[dict]:
        """Summarize the middle turns; persist the result as one compaction
        record and return the compacted list (head + summary + tail).

        The returned list is the model's view going forward; with ``session_id``
        set it is also what ``load_context_messages`` will rebuild from the DB.
        Without a db/session_id (legacy callers, tests) the compaction lives
        only in the returned list.
        """
        # Need at least head + some middle + 3 tail
        min_needed = 3 + 3 + 1
        if len(messages) <= min_needed:
            return messages

        # Anchors need stable row ids; without them (raw in-memory lists) fall
        # back to view-only compaction (no DB record).
        head_end = self._align_head(messages)
        tail_start = self._find_tail_start(messages, head_end)
        if head_end >= tail_start:
            return messages

        turns_to_summarize = messages[head_end:tail_start]
        if not turns_to_summarize:
            return messages

        # Degenerate re-compression guard: with a threshold below the compacted
        # view's intrinsic size (head + summary + tail floor), every iteration
        # would re-compress a middle holding nothing but the previous summary
        # — an aux call + a rewritten summary every turn, zero new information
        # (observed live: "Compressing 1 turns" every 20s, rows N -> N).
        # Real material = anything that is not the synthetic summary row; a
        # summary-only middle has nothing to fold in, so skip.
        real = [m for m in turns_to_summarize if not m.get("_compaction")]
        if not real:
            return messages

        logger.info(
            "Compressing: summarizing turns %d-%d (%d turns), "
            "protecting %d head + %d tail",
            head_end, tail_start, len(turns_to_summarize),
            head_end, len(messages) - tail_start,
        )

        summary = self._generate_summary(turns_to_summarize, session_key)

        if not summary:
            summary = (
                f"{len(turns_to_summarize)} turns were removed to free context. "
                "Continue based on recent messages and current state."
            )
        else:
            summary = f"{SUMMARY_PREFIX}{summary}"

        # Pick a role that avoids consecutive same-role
        last_head_role = messages[head_end - 1].get("role", "user") if head_end > 0 else "user"
        first_tail_role = messages[tail_start].get("role", "user") if tail_start < len(messages) else "user"
        if last_head_role in ("assistant", "tool"):
            summary_role = "user"
        else:
            summary_role = "assistant"
        if summary_role == first_tail_role:
            summary_role = "assistant" if summary_role == "user" else "user"

        summary_msg = {"role": summary_role,
                       "content": summary, "_compaction": True}
        compressed = self._sanitize_tool_pairs(
            list(messages[:head_end]) + [summary_msg] + list(messages[tail_start:]))

        # Close the loop: if the compacted view STILL trips the threshold the
        # tail/estimation math is wrong (or the system prompt alone is over
        # budget) — say so instead of silently re-compacting next iteration.
        compacted_tokens = self._estimate_tokens(compressed)
        if compacted_tokens >= self.threshold_tokens:
            logger.warning(
                "Compression did not get under threshold: %d tokens remain "
                "(threshold %d) — check tail budget / context_length config",
                compacted_tokens, self.threshold_tokens)

        # Persist as a compaction record keyed by message ids. The synthetic
        # summary is never a messages row; load_context_messages rebuilds the
        # same view from raw rows + this record.
        if self._db is not None and session_id:
            head_id = messages[head_end - 1].get("_id")
            tail_id = messages[tail_start].get("_id")
            if head_id and tail_id:
                self._db.add_compaction(session_id, head_id, tail_id,
                                        summary, summary_role)
            else:
                logger.warning("compress: rows lack _id — compaction not persisted")
        self.compression_count += 1

        logger.info("Compressed: %d -> %d messages", len(messages), len(compressed))
        return compressed

    # Same cut as the old per-char ord() > 0x2E80 loop, counted by the regex
    # engine — this runs over every message on every estimate call, and the
    # Python loop dominated at long-transcript sizes.
    _CJK_RE = re.compile(r"[⺁-\U0010FFFF]+")

    @classmethod
    def _text_tokens(cls, text: str) -> int:
        """CJK chars tokenize ~1/char on GLM-class models while ASCII runs
        ~4/char; a flat chars/4 undercounts Chinese-heavy prompts 2-3x, which
        let turns blow past the compression threshold."""
        cjk = sum(len(m) for m in cls._CJK_RE.findall(text))
        return cjk + (len(text) - cjk) // CHARS_PER_TOKEN

    @classmethod
    def _estimate_tokens(cls, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content") or ""
            total += cls._text_tokens(content) + 10
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    total += cls._text_tokens(args)
        return total

    @staticmethod
    def _prune_slice(messages: list[dict], protect_from: int) -> list[dict]:
        """Replace tool results beyond ``protect_from`` (>200 chars) with a
        placeholder. Shared by the in-compress prune and the per-request
        lightweight prune — one placeholder policy, two call sites."""
        result = [m.copy() for m in messages]
        for i in range(protect_from):
            msg = result[i]
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if content and len(content) > 200 and content != PRUNED_PLACEHOLDER \
                    and "<persisted-output>" not in content:
                result[i] = {**msg, "content": PRUNED_PLACEHOLDER}
        return result

    @classmethod
    def lightweight_prune(cls, messages: list[dict]) -> list[dict]:
        """API-copy prune applied every request: protect the last 20 tool
        results, older >200-char outputs become placeholders. Never touches
        the DB; the next request re-derives its copy from raw rows.
        Persisted-output stubs are left alone — the model can still read_file
        the spill path inside them."""
        protect_from = max(0, len(messages) - 20)
        return cls._prune_slice(messages, protect_from)

    def _find_tail_start(self, messages: list[dict], head_end: int) -> int:
        """Walk backward to find the tail boundary — the recent-context floor
        the compacted view keeps. Derived from the trigger line: the compacted
        view is head + summary + tail, so the tail must sit well below the
        threshold or compression can never reach it (view keeps tripping
        _should_compress forever). Tail takes 40% of the trigger line, floored
        at 3K so tiny configs still keep a usable window.

        Uses the same CJK-aware estimate as _estimate_tokens — the flat
        chars/4 cut let CJK-heavy tails run 2-3x over budget and re-trigger
        compression on the very next iteration.
        """
        trigger = self.threshold_tokens
        tail_budget = max(int(trigger * 0.4), min(3000, trigger))
        n = len(messages)
        accumulated = 0
        cut_idx = n

        for i in range(n - 1, head_end, -1):
            content = messages[i].get("content") or ""
            msg_tokens = self._text_tokens(content) + 10
            for tc in messages[i].get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    msg_tokens += self._text_tokens(args)
            if accumulated + msg_tokens > tail_budget:
                break
            accumulated += msg_tokens
            cut_idx = i

        # Ensure at least 3 tail messages
        cut_idx = min(cut_idx, n - 3)
        return max(cut_idx, head_end + 1)

    @staticmethod
    def _align_head(messages: list[dict], hard_cap: int = 8) -> int:
        """Protect system + the first SEMANTICALLY COMPLETE exchange: from the
        first user message through its assistant response and every paired
        tool result. The old fixed cut (3 rows) could slice mid-exchange and
        orphan the first turn's tool calls. Returns the head boundary index.
        Falls back to 3 when the list does not open with a user message
        (unexpected shape)."""
        if len(messages) < 2 or messages[0].get("role") != "system":
            return min(3, len(messages))
        if messages[1].get("role") != "user":
            return min(3, len(messages))

        idx = 2
        answered: set[str] = set()
        while idx < len(messages):
            msg = messages[idx]
            role = msg.get("role")
            if role == "tool":
                answered.add(msg.get("tool_call_id", ""))
                idx += 1
                continue
            if role == "assistant":
                pending = [
                    tc.get("id") for tc in msg.get("tool_calls") or []
                    if isinstance(tc, dict) and tc.get("id") not in answered
                ]
                if pending:
                    # This assistant called tools; keep walking so the pair
                    # stays inside the head.
                    idx += 1
                    continue
                # Plain assistant reply closes the first exchange.
                return min(idx + 1, hard_cap)
            break
        return min(idx, hard_cap)

    def _generate_summary(self, turns: list[dict],
                          session_key: Optional[str] = None) -> Optional[str]:
        """Use auxiliary client to generate structured summary of middle turns.

        The previous summary comes from the compactions table (survives
        restarts, scoped per session) rather than an in-memory cache.
        """
        if not self._aux:
            return None

        content = self._serialize_turns(turns)
        previous = self._previous_summary(session_key)
        if previous:
            prompt = UPDATE_SUMMARY_PROMPT.format(previous=previous, content=content)
        else:
            prompt = SUMMARY_PROMPT.format(content=content)

        try:
            response = self._aux.call_llm(
                task="compression",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
            )
            if not response:
                return None
            from core.agent.auxiliary_client import extract_content_or_reasoning
            return extract_content_or_reasoning(response)
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            return None

    def _previous_summary(self, session_key: Optional[str]) -> Optional[str]:
        if self._db is None or not session_key:
            return None
        try:
            entry = self._db.get_entry(session_key)
            if not entry:
                return None
            comp = self._db.latest_compaction(entry.session_id)
            return comp["summary"] if comp else None
        except Exception:
            logger.debug("previous summary lookup failed", exc_info=True)
            return None

    @staticmethod
    def _shrink_big_string(value: str, keep: int = 300) -> str:
        if len(value) <= keep:
            return value
        return f"{value[:keep]}\n...[{len(value)} chars truncated]..."

    @classmethod
    def _compact_arguments(cls, args_str: str) -> str:
        """Shrink a tool-call's arguments for the summary material, size-driven
        and tool-agnostic: small fields (paths, names — the key facts) survive
        whole, big string blobs (file contents, commands) keep a head glance so
        the summary can NAME the change; exact content lives on disk."""
        try:
            obj = json.loads(args_str)
        except (json.JSONDecodeError, TypeError):
            return cls._head_tail(args_str, 300, 300)
        if isinstance(obj, dict):
            obj = {k: cls._shrink_big_string(v) if isinstance(v, str) else v
                   for k, v in obj.items()}
            return json.dumps(obj, ensure_ascii=False)
        return cls._shrink_big_string(args_str)

    @staticmethod
    def _head_tail(text: str, head: int, tail: int) -> str:
        if len(text) <= head + tail:
            return text
        return (f"{text[:head]}\n...[{len(text)} chars truncated]...\n"
                f"{text[-tail:]}")

    @classmethod
    def _serialize_turns(cls, turns: list[dict]) -> str:
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if role == "tool":
                if "<persisted-output>" in content:
                    # The stub already IS the compact form (preview + spill
                    # path) — pass it through whole.
                    parts.append(f"[TOOL RESULT]: {content}")
                else:
                    parts.append(f"[TOOL RESULT]: {cls._head_tail(content, 2500, 1000)}")
                continue
            if role == "assistant":
                tc_str = ""
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args = cls._compact_arguments(fn.get("arguments", ""))
                        tc_str += f"\n  {name}({args})"
                parts.append(f"[ASSISTANT]: {content[:2000]}{tc_str}")
            else:
                parts.append(f"[{role.upper()}]: {content[:4000]}")
        return "\n\n".join(parts)

    @staticmethod
    def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
        """Fix orphaned tool_call / tool_result pairs after compression."""
        surviving_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        cid = tc.get("id", "")
                        if cid:
                            surviving_ids.add(cid)

        result_ids = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_ids.add(cid)

        orphaned = result_ids - surviving_ids
        if orphaned:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned)
            ]

        missing = surviving_ids - result_ids
        if missing:
            patched = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        cid = tc.get("id") if isinstance(tc, dict) else ""
                        if cid in missing:
                            patched.append({
                                "role": "tool",
                                "content": "[Result from earlier conversation — see summary above]",
                                "tool_call_id": cid,
                            })
            messages = patched

        return messages
