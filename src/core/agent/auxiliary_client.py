"""Auxiliary LLM client — stateless single-shot completions for tool use.

Why a separate layer instead of calling agent.chat()?

1. No tool loop — auxiliary calls are pure completions, never trigger tool calls
   (avoids recursive agent→tool→agent→tool loops)
2. No state pollution — results don't write to session history
3. Model flexibility — different tasks can use different models/providers
4. No system prompt overhead — no full agent prompt + tool schemas injected
5. Independent timeouts — per-task timeout instead of agent global timeout

Config (config.yaml):
    auxiliary:
      compression:
        model: glm-4-flash        # cheaper model for compression
      vision:
        model: gpt-4o             # vision-capable model
      title:
        model: glm-4-flash        # cheap model for titles
        timeout: 10
"""

import logging
import os
import re
import base64
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = {
    "compression": "",   # fall back to main model
    "vision": "",       # needs vision-capable model
    "title": "",        # cheap model is fine
    "image_gen": "dall-e-3",
    "tts": "tts-1",
}

_DEFAULT_TIMEOUTS = {
    "compression": 30,
    "vision": 60,
    "title": 10,
    "approval_judge": 10,
    "image_gen": 60,
    "tts": 30,
}


def extract_content_or_reasoning(response: Any) -> str:
    """Extract content from an LLM response, falling back to reasoning fields.

    Reasoning models (DeepSeek-R1, Qwen-QwQ, etc.) may return content=None
    with reasoning in structured fields.

    Resolution order:
      1. message.content — strip inline think/reasoning blocks
      2. message.reasoning / message.reasoning_content
      3. message.reasoning_details — array format (OpenRouter)
    """
    msg = response.choices[0].message
    content = (msg.content or "").strip()

    if content:
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning|REASONING_SCRATCHPAD)>"
            r".*?"
            r"</(?:think|thinking|reasoning|REASONING_SCRATCHPAD)>",
            "", content, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if cleaned:
            return cleaned

    reasoning_parts: list[str] = []
    for field in ("reasoning", "reasoning_content"):
        val = getattr(msg, field, None)
        if val and isinstance(val, str) and val.strip() and val not in reasoning_parts:
            reasoning_parts.append(val.strip())

    details = getattr(msg, "reasoning_details", None)
    if details and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                summary = (
                    detail.get("summary")
                    or detail.get("content")
                    or detail.get("text")
                )
                if summary and summary not in reasoning_parts:
                    reasoning_parts.append(summary.strip() if isinstance(summary, str) else str(summary))

    if reasoning_parts:
        return "\n\n".join(reasoning_parts)

    return ""


# ---------------------------------------------------------------------------
# describe_image — 工具内部基础设施（非工具面）
# ---------------------------------------------------------------------------

# 入站附件自动描述的降级文案：只陈述事实，不点名任何工具——用哪个图片
# 工具（image_ocr 等）是 agent 按自己 roster 的路由决策，实现层不替它做。
_DESCRIBE_UNAVAILABLE = (
    "（图片未自动识别，路径: {path}。请按需用你可用的图片工具处理）")


def describe_image(aux: "AuxiliaryClient", image: str,
                   prompt: Optional[str] = None) -> str:
    """调辅助 LLM 看图，返回描述文本；失败/未配置时返回中性降级文案。

    给工具内部与入口层的附件自动描述用（gateway 企微/飞书入站图、serve
    桌面上传、computer_screenshot 的 describe 参数）。住在 core 层而非
    vision_tools：这是 AuxiliaryClient 之上的基础设施，任何工具 import
    它都不构成 tool↔tool 耦合（规则见 CLAUDE.md）。

    接受本地路径或 http(s) URL。Never raises。
    """
    if aux is None or not aux.is_available("vision"):
        return _DESCRIBE_UNAVAILABLE.format(path=image)
    try:
        data_url = _image_to_data_url(image)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text",
                 "text": prompt or "简短描述这张图片的内容，包括文字、物体、场景等关键信息。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }]
        # 视觉模型优先，空内容回落主模型（与 vision_tools 的 fallback 同构）
        response = aux.call_vision(messages=messages, max_tokens=2000)
        content = extract_content_or_reasoning(response) if response else ""
        if not content:
            response = aux.call_llm(task="", messages=messages, max_tokens=2000)
            content = extract_content_or_reasoning(response) if response else ""
        if not content:
            return "(vision analysis returned no response)"
        from core.support.redact import redact_sensitive_text
        return redact_sensitive_text(content)
    except Exception as e:
        return f"(vision error: {e})"


_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _image_to_data_url(image: str) -> str:
    """Local file path or http(s) URL → base64 data URL (≤10MB)."""
    p = Path(image).expanduser()
    if p.is_file():
        mime = _MIME_BY_EXT.get(p.suffix.lower())
        if not mime:
            raise ValueError(f"Unsupported image format: {p.suffix}")
        data = p.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError(f"Image too large: {len(data)} bytes (max 10MB)")
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    if image.startswith(("http://", "https://")):
        resp = httpx.get(image, timeout=30, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; XiheAgent/1.0)",
            "Accept": "image/*,*/*;q=0.8",
        })
        resp.raise_for_status()
        data = resp.content
        if len(data) > 10 * 1024 * 1024:
            raise ValueError(f"Image too large: {len(data)} bytes (max 10MB)")
        mime = _sniff_image_mime(data) or \
            resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    raise ValueError(
        f"Invalid image source: {image}. Use a file path or HTTP URL.")


def _sniff_image_mime(data: bytes) -> Optional[str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class AuxiliaryClient:
    """Stateless LLM client for auxiliary (non-agent-loop) completions.

    Initialized once with the main model credentials, then each call can
    optionally override model/timeout per task.
    """

    def __init__(self, base_url: str = "", api_key: str = "", model: str = "",
                 config: Optional[dict] = None):
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = model
        self._config = config or {}
        self._client = OpenAI(
            api_key=api_key, base_url=base_url,
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) if base_url and api_key else None

    _TASK_CONFIG_KEYS = {
        "vision": "vision_model",
    }

    def _resolve(self, task: str, model: Optional[str] = None,
                 timeout: Optional[float] = None) -> tuple[str, float]:
        """Resolve effective model and timeout for a task.

        Priority: explicit arg > top-level config key > auxiliary section > default.

        When a top-level config key (e.g. vision_model) is explicitly set to empty
        string, the task is considered **disabled** — no fallback to default model.
        """
        top_key = self._TASK_CONFIG_KEYS.get(task)
        top_val = self._config.get(top_key) if top_key else None

        if top_key and top_key in self._config and top_val == "":
            effective_timeout = timeout or _DEFAULT_TIMEOUTS.get(task, 30)
            return "", effective_timeout

        aux_cfg = self._config.get("auxiliary", {}) or {}
        task_cfg = aux_cfg.get(task, {}) or {}

        effective_model = (
            model
            or (top_val if top_val else None)
            or task_cfg.get("model")
            or self._default_model
        )

        effective_timeout = timeout or task_cfg.get("timeout") or _DEFAULT_TIMEOUTS.get(task, 30)

        return effective_model, effective_timeout

    @property
    def client(self) -> Optional[OpenAI]:
        return self._client

    def _build_call_kwargs(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 4000,
        temperature: Optional[float] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Build kwargs for chat.completions.create()."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    def call_llm(
        self,
        task: str,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """Single-shot LLM completion. Returns the raw response object.

        Args:
            task: Task name for model/timeout resolution (e.g., "compression", "title").
            messages: Chat messages list.
            model: Explicit model override.
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.

        Returns:
            Response object with .choices[0].message.content, or None on failure.
        """
        if not self._client:
            logger.debug("AuxiliaryClient not configured (no base_url/api_key)")
            return None

        effective_model, effective_timeout = self._resolve(task, model, timeout)

        if not effective_model:
            logger.debug("Auxiliary %s: no model resolved (task disabled)", task)
            return None

        logger.info("Auxiliary %s: using %s", task, effective_model)

        kwargs = self._build_call_kwargs(
            effective_model, messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=effective_timeout,
        )

        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as e:
            err_str = str(e)
            # Retry with max_completion_tokens if max_tokens is rejected
            if "max_tokens" in err_str.lower() or "unsupported_parameter" in err_str.lower():
                kwargs.pop("max_tokens", None)
                kwargs["max_completion_tokens"] = max_tokens
                try:
                    return self._client.chat.completions.create(**kwargs)
                except Exception:
                    pass
            logger.warning("Auxiliary call_llm(task=%s, model=%s) failed: %s", task, effective_model, e)
            return None

    def call_vision(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 2000,
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """Vision LLM completion. Same as call_llm but with vision defaults."""
        return self.call_llm(
            task="vision",
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def generate_image(
        self,
        prompt: str,
        model: Optional[str] = None,
        size: str = "1024x1024",
        n: int = 1,
        style: str = "vivid",
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """Generate images via DALL-E style API."""
        if not self._client:
            return None

        effective_model, effective_timeout = self._resolve("image_gen", model, timeout)

        try:
            return self._client.images.generate(  # type: ignore[call-overload]  # 非 OpenAI 网关接受任意 style/size 组合
                model=effective_model or "dall-e-3",
                prompt=prompt,
                size=size,
                n=n,
                style=style,
                timeout=effective_timeout,
            )
        except Exception as e:
            logger.warning("Auxiliary generate_image failed: %s", e)
            return None

    def text_to_speech(
        self,
        text: str,
        voice: str = "alloy",
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """Generate speech via TTS API."""
        if not self._client:
            return None

        effective_model, effective_timeout = self._resolve("tts", model, timeout)

        try:
            return self._client.audio.speech.create(
                model=effective_model or "tts-1",
                voice=voice,
                input=text,
                timeout=effective_timeout,
            )
        except Exception as e:
            logger.warning("Auxiliary text_to_speech failed: %s", e)
            return None

    def is_available(self, task: Optional[str] = None) -> bool:
        """Check if the client is configured and (optionally) a task is ready."""
        if not self._client:
            return False
        if task == "vision":
            model, _ = self._resolve("vision")
            return bool(model)
        if task == "image_gen":
            enabled = ((self._config.get("auxiliary") or {}).get("image_gen") or {}).get("enabled")
            return str(enabled).lower() in ("1", "true", "yes")
        if task == "tts":
            enabled = ((self._config.get("auxiliary") or {}).get("tts") or {}).get("enabled")
            return str(enabled).lower() in ("1", "true", "yes")
        return True
