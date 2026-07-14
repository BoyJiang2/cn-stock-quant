"""Provider-neutral, opt-in LLM text streaming adapters.

This module deliberately has no application configuration dependency.  The
caller must provide an explicit configuration for every remote request, which
keeps advisory data from being sent to a model provider by accident.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol


class LLMProviderConfigurationError(RuntimeError):
    """Raised before a remote LLM request when required configuration is absent."""


class TextStreamingProvider(Protocol):
    """Minimal interface consumed by the advisory service."""

    def stream_text(self, *, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Yield text deltas in order from one model response."""


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    """Explicit configuration required for a remote Responses API call."""

    api_key: str | None
    model: str | None
    remote_enabled: bool = False


class OpenAIResponsesProvider:
    """Stream text deltas from the OpenAI Responses API.

    The OpenAI SDK import is intentionally lazy so local-only deployments can
    start without the optional package installed.  ``client_factory`` exists
    for tests and must return an object exposing ``responses.create``.
    """

    def __init__(
        self,
        config: OpenAIResponsesConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory

    def stream_text(self, *, system_prompt: str, user_prompt: str) -> Iterator[str]:
        self.validate_configuration()
        client = self._build_client()
        events = client.responses.create(
            model=self._config.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        for event in events:
            if self._event_type(event) != "response.output_text.delta":
                continue
            delta = self._event_value(event, "delta")
            if isinstance(delta, str) and delta:
                yield delta

    def validate_configuration(self) -> None:
        if not self._config.remote_enabled:
            raise LLMProviderConfigurationError(
                "Remote LLM use is disabled. Set remote_enabled=True only after "
                "reviewing the data-sharing boundary."
            )
        if not self._config.api_key:
            raise LLMProviderConfigurationError("OPENAI_API_KEY is required for remote LLM use.")
        if not self._config.model:
            raise LLMProviderConfigurationError("An OpenAI Responses model must be configured.")
        if self._client_factory is None:
            try:
                import openai  # noqa: F401
            except ImportError as exc:
                raise LLMProviderConfigurationError(
                    "The optional 'openai' package is required for OpenAI Responses streaming."
                ) from exc

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(api_key=self._config.api_key)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMProviderConfigurationError(
                "The optional 'openai' package is required for OpenAI Responses streaming."
            ) from exc
        return OpenAI(api_key=self._config.api_key)

    @staticmethod
    def _event_type(event: Any) -> str | None:
        return event.get("type") if isinstance(event, dict) else getattr(event, "type", None)

    @staticmethod
    def _event_value(event: Any, key: str) -> Any:
        return event.get(key) if isinstance(event, dict) else getattr(event, key, None)
