"""Provider-agnostic LLM tool-calling layer.

A single :func:`call_tool` helper forces a model to return a structured object
via a tool/function call and gives back the validated input dict. It backs every
LLM stage in the system (invoice extraction, the decision engine, and policy
compilation) so provider selection lives in exactly one place.

Two backends cover the providers we support:

* ``claude`` — Anthropic SDK (``messages.create`` with forced ``tool_choice``).
* ``openai`` — the OpenAI client (function calling); ``base_url`` may point at
  any OpenAI-compatible endpoint.

Content is passed in a neutral form (text + base64 image parts) and translated
per backend, so the same call shape works for vision (extraction) and text-only
(decision / compilation). LLM is mandatory: a missing key raises
:class:`LLMUnavailable` rather than silently degrading.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from ap_invoice.core.config import get_settings
from ap_invoice.core.logging import get_logger

logger = get_logger(__name__)

Provider = Literal["claude", "openai", "gemini"]

# A neutral content part is one of:
#   {"type": "text", "text": "..."}
#   {"type": "image", "media_type": "image/png", "data": "<base64>"}
ContentPart = dict[str, Any]


class LLMUnavailable(RuntimeError):
    """Raised when the selected provider cannot run (no key/endpoint, API error)."""


def _resolve(provider: Provider) -> tuple[str, str | None, str, str]:
    """Return (api_key, base_url, default_model, backend) for a provider.

    ``backend`` is "anthropic" or "openai" (the SDK to use).
    """
    s = get_settings()
    if provider == "claude":
        if not s.anthropic_api_key:
            raise LLMUnavailable("No Anthropic API key configured (AP_ANTHROPIC_API_KEY).")
        return s.anthropic_api_key, None, s.claude_model, "anthropic"
    if provider == "gemini":
        if not s.gemini_api_key:
            raise LLMUnavailable("No Gemini API key configured (AP_GEMINI_API_KEY).")
        return (
            s.gemini_api_key,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            s.gemini_model,
            "openai",
        )
    # openai
    if not s.openai_api_key:
        raise LLMUnavailable("No OpenAI API key configured (AP_OPENAI_API_KEY).")
    return s.openai_api_key, s.openai_base_url, s.openai_model, "openai"


async def call_tool(
    *,
    provider: Provider,
    system: str,
    content: list[ContentPart],
    tool_name: str,
    tool_description: str,
    tool_schema: dict[str, Any],
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Force ``provider`` to return ``tool_schema``-shaped JSON and return it as a dict."""
    api_key, base_url, default_model, backend = _resolve(provider)
    settings = get_settings()
    chosen_model = model or default_model
    tokens = max_tokens or settings.extractor_max_tokens

    if provider == "gemini":
        tool_schema = _clean_schema_for_gemini(tool_schema)

    if backend == "anthropic":
        return await _call_anthropic(
            api_key=api_key,
            model=chosen_model,
            system=system,
            content=content,
            tool_name=tool_name,
            tool_description=tool_description,
            tool_schema=tool_schema,
            max_tokens=tokens,
            request_timeout=settings.extractor_timeout_seconds,
        )
    return await _call_openai(
        api_key=api_key,
        base_url=base_url,
        model=chosen_model,
        system=system,
        content=content,
        tool_name=tool_name,
        tool_description=tool_description,
        tool_schema=tool_schema,
        max_tokens=tokens,
        request_timeout=settings.extractor_timeout_seconds,
        use_required_tool_choice=(provider == "gemini"),
    )


# --------------------------------------------------------------------------- #
# Anthropic backend
# --------------------------------------------------------------------------- #


def _anthropic_content(content: list[ContentPart]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in content:
        if part["type"] == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image":
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part["media_type"],
                        "data": part["data"],
                    },
                }
            )
    return blocks


async def _call_anthropic(
    *,
    api_key: str,
    model: str,
    system: str,
    content: list[ContentPart],
    tool_name: str,
    tool_description: str,
    tool_schema: dict[str, Any],
    max_tokens: int,
    request_timeout: float,
) -> dict[str, Any]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=request_timeout)
    tool = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": tool_schema,
    }
    try:
        response = await client.messages.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": _anthropic_content(content)}],
        )
    except anthropic.APIError as exc:
        logger.warning("llm_call_failed", backend="anthropic", model=model, error=str(exc))
        raise LLMUnavailable(str(exc)) from exc
    finally:
        await client.close()

    tool_input = next((block.input for block in response.content if block.type == "tool_use"), None)
    if tool_input is None:
        raise LLMUnavailable("Model returned no tool output.")
    return dict(tool_input)


# --------------------------------------------------------------------------- #
# OpenAI-compatible backend (OpenAI GPT / Gemini / any compatible endpoint)
# --------------------------------------------------------------------------- #


def _openai_content(content: list[ContentPart]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for part in content:
        if part["type"] == "text":
            parts.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image":
            url = f"data:{part['media_type']};base64,{part['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


async def _call_openai(
    *,
    api_key: str,
    base_url: str | None,
    model: str,
    system: str,
    content: list[ContentPart],
    tool_name: str,
    tool_description: str,
    tool_schema: dict[str, Any],
    max_tokens: int,
    request_timeout: float,
    use_required_tool_choice: bool = False,
) -> dict[str, Any]:
    import openai

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=request_timeout)
    tool = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_description,
            "parameters": tool_schema,
        },
    }
    tool_choice = (
        "required"
        if use_required_tool_choice
        else {"type": "function", "function": {"name": tool_name}}
    )
    try:
        response = await client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _openai_content(content)},
            ],
            tools=[tool],
            tool_choice=tool_choice,
        )
    except openai.OpenAIError as exc:
        logger.warning("llm_call_failed", backend="openai", model=model, error=str(exc))
        raise LLMUnavailable(str(exc)) from exc
    finally:
        await client.close()

    choices = response.choices
    tool_calls = choices[0].message.tool_calls if choices else None
    if not tool_calls:
        raise LLMUnavailable("Model returned no tool call.")
    raw_args = tool_calls[0].function.arguments
    try:
        parsed: dict[str, Any] = json.loads(raw_args)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMUnavailable(f"Invalid tool-call JSON: {exc}") from exc
    return parsed


def _clean_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve references and simplify Pydantic schema features that Gemini does not support."""
    import copy

    # 1. Dereference $defs and $ref
    schema_copy = copy.deepcopy(schema)
    defs = schema_copy.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            # Strip 'default' and 'title' keywords which Gemini's validation layer rejects
            node_clean = {k: v for k, v in node.items() if k not in ("default", "title")}
            if "$ref" in node_clean:
                ref_path = node_clean["$ref"]
                ref_name = ref_path.split("/")[-1]
                if ref_name in defs:
                    resolved = resolve(defs[ref_name])
                    node_copy = {k: v for k, v in node_clean.items() if k != "$ref"}
                    return {**resolved, **node_copy}
            return {k: resolve(v) for k, v in node_clean.items()}
        elif isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    flat_schema = resolve(schema_copy)

    # 2. Simplify anyOf nullable blocks for Gemini compatibility
    def simplify_anyof(node: Any) -> Any:
        if isinstance(node, dict):
            if "anyOf" in node:
                anyof_list = node["anyOf"]
                non_null_type = None
                for option in anyof_list:
                    if isinstance(option, dict) and option.get("type") != "null":
                        non_null_type = option
                        break
                if non_null_type:
                    node_copy = {k: v for k, v in node.items() if k != "anyOf"}
                    resolved_non_null = simplify_anyof(non_null_type)
                    return {**resolved_non_null, **node_copy, "nullable": True}
            return {k: simplify_anyof(v) for k, v in node.items()}
        elif isinstance(node, list):
            return [simplify_anyof(item) for item in node]
        return node

    cleaned: dict[str, Any] = simplify_anyof(flat_schema)
    return cleaned
