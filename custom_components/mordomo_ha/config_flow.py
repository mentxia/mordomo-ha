"""Config flow for Mordomo HA."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ALLOWED_NUMBERS,
    CONF_LLM_API_KEY,
    CONF_LLM_MODEL,
    CONF_LLM_PROVIDER,
    CONF_SYSTEM_PROMPT,
    CONF_WHATSAPP_API_KEY,
    CONF_WHATSAPP_API_URL,
    CONF_WHATSAPP_PHONE_ID,
    DEFAULT_MODELS,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
    LLM_ANTHROPIC,
    LLM_CUSTOM,
    LLM_DEEPSEEK,
    LLM_OLLAMA,
    LLM_OPENAI,
    LLM_PROVIDERS,
)
from .whatsapp import GATEWAY_LABELS, WhatsAppGateway

_LOGGER = logging.getLogger(__name__)

CONF_WHATSAPP_GATEWAY = "whatsapp_gateway"
CONF_OLLAMA_URL = "ollama_url"
CONF_CUSTOM_API_URL = "custom_api_url"


class MordomoHAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mordomo HA."""

    VERSION = 1

    def __init__(self):
        self._data = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Choose LLM provider."""
        errors = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_llm_config()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LLM_PROVIDER, default=LLM_OPENAI): vol.In(
                        LLM_PROVIDERS
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "title": "Mordomo HA - Escolha o LLM",
            },
        )

    async def async_step_llm_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Configure LLM details."""
        errors = {}
        provider = self._data.get(CONF_LLM_PROVIDER, LLM_OPENAI)

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_whatsapp()

        schema_dict = {}

        if provider != LLM_OLLAMA:
            schema_dict[vol.Required(CONF_LLM_API_KEY)] = str

        schema_dict[
            vol.Required(CONF_LLM_MODEL, default=DEFAULT_MODELS.get(provider, ""))
        ] = str

        if provider == LLM_OLLAMA:
            schema_dict[
                vol.Required(CONF_OLLAMA_URL, default="http://localhost:11434")
            ] = str
        elif provider == LLM_CUSTOM:
            schema_dict[vol.Required(CONF_CUSTOM_API_URL)] = str

        return self.async_show_form(
            step_id="llm_config",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "provider": LLM_PROVIDERS.get(provider, provider),
            },
        )

    async def async_step_whatsapp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Configure WhatsApp."""
        errors = {}

        if user_input is not None:
            try:
                bridge_port = int(user_input.get("bridge_port", 3781))
                if not (1024 <= bridge_port <= 65535):
                    errors["bridge_port"] = "invalid_port"
            except (ValueError, TypeError):
                errors["bridge_port"] = "invalid_port"

            if not errors:
                self._data.update(user_input)
                return await self.async_step_security()

        return self.async_show_form(
            step_id="whatsapp",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WHATSAPP_GATEWAY, default=WhatsAppGateway.BAILEYS_DIRECT
                    ): vol.In(GATEWAY_LABELS),
                    # Port where the Baileys bridge listens (only used for baileys_direct)
                    vol.Optional("bridge_port", default=3781): vol.All(
                        vol.Coerce(int), vol.Range(min=1024, max=65535)
                    ),
                    # External gateways: leave blank when using Baileys direct
                    vol.Optional(CONF_WHATSAPP_API_URL, default=""): str,
                    vol.Optional(CONF_WHATSAPP_API_KEY, default=""): str,
                    vol.Optional(CONF_WHATSAPP_PHONE_ID, default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "baileys_note": (
                    "Para Baileys Direto, deixa API URL/Key/ID em branco - o QR code "
                    "aparece no Dashboard. A porta padrao e 3781."
                ),
            },
        )

    async def async_step_security(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Security settings."""
        errors = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_prompt()

        return self.async_show_form(
            step_id="security",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALLOWED_NUMBERS): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example": "351912345678,351967654321",
            },
        )

    async def async_step_prompt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5: Custom system prompt."""
        errors = {}

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title="Mordomo HA",
                data=self._data,
            )

        return self.async_show_form(
            step_id="prompt",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SYSTEM_PROMPT, default=DEFAULT_SYSTEM_PROMPT
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow."""
        return MordomoHAOptionsFlow(config_entry)


class MordomoHAOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Mordomo HA."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LLM_MODEL,
                        default=current.get(CONF_LLM_MODEL, ""),
                    ): str,
                    vol.Optional(
                        CONF_ALLOWED_NUMBERS,
                        default=current.get(CONF_ALLOWED_NUMBERS, ""),
                    ): str,
                    vol.Optional(
                        CONF_SYSTEM_PROMPT,
                        default=current.get(CONF_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT),
                    ): str,
                }
            ),
        )
