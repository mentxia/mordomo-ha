"""LLM Engine for Mordomo HA - Multi-provider support."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from .const import (
    LLM_ANTHROPIC,
    LLM_CUSTOM,
    LLM_DEEPSEEK,
    LLM_OLLAMA,
    LLM_OPENAI,
)

_LOGGER = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    def __init__(self, api_key: str, model: str, api_url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self._conversation_history: dict[str, list[dict]] = {}

    def get_history(self, phone: str) -> list[dict]:
        """Get conversation history for a phone number."""
        if phone not in self._conversation_history:
            self._conversation_history[phone] = []
        return self._conversation_history[phone]

    def add_to_history(self, phone: str, role: str, content: str):
        """Add message to conversation history."""
        history = self.get_history(phone)
        history.append({"role": role, "content": content})
        # Keep last 20 messages to manage context window
        if len(history) > 20:
            self._conversation_history[phone] = history[-20:]

    def clear_history(self, phone: str):
        """Clear conversation history for a phone number."""
        self._conversation_history[phone] = []

    @abstractmethod
    async def chat(
        self,
        message: str,
        system_prompt: str,
        phone: str,
        ha_context: str = "",
    ) -> str:
        """Send a message and get a response."""


class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible provider (works with OpenAI, DeepSeek, Custom)."""

    def __init__(self, api_key: str, model: str, api_url: str | None = None):
        super().__init__(api_key, model, api_url)
        if not self.api_url:
            self.api_url = "https://api.openai.com/v1"

    async def chat(
        self,
        message: str,
        system_prompt: str,
        phone: str,
        ha_context: str = "",
    ) -> str:
        """Send message via OpenAI-compatible API."""
        self.add_to_history(phone, "user", message)

        full_system = system_prompt
        if ha_context:
            full_system += f"\n\n## Estado atual da casa:\n{ha_context}"

        messages = [{"role": "system", "content": full_system}]
        messages.extend(self.get_history(phone))

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        _LOGGER.error("LLM API error %s: %s", resp.status, error_text)
                        return f"Desculpa, tive um erro ao processar: {resp.status}"

                    data = await resp.json()
                    response = data["choices"][0]["message"]["content"]
                    self.add_to_history(phone, "assistant", response)
                    return response

        except Exception as err:
            _LOGGER.error("LLM request failed: %s", err)
            return "Desculpa, não consegui processar o teu pedido de momento."


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider."""

    def __init__(self, api_key: str, model: str, api_url: str | None = None):
        super().__init__(api_key, model, api_url)
        self.api_url = api_url or "https://api.anthropic.com/v1"

    async def chat(
        self,
        message: str,
        system_prompt: str,
        phone: str,
        ha_context: str = "",
    ) -> str:
        """Send message via Anthropic API."""
        self.add_to_history(phone, "user", message)

        full_system = system_prompt
        if ha_context:
            full_system += f"\n\n## Estado atual da casa:\n{ha_context}"

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": self.model,
            "system": full_system,
            "messages": self.get_history(phone),
            "max_tokens": 2000,
            "temperature": 0.7,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/messages",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        _LOGGER.error("Anthropic API error %s: %s", resp.status, error_text)
                        return f"Desculpa, tive um erro ao processar: {resp.status}"

                    data = await resp.json()
                    response = data["content"][0]["text"]
                    self.add_to_history(phone, "assistant", response)
                    return response

        except Exception as err:
            _LOGGER.error("Anthropic request failed: %s", err)
            return "Desculpa, não consegui processar o teu pedido de momento."


class OllamaProvider(BaseLLMProvider):
    """Ollama local provider."""

    def __init__(self, api_key: str, model: str, api_url: str | None = None):
        super().__init__(api_key, model, api_url)
        self.api_url = api_url or "http://localhost:11434"

    async def chat(
        self,
        message: str,
        system_prompt: str,
        phone: str,
        ha_context: str = "",
    ) -> str:
        """Send message via Ollama API."""
        self.add_to_history(phone, "user", message)

        full_system = system_prompt
        if ha_context:
            full_system += f"\n\n## Estado atual da casa:\n{ha_context}"

        messages = [{"role": "system", "content": full_system}]
        messages.extend(self.get_history(phone))

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        _LOGGER.error("Ollama API error %s: %s", resp.status, error_text)
                        return f"Desculpa, tive um erro ao processar: {resp.status}"

                    data = await resp.json()
                    response = data["message"]["content"]
                    self.add_to_history(phone, "assistant", response)
                    return response

        except Exception as err:
            _LOGGER.error("Ollama request failed: %s", err)
            return "Desculpa, não consegui processar o teu pedido de momento."


def create_llm_provider(
    provider: str, api_key: str, model: str, api_url: str | None = None
) -> BaseLLMProvider:
    """Factory to create the appropriate LLM provider."""
    if provider == LLM_OPENAI:
        return OpenAIProvider(api_key, model)
    elif provider == LLM_ANTHROPIC:
        return AnthropicProvider(api_key, model)
    elif provider == LLM_DEEPSEEK:
        return OpenAIProvider(api_key, model, "https://api.deepseek.com/v1")
    elif provider == LLM_OLLAMA:
        return OllamaProvider("", model, api_url or "http://localhost:11434")
    elif provider == LLM_CUSTOM:
        return OpenAIProvider(api_key, model, api_url)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
