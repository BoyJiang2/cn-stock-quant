from __future__ import annotations

import pytest

from app.ai_advisory.providers import (
    LLMProviderConfigurationError,
    OpenAIResponsesConfig,
    OpenAIResponsesProvider,
)


class _FakeResponses:
    def __init__(self, events):
        self._events = events
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._events)


class _FakeClient:
    def __init__(self, events):
        self.responses = _FakeResponses(events)


def test_remote_use_is_disabled_by_default():
    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(api_key="test-key", model="test-model")
    )

    with pytest.raises(LLMProviderConfigurationError, match="disabled"):
        provider.validate_configuration()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (OpenAIResponsesConfig(api_key=None, model="test", remote_enabled=True), "OPENAI_API_KEY"),
        (OpenAIResponsesConfig(api_key="key", model=None, remote_enabled=True), "model"),
    ],
)
def test_remote_use_requires_key_and_model(config, message):
    provider = OpenAIResponsesProvider(config)

    with pytest.raises(LLMProviderConfigurationError, match=message):
        list(provider.stream_text(system_prompt="system", user_prompt="user"))


def test_openai_responses_provider_streams_only_text_deltas():
    client = _FakeClient(
        [
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "first "},
            {"type": "response.output_text.delta", "delta": "second"},
            {"type": "response.completed"},
        ]
    )
    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(api_key="test-key", model="test-model", remote_enabled=True),
        client_factory=lambda **_: client,
    )

    assert list(provider.stream_text(system_prompt="system", user_prompt="user")) == ["first ", "second"]
    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "stream": True,
        }
    ]
