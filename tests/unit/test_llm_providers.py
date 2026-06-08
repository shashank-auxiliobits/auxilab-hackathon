"""Unit tests for the provider-agnostic LLM layer (content shaping + resolution)."""

from __future__ import annotations

import pytest

from ap_invoice.services.llm.providers import (
    LLMUnavailable,
    _anthropic_content,
    _openai_content,
    _resolve,
)

CONTENT = [
    {"type": "text", "text": "hello"},
    {"type": "image", "media_type": "image/png", "data": "QUJD"},
]


def test_anthropic_content_shaping() -> None:
    blocks = _anthropic_content(CONTENT)
    assert blocks[0] == {"type": "text", "text": "hello"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["data"] == "QUJD"


def test_openai_content_shaping() -> None:
    parts = _openai_content(CONTENT)
    assert parts[0] == {"type": "text", "text": "hello"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_resolve_claude_uses_anthropic_backend() -> None:
    # conftest sets a dummy AP_ANTHROPIC_API_KEY.
    _key, base_url, _model, backend = _resolve("claude")
    assert backend == "anthropic"
    assert base_url is None


def test_resolve_openai_without_key_raises() -> None:
    # No AP_OPENAI_API_KEY is set in the test env.
    with pytest.raises(LLMUnavailable):
        _resolve("openai")
