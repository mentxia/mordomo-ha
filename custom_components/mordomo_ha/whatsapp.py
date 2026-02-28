"""WhatsApp Connector for Mordomo HA.

Primary method: Direct Baileys bridge (like OpenClaw) - just scan QR code.
Fallback: External gateways (Evolution API, WAHA, Meta Cloud API).
"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class WhatsAppGateway(str, Enum):
    """Supported WhatsApp connection methods."""
    BAILEYS_DIRECT = "baileys_direct"
    EVOLUTION_API = "evolution_api"
    WAHA = "waha"
    META_CLOUD = "meta_cloud"


GATEWAY_LABELS = {
    WhatsAppGateway.BAILEYS_DIRECT: "Direto via QR Code (como OpenClaw) - Recomendado",
    WhatsAppGateway.EVOLUTION_API: "Evolution API (Self-hosted)",
    WhatsAppGateway.WAHA: "WAHA - WhatsApp HTTP API (Self-hosted)",
    WhatsAppGateway.META_CLOUD: "Meta Cloud API (Oficial)",
}


class BaileysDirectGateway:
    """Direct WhatsApp Web connection via Baileys (same as OpenClaw).

    Runs a Node.js subprocess with Baileys. Scan QR code and go.
    No external gateway needed.
    """

    def __init__(self, bridge_port: int = 3781, auth_dir: str = "",
                 webhook_url: str = "", ha_token: str = ""):
        self.bridge_port = bridge_port
        self.bridge_url = f"http://127.0.0.1:{bridge_port}"
        self.auth_dir = auth_dir
        self.webhook_url = webhook_url
        self.ha_token = ha_token
        self._process: asyncio.subprocess.Process | None = None
        self._running = False
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a reusable aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def start_bridge(self) -> bool:
        """Start the Baileys bridge subprocess (non-blocking)."""
        bridge_dir = os.path.join(os.path.dirname(__file__), "bridge")
        bridge_script = os.path.join(bridge_dir, "baileys_bridge.js")
        node_modules = os.path.join(bridge_dir, "node_modules")

        # Check Node.js (non-blocking)
        try:
            proc = await asyncio.create_subprocess_exec(
                "node", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                _LOGGER.error("Node.js not found")
                return False
        except (FileNotFoundError, asyncio.TimeoutError):
            _LOGGER.error("Node.js not found. Install Node.js >= 18 to use Baileys.")
            return False

        # npm install if needed (non-blocking)
        if not os.path.exists(node_modules):
            _LOGGER.info("Installing Baileys bridge dependencies...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install", "--production",
                    cwd=bridge_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode != 0:
                    _LOGGER.error("npm install failed: %s", stderr.decode())
                    return False
            except Exception as err:
                _LOGGER.error("npm install error: %s", err)
                return False

        if not self.auth_dir:
            self.auth_dir = os.path.join(bridge_dir, "auth")
        os.makedirs(self.auth_dir, exist_ok=True)

        env = os.environ.copy()
        env.update({
            "MORDOMO_AUTH_DIR": self.auth_dir,
            "MORDOMO_WEBHOOK_URL": self.webhook_url,
            "MORDOMO_BRIDGE_PORT": str(self.bridge_port),
            "MORDOMO_HA_TOKEN": self.ha_token,
            "MORDOMO_LOG_LEVEL": "warn",
        })

        try:
            self._process = await asyncio.create_subprocess_exec(
                "node", bridge_script,
                cwd=bridge_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._running = True
            # Start log reading as a background task
            asyncio.get_running_loop().create_task(self._read_logs_async())
            await asyncio.sleep(3)

            if self._process.returncode is not None:
                _LOGGER.error("Bridge exited immediately")
                return False

            _LOGGER.info("Baileys bridge started on port %d (PID %d)", self.bridge_port, self._process.pid)
            return True
        except Exception as err:
            _LOGGER.error("Bridge start failed: %s", err)
            return False

    async def _read_logs_async(self):
        """Read bridge logs asynchronously."""
        if not self._process or not self._process.stdout:
            return
        try:
            while self._running:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if text:
                    _LOGGER.info("[baileys] %s", text)
        except Exception:
            pass

    async def stop_bridge(self):
        """Stop the bridge subprocess (non-blocking)."""
        self._running = False
        # Close the shared HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        if self._process:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

    async def send_message(self, to: str, message: str) -> bool:
        try:
            session = self._get_session()
            async with session.post(f"{self.bridge_url}/send",
                json={"to": to, "message": message},
                timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    _LOGGER.error("Bridge send error: %s", await resp.text())
                    return False
                return True
        except aiohttp.ClientError as err:
            _LOGGER.error("Bridge send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        try:
            session = self._get_session()
            async with session.post(f"{self.bridge_url}/send-image",
                json={"to": to, "image_url": image_url, "caption": caption},
                timeout=aiohttp.ClientTimeout(total=30)) as resp:
                return resp.status == 200
        except aiohttp.ClientError as err:
            _LOGGER.error("Bridge send image failed: %s", err)
            return False

    async def get_status(self) -> dict:
        try:
            session = self._get_session()
            async with session.get(f"{self.bridge_url}/status",
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {"status": "bridge_unreachable"}

    async def get_qr_code(self) -> dict:
        try:
            session = self._get_session()
            async with session.get(f"{self.bridge_url}/qr",
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {"status": "bridge_unreachable"}

    async def logout(self) -> bool:
        try:
            session = self._get_session()
            async with session.post(f"{self.bridge_url}/logout",
                timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
            return False

    def parse_webhook(self, data: dict) -> dict | None:
        if "from" not in data or "message" not in data:
            return None
        return {
            "from": data["from"],
            "message": data["message"],
            "type": data.get("type", "text"),
            "is_group": data.get("isGroup", False),
            "raw": data,
        }


class ExternalGateway:
    """Fallback: external WhatsApp gateways (Evolution API, WAHA, Meta Cloud)."""

    def __init__(self, gateway_type: str, api_url: str, api_key: str, phone_id: str = ""):
        self.gateway_type = gateway_type
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.phone_id = phone_id

    async def send_message(self, to: str, message: str) -> bool:
        number = to.replace("+", "").replace(" ", "")
        try:
            async with aiohttp.ClientSession() as session:
                if self.gateway_type == "evolution_api":
                    headers = {"apikey": self.api_key, "Content-Type": "application/json"}
                    url = f"{self.api_url}/message/sendText/{self.phone_id}"
                    payload = {"number": number, "text": message}
                elif self.gateway_type == "waha":
                    headers = {"Content-Type": "application/json"}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    url = f"{self.api_url}/api/sendText"
                    payload = {"chatId": f"{number}@c.us", "text": message,
                               "session": self.phone_id or "default"}
                elif self.gateway_type == "meta_cloud":
                    headers = {"Authorization": f"Bearer {self.api_key}",
                               "Content-Type": "application/json"}
                    url = f"{self.api_url}/{self.phone_id}/messages"
                    payload = {"messaging_product": "whatsapp", "to": number,
                               "type": "text", "text": {"body": message}}
                else:
                    return False

                async with session.post(url, headers=headers, json=payload) as resp:
                    return resp.status in (200, 201)
        except Exception as err:
            _LOGGER.error("Gateway send failed: %s", err)
            return False

    async def send_image(self, to: str, image_url: str, caption: str = "") -> bool:
        return False

    def parse_webhook(self, data: dict) -> dict | None:
        try:
            if self.gateway_type == "evolution_api":
                event = data.get("event", "")
                if event not in ("messages.upsert", "MESSAGES_UPSERT"):
                    return None
                msg_data = data.get("data", {})
                key = msg_data.get("key", {})
                if key.get("fromMe", False):
                    return None
                phone = key.get("remoteJid", "").split("@")[0]
                mc = msg_data.get("message", {})
                text = mc.get("conversation", "") or mc.get("extendedTextMessage", {}).get("text", "")
                return {"from": phone, "message": text, "type": "text", "raw": msg_data} if text else None
            elif self.gateway_type == "waha":
                if data.get("event") != "message":
                    return None
                p = data.get("payload", {})
                if p.get("fromMe"):
                    return None
                phone = p.get("from", "").replace("@c.us", "")
                text = p.get("body", "")
                return {"from": phone, "message": text, "type": "text", "raw": p} if text else None
            elif self.gateway_type == "meta_cloud":
                msgs = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
                if not msgs:
                    return None
                m = msgs[0]
                return {"from": m["from"], "message": m.get("text", {}).get("body", ""),
                        "type": m.get("type", "text"), "raw": m}
        except Exception as err:
            _LOGGER.error("Webhook parse error: %s", err)
        return None


def create_whatsapp_gateway(
    gateway_type: str, api_url: str = "", api_key: str = "", phone_id: str = "",
    bridge_port: int = 3781, webhook_url: str = "", ha_token: str = "", auth_dir: str = "",
) -> BaileysDirectGateway | ExternalGateway:
    """Factory to create the appropriate WhatsApp connector."""
    if gateway_type == WhatsAppGateway.BAILEYS_DIRECT:
        return BaileysDirectGateway(bridge_port=bridge_port, auth_dir=auth_dir,
                                     webhook_url=webhook_url, ha_token=ha_token)
    return ExternalGateway(gateway_type, api_url, api_key, phone_id)
