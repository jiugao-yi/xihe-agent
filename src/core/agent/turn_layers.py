"""Turn-scoped prompt layers — text injected into the system message for ONE
turn only, at the API boundary (never persisted into history).

Two residents today:
- ``PLAN_MODE_LAYER``: the harness plan-mode gate (read-only roster). The
  model ends the turn by submitting the plan through ONE clarify call — the
  user's click on 批准执行 resolves it, and the agent records approval
  WITHOUT any text parsing (``XiheAgent.plan_approved``). The reply text IS
  the plan; the entrypoint persists it (``save_plan``) and launches the
  execution turn (``execution_message``).
- ``REGENERATE_HINT``: injected when the user rolled back a previous answer
  and re-asks — makes the model answer independently instead of deferring
  to its earlier reply.

Agent-side plumbing lives in ``XiheAgent``: ``chat(plan_mode=...)`` /
``chat(turn_layer=...)`` set ``self._turn_layer``, and
``_apply_api_transforms`` prepends it to the system message on every API
call of the turn (re-applied after mid-turn compression). Adding a new
hint = define its text here, pass it via ``turn_layer`` at an entrypoint.
"""

import re
import time
from pathlib import Path

PLAN_SUBMIT_OPTIONS = ["批准执行", "放弃"]
PLAN_APPROVED_ANSWER = "批准执行"

# Turn-scoped hint for regenerate turns (never persisted): the user rolled
# back a previous answer to this same prompt and is re-asking. Without it the
# model reads the surrounding history, recognizes the topic, and answers
# "I just answered this" instead of producing a fresh attempt.
REGENERATE_HINT = (
    "> Note: the user rolled back their previous exchange on this topic and "
    "is re-asking it. Ignore any earlier answer/reasoning about this request "
    "that appears in the history — produce a fresh, independent attempt. Do "
    "not mention that you answered before."
)

PLAN_MODE_LAYER = f"""# Plan Mode

You are in PLAN MODE for this turn. Your tool surface is read-only BY DESIGN —
do not attempt file writes, shell commands, or any mutation. Explore freely
with read_file / search_files / directory_tree, then produce an
implementation plan for the user's request:

- **Goal** — one sentence
- **Approach** — and the main alternative you rejected, with the reason
- **Tasks** — ordered, each independently verifiable: exact file paths, what
  changes, and the command/check that verifies it
- **Risks / open questions** — what could bite during execution

Your reply text IS the plan (the executor does not share your exploration
context — make it self-contained). When the plan is complete, output it as
your final reply and THEN submit it for approval with ONE clarify call:

    clarify(question="计划摘要：<one-line summary + key decisions>",
            options={PLAN_SUBMIT_OPTIONS!r})

- A free-text answer is revision feedback: revise the plan (your final reply
  must be the revised full plan) and submit again.
- The clarify call must be the LAST thing you do this turn. Approval is the
  user's decision — after submitting, stop.
"""


def save_plan(plan_body: str) -> Path:
    """Persist an approved plan under AGENT_HOME/scratch/plans/."""
    from core.config import AGENT_HOME
    d = Path(AGENT_HOME) / "scratch" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w一-鿿-]+", "-", (plan_body.splitlines() or ["plan"])[0]
                  .lstrip("# ").strip()).strip("-")[:40] or "plan"
    p = d / f"{time.strftime('%Y-%m-%d_%H%M%S')}-{slug}.md"
    p.write_text(plan_body + "\n", encoding="utf-8")
    return p


def execution_message(plan_body: str, plan_path: Path | None = None) -> str:
    """The user message that launches the execution turn after approval."""
    loc = f" (saved at {plan_path})" if plan_path else ""
    return (
        "Execute the approved plan below, exactly and completely. Track the "
        "tasks with todo (one item per task, mark as you go), verify each "
        "task per its own verification step, and report faithfully at the "
        f"end — including anything that failed.\n\nApproved plan{loc}:\n\n"
        f"{plan_body}"
    )
