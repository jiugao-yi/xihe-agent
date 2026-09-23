"""AgentTurnRunner — the shared turn orchestration layer for all three
entrypoints (serve / CLI / gateway).

Entrypoints keep only what is genuinely theirs: protocol parsing, turn
serialization, ACK/delivery quirks. Everything SEMANTIC about running a turn
lives here, once:

- agent construction (+ the api_key-missing diagnosis)
- approval-key bucketing (workspace-bound conversations share the
  "approve and don't ask again" memory)
- plan-mode outcome handling: persisting the approved plan, producing the
  execution-turn message
- late-steer policy is a pure decision here too (see LateSteer), while the
  entrypoint decides how to act on it (serve drops, gateway re-queues)

Usage:
    runner = AgentTurnRunner(ctx, params, callbacks)
    result = runner.run()                       # blocks; thread-safe to call
    if runner.plan_outcome:                     # plan turn approved
        plan_path, exec_text = runner.plan_outcome
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from core.agent.turn_layers import (
    REGENERATE_HINT,
    execution_message,
    save_plan,
)
from core.session import SessionSource

if TYPE_CHECKING:
    from core.agent.agent import TurnCallbacks

logger = logging.getLogger(__name__)


@dataclass
class AgentTurnParams:
    """Everything an entrypoint resolves BEFORE asking for a turn. Pure data —
    the entrypoint parses its own protocol into this."""
    source: SessionSource
    text: str
    attachments: list | None = None   # attachment metadata persisted alongside the
                                      # user row; the hint block is API-boundary-
                                      # injected, so `text` stays the literal input
    cwd: str | None = None            # workspace workdir (serve threads it in)
    plan: bool = False                # plan-mode turn (read-only roster)
    regenerate: bool = False          # re-ask after rolling back a previous answer
    approval_key: str | None = None   # overrides the approval-memory bucket


@dataclass
class TurnResult:
    text: str | None = None
    exit_reason: str | None = None
    usage: dict = field(default_factory=dict)
    error: str | None = None          # non-None = the turn failed

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def failed_message(self) -> str:
        """Exit-reason-tagged closing text for non-completed turns (api
        errors / max-iterations return their message in ``text``)."""
        return self.text or ""


@dataclass
class PlanOutcome:
    """An approved plan, persisted and packaged for an execution turn."""
    body: str
    path: Path | None
    exec_text: str                    # send this as the next user message


class AgentTurnRunner:
    """Runs one agent turn with shared orchestration. Construct per turn
    (cheap); ``run()`` blocks — call from the mode's worker thread.

    Agent lifecycle in the constructor: pass ``agent=`` to reuse a long-lived
    instance (CLI); without one the turn's agent is built right here (the
    shared-context factory does the wiring — construction errors surface
    immediately, e.g. a missing api_key). A constructed Runner is always
    ready to run; ``runner.agent`` is readable for interrupt registration.
    """

    def __init__(self, ctx: Any, params: AgentTurnParams,
                 callbacks: "TurnCallbacks",
                 agent: Optional[Any] = None):
        self.ctx = ctx
        self.params = params
        self.callbacks = callbacks
        self.result: TurnResult | None = None
        self.agent = agent if agent is not None else ctx.create_agent(
            enabled_toolsets=ctx.main_toolsets,
            skills_allowed=ctx.main_skills,
            cwd=params.cwd or None)

    def api_key_missing(self) -> bool:
        return not (self.ctx.config or {}).get("api_key")

    # ---- the turn -------------------------------------------------------

    def run(self) -> TurnResult:
        """Execute the turn. Blocks until it settles; every exit path has
        already persisted in-progress messages (XiheAgent.chat contract)."""
        p = self.params
        try:
            final = self.agent.chat(
                source=p.source,
                user_message=p.text,
                attachments=p.attachments,
                callbacks=self.callbacks,
                approval_key=p.approval_key,
                plan_mode=p.plan,
                turn_layer=(REGENERATE_HINT if p.regenerate else None),
            )
            usage = dict(getattr(self.agent, "_turn_usage", {}))
            self.result = TurnResult(
                text=final,
                exit_reason=getattr(self.agent, "_last_exit_reason", None),
                usage=usage)
            return self.result
        except Exception as e:
            logger.exception("turn failed (session=%s)", p.source.chat_id)
            self.result = TurnResult(error=str(e))
            return self.result

    # ---- plan-mode outcome ----------------------------------------------

    @property
    def plan_approved(self) -> bool:
        return bool(self.params.plan and self.agent is not None
                    and getattr(self.agent, "plan_approved", False))

    def plan_outcome(self) -> PlanOutcome | None:
        """Persist the approved plan and package the execution turn. None
        when this turn was not an approved plan turn (the plan text is the
        turn's final reply, captured by run())."""
        if not self.plan_approved:
            return None
        body = (self.result.text or "").strip() if self.result else ""
        if not body:
            return None
        try:
            path = save_plan(body)
        except Exception:
            logger.exception("plan save failed")
            path = None
        return PlanOutcome(body=body, path=path,
                           exec_text=execution_message(body, path))
