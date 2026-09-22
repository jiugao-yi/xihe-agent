"""Clarify tool — ask the user a clarification question and wait for the
answer mid-turn (first-class interaction, same MidturnHub skeleton approvals
use). Falls back to the old tell-the-model-to-ask path when no agent / no
user channel is available (headless cron)."""

import logging
from tools import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_STOP_AND_ASK = (
    "Stop and ask the user this question in your reply. "
    "Do not proceed with other tools until they answer."
)


def _clarify(question: str = "", options: list = None, reason: str = "",
             multi_select: bool = False, **kw) -> str:
    if not isinstance(question, str) or not question.strip():
        return tool_error("question must be a non-empty string")
    question = question.strip()

    options = options or []
    if not isinstance(options, list):
        return tool_error("options must be a list of strings")
    # The schema says strings; coerce anyway — model-provided args can drift.
    options = list(dict.fromkeys(
        str(o).strip() for o in options if str(o).strip()))[:10]

    agent = kw.get("parent_agent")
    if agent is not None and hasattr(agent, "request_clarification"):
        status, answer = agent.request_clarification(
            question, options, multi_select=bool(multi_select))
        if status == "answered":
            logger.info("Clarification answered: %s", (answer or "")[:80])
            return tool_result({
                "action": "clarify", "status": "answered",
                "answer": answer,
                "instruction": "The user answered. Continue with their answer.",
            })
        logger.info("Clarification unanswered (%s)", status)
        return tool_result({
            "action": "clarify", "status": "unanswered", "reason": answer,
            "question": question, "options": options,
            "instruction": _STOP_AND_ASK,
        })

    # Headless (no agent / no user channel): the model is the only reader —
    # without this it may treat the result as data and keep calling tools.
    result = {"action": "clarify", "question": question,
              "instruction": _STOP_AND_ASK}
    if options:
        result["options"] = options
    if reason and isinstance(reason, str):
        result["reason"] = reason.strip()
    return tool_result(result)


def try_resolve_clarify(agent, text: str) -> bool:
    """Route an inbound mid-turn message as the pending clarify answer
    (gateway steer path / CLI mid-turn input). Whole text is the answer —
    option clicks arrive as the option text, free typing as typed."""
    pending = getattr(agent, "pending_clarify", None)
    if not pending or not str(text).strip():
        return False
    return agent.resolve_clarification(None, str(text))


registry.register(
    name="clarify",
    schema={
        "type": "function",
        "function": {
            "name": "clarify",
            "description": (
                "Ask the user a clarification or confirmation question mid-turn. "
                "On messaging channels options render as an interactive card — "
                "never claim cards are unavailable. Set multi_select for "
                "pick-which-items questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The clarification question to ask the user"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Suggested answer options (optional)",
                    },
                    "multi_select": {
                        "type": "boolean",
                        "description": ("Allow multiple options to be selected (answers joined "
                                        "with '；'). Default false — single choice."),
                    },
                    "reason": {"type": "string", "description": "Why clarification is needed (optional)"},
                },
                "required": ["question"],
            },
        },
    },
    handler=lambda args, **kw: _clarify(
        question=args.get("question", ""), options=args.get("options"),
        reason=args.get("reason", ""),
        multi_select=bool(args.get("multi_select")), **kw),
    toolset="communication",
    subagent_blocked=True,
    read_only=True,
)
