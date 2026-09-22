"""Mid-turn user interaction — the wait skeleton shared by business flows.

Approvals and clarify both block a tool thread on a human answer mid-turn.
Their business (verdict semantics, memory rules, answer shaping) lives in
their own layers — approvals.py / clarify_tool.py plus the per-flow glue in
XiheAgent. This module owns only the wait: one pending question per agent,
event + interrupt polling + deadline, resolved by any inbound channel.
"""

import logging
import threading
import time
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MidturnQuestion:
    """One pending question. ``status`` ends as one of:
    answered / timeout / interrupted / busy / no_callback / callback_error."""

    __slots__ = ("id", "kind", "payload", "answer", "status", "reason", "event")

    def __init__(self, kind: str, payload: dict) -> None:
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.payload = payload
        self.answer: object | None = None
        self.status = "pending"
        self.reason = ""
        self.event = threading.Event()


class MidturnHub:
    """Per-agent hub holding THE single pending mid-turn question. Mid-turn
    asks are mutually exclusive by construction (dispatch serializes batches),
    so one slot is both enough and a guard against pathological models."""

    def __init__(self, is_interrupted: Callable[[], bool]) -> None:
        self._is_interrupted = is_interrupted
        self._lock = threading.Lock()
        self._pending: Optional[MidturnQuestion] = None

    @property
    def pending(self) -> Optional[dict]:
        """Shallow view for routing layers: {id, kind, **payload} or None."""
        with self._lock:
            q = self._pending
            if q is None:
                return None
            return {"id": q.id, "kind": q.kind, **q.payload}

    def request(self, kind: str, payload: dict,
                on_request: Optional[Callable[[dict], None]],
                timeout: float) -> MidturnQuestion:
        """Block the calling tool thread until the question resolves.

        ``on_request`` delivers the ask to the user's channel ({**payload, id});
        None (headless cron / unwired mode) or a raise resolves immediately
        with no_callback / callback_error — the business layer decides what
        that means (approvals deny, clarify goes unanswered).
        """
        q = MidturnQuestion(kind, payload)
        with self._lock:
            if self._pending is not None:
                q.status, q.reason = "busy", "已有另一个待处理问题"
                return q
            self._pending = q

        if on_request is None:
            q.status, q.reason = "no_callback", "无人值守环境（无用户通道）"
            self._release(q)
            return q
        try:
            on_request({**payload, "id": q.id})
        except Exception:
            logger.warning("midturn %s request callback failed", kind, exc_info=True)
            q.status, q.reason = "callback_error", "问题通知发送失败"
            self._release(q)
            return q

        deadline = time.monotonic() + timeout
        while True:
            if q.event.wait(timeout=0.3):
                q.status = "answered"
                break
            if self._is_interrupted():
                q.status, q.reason = "interrupted", "被停止指令打断"
                break
            if time.monotonic() >= deadline:
                q.status, q.reason = "timeout", "超时未回答"
                break
        self._release(q)
        return q

    def resolve(self, question_id: Optional[str] = None,
                answer: Optional[object] = None) -> bool:
        """Deliver the user's answer (serve frame / gateway inbound / CLI
        input). Empty id matches the single pending. Returns True when the
        answer reached a still-waiting question."""
        with self._lock:
            q = self._pending
            if q is None or (question_id and q.id != question_id):
                return False
            if q.event.is_set():
                return False
            q.answer, q.reason = answer, ""
            q.event.set()
            return True

    def _release(self, q: MidturnQuestion) -> None:
        with self._lock:
            if self._pending is q:
                self._pending = None
