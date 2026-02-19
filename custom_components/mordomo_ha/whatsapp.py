"""WhatsApp Connector for Mordomo HA."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class WhatsAppGateway(str, Enum):
    """Supported WhatsApp gateways."""

    META_CLOUD = "meta_cloud"
    EVOLUTION_API = "evolution_api"
    WAHA = "waha"
    BAILEYS_API = "baileys_api"


GATEWAY_LABELS = {
    WhatsAppGateway.META_CLOUD: "Meta Cloud API (Oficial)",
    WhatsAppGateway.EVOLUTION_API: "Evolution API (Self-hosted)",
    WhatsAppGateway.WAHA: "WAHA - WhatsApp HTTP API (Self-hosted)",
    WhatsAppGateway.BAILEYS_API: "Baileys API (Self-hosted)",
}


class BaseWhatsAppGateway(ABC):
    """Base class for WhatsApp gateways."""

    def __init__(self, api_url: str, api_key: str, phone_id: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.phone_id = phone_id

    @abstractmethod
    async def send_message(self, to: str, message: str) -> bool:
        """Send a text message."""

    @abstractmethod
    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        """Send an image message."""

    @abstractmethod
    def parse_webhook(self, data: dict) -> dict | None:
        """Parse incoming webhook data. Returns dict with 'from', 'message', 'type'."""


class MetaCloudGateway(BaseWhatsAppGateway):
    """Meta Cloud API (Official WhatsApp Business API)."""

    async def send_message(self, to: str, message: str) -> bool:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/{self.phone_id}/messages",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        _LOGGER.error("Meta API error: %s", error)
                        return False
                    return True
        except Exception as err:
            _LOGGER.error("Meta send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": image_url, "caption": caption},
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/{self.phone_id}/messages",
                    headers=headers,
                    json=payload,
                ) as resp:
                    return resp.status == 200
        except Exception as err:
            _LOGGER.error("Meta image send failed: %s", err)
            return False

    def parse_webhook(self, data: dict) -> dict | None:
        try:
            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                return None
            msg = messages[0]
            return {
                "from": msg["from"],
                "message": msg.get("text", {}).get("body", ""),
                "type": msg.get("type", "text"),
                "raw": msg,
            }
        except (IndexError, KeyError) as err:
            _LOGGER.error("Failed to parse Meta webhook: %s", err)
            return None


class EvolutionAPIGateway(BaseWhatsAppGateway):
    """Evolution API gateway (popular self-hosted option)."""

    async def send_message(self, to: str, message: str) -> bool:
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        # Ensure number format
        number = to.replace("+", "").replace(" ", "")
        payload = {
            "number": number,
            "text": message,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/message/sendText/{self.phone_id}",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status not in (200, 201):
                        error = await resp.text()
                        _LOGGER.error("Evolution API error: %s", error)
                        return False
                    return True
        except Exception as err:
            _LOGGER.error("Evolution send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        number = to.replace("+", "").replace(" ", "")
        payload = {
            "number": number,
            "mediatype": "image",
            "media": image_url,
            "caption": caption,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/message/sendMedia/{self.phone_id}",
                    headers=headers,
                    json=payload,
                ) as resp:
                    return resp.status in (200, 201)
        except Exception as err:
            _LOGGER.error("Evolution image send failed: %s", err)
            return False

    def parse_webhook(self, data: dict) -> dict | None:
        try:
            # Evolution API webhook format
            event = data.get("event", "")
            if event not in ("messages.upsert", "MESSAGES_UPSERT"):
                return None

            msg_data = data.get("data", {})
            key = msg_data.get("key", {})

            # Skip messages from self
            if key.get("fromMe", False):
                return None

            remote_jid = key.get("remoteJid", "")
            phone = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

            message_content = msg_data.get("message", {})
            text = (
                message_content.get("conversation", "")
                or message_content.get("extendedTextMessage", {}).get("text", "")
            )

            if not text:
                return None

            return {
                "from": phone,
                "message": text,
                "type": "text",
                "raw": msg_data,
            }
        except (IndexError, KeyError) as err:
            _LOGGER.error("Failed to parse Evolution webhook: %s", err)
            return None


class WAHAGateway(BaseWhatsAppGateway):
    """WAHA - WhatsApp HTTP API gateway."""

    async def send_message(self, to: str, message: str) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        number = to.replace("+", "").replace(" ", "")
        payload = {
            "chatId": f"{number}@c.us",
            "text": message,
            "session": self.phone_id or "default",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/sendText",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status not in (200, 201):
                        error = await resp.text()
                        _LOGGER.error("WAHA API error: %s", error)
                        return False
                    return True
        except Exception as err:
            _LOGGER.error("WAHA send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        number = to.replace("+", "").replace(" ", "")
        payload = {
            "chatId": f"{number}@c.us",
            "file": {"url": image_url},
            "caption": caption,
            "session": self.phone_id or "default",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/sendImage",
                    headers=headers,
                    json=payload,
                ) as resp:
                    return resp.status in (200, 201)
        except Exception as err:
            _LOGGER.error("WAHA image send failed: %s", err)
            return False

    def parse_webhook(self, data: dict) -> dict | None:
        try:
            event = data.get("event", "")
            if event != "message":
                return None

            payload = data.get("payload", {})
            if payload.get("fromMe", False):
                return None

            chat_id = payload.get("from", "")
            phone = chat_id.replace("@c.us", "").replace("@g.us", "")
            text = payload.get("body", "")

            if not text:
                return None

            return {
                "from": phone,
                "message": text,
                "type": "text",
                "raw": payload,
            }
        except (IndexError, KeyError) as err:
            _LOGGER.error("Failed to parse WAHA webhook: %s", err)
            return None


class BaileysGateway(BaseWhatsAppGateway):
    """Baileys-based API gateway."""

    async def send_message(self, to: str, message: str) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        number = to.replace("+", "").replace(" ", "")
        payload = {
            "jid": f"{number}@s.whatsapp.net",
            "message": {"text": message},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/send-message",
                    headers=headers,
                    json=payload,
                ) as resp:
                    return resp.status in (200, 201)
        except Exception as err:
            _LOGGER.error("Baileys send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        number = to.replace("+", "").replace(" ", "")
        payload = {
            "jid": f"{number}@s.whatsapp.net",
            "message": {"image": {"url": image_url}, "caption": caption},
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/send-message",
                    headers=headers,
                    json=payload,
                ) as resp:
                    return resp.status in (200, 201)
        except Exception as err:
            _LOGGER.error("Baileys image send failed: %s", err)
            return False

    def parse_webhook(self, data: dict) -> dict | None:
        try:
            msg = data.get("message", {})
            key = data.get("key", {})

            if key.get("fromMe", False):
                return None

            remote_jid = key.get("remoteJid", "")
            phone = remote_jid.split("@")[0]

            text = (
                msg.get("conversation", "")
                or msg.get("extendedTextMessage", {}).get("text", "")
            )

            if not text:
                return None

            return {
                "from": phone,
                "message": text,
                "type": "text",
                "raw": data,
            }
        except (IndexError, KeyError) as err:
            _LOGGER.error("Failed to parse Baileys webhook: %s", err)
            return None


def create_whatsapp_gateway(
    gateway_type: str | WhatsAppGateway,
    api_url: str,
    api_key: str,
    phone_id: str = "",
) -> BaseWhatsAppGateway:
    """Factory to create the appropriate WhatsApp gateway."""
    gw = WhatsAppGateway(gateway_type) if isinstance(gateway_type, str) else gateway_type

    if gw == WhatsAppGateway.META_CLOUD:
        return MetaCloudGateway(
            api_url or "https://graph.facebook.com/v19.0",
            api_key,
            phone_id,
        )
    elif gw == WhatsAppGateway.EVOLUTION_API:
        return EvolutionAPIGateway(api_url, api_key, phone_id)
    elif gw == WhatsAppGateway.WAHA:
        return WAHAGateway(api_url, api_key, phone_id)
    elif gw == WhatsAppGateway.BAILEYS_API:
        return BaileysGateway(api_url, api_key, phone_id)
    else:
        raise ValueError(f"Unknown WhatsApp gateway: {gw}")
