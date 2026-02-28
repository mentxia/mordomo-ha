"""Dashboard API for Mordomo HA - Backend endpoints for the panel."""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import Any

from aiohttp import web

from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "mordomo_ha.dashboard"
STORAGE_VERSION = 1
MAX_MESSAGES = 500

PANEL_URL = "/mordomo-ha-panel"


class DashboardData:
    """Manages dashboard state: message log, stats, etc."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.messages: deque[dict] = deque(maxlen=MAX_MESSAGES)
        self._msg_counter = 0
        self.stats = {
            "total_messages_in": 0,
            "total_messages_out": 0,
            "total_commands": 0,
            "total_automations_created": 0,
            "total_jobs_scheduled": 0,
            "started_at": None,
            "last_message_at": None,
            "unique_users": set(),
            "errors": 0,
        }

    async def async_load(self):
        """Load stored data."""
        data = await self._store.async_load()
        if data:
            for msg in data.get("messages", []):
                self.messages.append(msg)
            stored_stats = data.get("stats", {})
            self.stats["total_messages_in"] = stored_stats.get("total_messages_in", 0)
            self.stats["total_messages_out"] = stored_stats.get("total_messages_out", 0)
            self.stats["total_commands"] = stored_stats.get("total_commands", 0)
            self.stats["total_automations_created"] = stored_stats.get("total_automations_created", 0)
            self.stats["total_jobs_scheduled"] = stored_stats.get("total_jobs_scheduled", 0)
            self.stats["started_at"] = stored_stats.get("started_at")
            self.stats["unique_users"] = set(stored_stats.get("unique_users", []))
            self.stats["errors"] = stored_stats.get("errors", 0)

        if not self.stats["started_at"]:
            self.stats["started_at"] = dt_util.now().isoformat()

    async def async_save(self):
        """Persist data."""
        data = {
            "messages": list(self.messages),
            "stats": {
                **self.stats,
                "unique_users": list(self.stats["unique_users"]),
            },
        }
        await self._store.async_save(data)

    def log_incoming(self, sender: str, message: str):
        """Log an incoming message."""
        self._msg_counter += 1
        self.messages.append({
            "id": self._msg_counter,
            "direction": "in",
            "phone": sender,
            "text": message,
            "timestamp": dt_util.now().isoformat(),
        })
        self.stats["total_messages_in"] += 1
        self.stats["last_message_at"] = dt_util.now().isoformat()
        self.stats["unique_users"].add(sender)

    def log_outgoing(self, recipient: str, message: str):
        """Log an outgoing message."""
        self._msg_counter += 1
        self.messages.append({
            "id": self._msg_counter,
            "direction": "out",
            "phone": recipient,
            "text": message,
            "timestamp": dt_util.now().isoformat(),
        })
        self.stats["total_messages_out"] += 1

    def log_command(self, command_type: str = ""):
        """Log a command execution."""
        self.stats["total_commands"] += 1
        if command_type == "create_automation":
            self.stats["total_automations_created"] += 1
        elif command_type == "schedule_job":
            self.stats["total_jobs_scheduled"] += 1

    def log_error(self):
        """Log an error."""
        self.stats["errors"] += 1

    def get_messages(self, limit: int = 100, phone: str = "") -> list[dict]:
        """Get recent messages, optionally filtered by phone."""
        msgs = list(self.messages)
        if phone:
            msgs = [m for m in msgs if m.get("phone") == phone]
        return msgs[-limit:]

    def get_stats(self) -> dict:
        """Get dashboard statistics."""
        return {
            **self.stats,
            "unique_users": list(self.stats["unique_users"]),
            "message_log_size": len(self.messages),
        }


# -- Module-level guards: panel/views survive HA config entry reloads --
_PANEL_REGISTERED = False
_VIEWS_REGISTERED = False


async def setup_panel(hass: HomeAssistant, entry_id: str):
    """Register the Mordomo HA panel and API routes.

    The frontend panel and HTTP views are registered exactly once for the
    lifetime of the HA process - they cannot be unregistered and re-registered
    without a full restart, so we guard with a module-level flag.
    """
    global _PANEL_REGISTERED, _VIEWS_REGISTERED  # noqa: PLW0603

    mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
    if not mordomo:
        return

    # Initialize dashboard data (always fresh per config entry)
    dashboard = DashboardData(hass)
    await dashboard.async_load()
    mordomo["dashboard"] = dashboard

    # Store active entry_id so views can always find the current data
    hass.data[DOMAIN]["_active_entry_id"] = entry_id

    # -- Panel (sidebar) - register only once per process lifetime --
    if not _PANEL_REGISTERED:
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="iframe",
                sidebar_title="Mordomo HA",
                sidebar_icon="mdi:robot-happy",
                frontend_url_path="mordomo-ha",
                config={"url": PANEL_URL},
                require_admin=False,
            )
            _PANEL_REGISTERED = True
            _LOGGER.info("Mordomo HA sidebar panel registered")
        except Exception as err:
            _LOGGER.debug("Panel already registered: %s", err)
            _PANEL_REGISTERED = True

    # -- HTTP API views - register only once per process lifetime --
    if not _VIEWS_REGISTERED:
        hass.http.register_view(MordomoPanelView)
        hass.http.register_view(MordomoApiMessages)
        hass.http.register_view(MordomoApiStats)
        hass.http.register_view(MordomoApiChat)
        hass.http.register_view(MordomoApiConfig)
        hass.http.register_view(MordomoApiQrCode)
        hass.http.register_view(MordomoApiJobs)
        hass.http.register_view(MordomoApiHouseState)
        _VIEWS_REGISTERED = True
        _LOGGER.info("Mordomo HA dashboard API views registered")
    else:
        _LOGGER.debug("Mordomo HA views already registered; skipping")


def _get_mordomo(hass: HomeAssistant) -> dict:
    """Helper: get the active mordomo data dict."""
    domain_data = hass.data.get(DOMAIN, {})
    entry_id = domain_data.get("_active_entry_id", "")
    return domain_data.get(entry_id, {})


# ----------------------------------------------------------------------
# All views inherit from HomeAssistantView which provides:
#   - .register() method required by hass.http.register_view()
#   - Built-in auth checking via requires_auth
#   - Proper aiohttp route registration
# ----------------------------------------------------------------------


class MordomoPanelView(HomeAssistantView):
    """Serve the main dashboard HTML."""

    url = PANEL_URL
    name = "mordomo_ha_panel"
    requires_auth = True

    async def get(self, request):
        """Serve the panel HTML."""
        hass = request.app["hass"]
        panel_path = os.path.join(
            os.path.dirname(__file__), "panel", "index.html"
        )

        def _read_panel():
            with open(panel_path, "r", encoding="utf-8") as f:
                return f.read()

        try:
            html = await hass.async_add_executor_job(_read_panel)
            return web.Response(text=html, content_type="text/html")
        except FileNotFoundError:
            return web.Response(
                text="<h1>Mordomo HA Panel not found</h1><p>Panel files missing.</p>",
                content_type="text/html",
                status=404,
            )


class MordomoApiMessages(HomeAssistantView):
    """API: Get message history."""

    url = "/api/mordomo_ha/messages"
    name = "mordomo_ha_api_messages"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        dashboard = mordomo.get("dashboard")
        if not dashboard:
            return self.json({"error": "Dashboard not initialized"}, status_code=500)

        try:
            limit = int(request.query.get("limit", 100))
        except (ValueError, TypeError):
            limit = 100
        phone = request.query.get("phone", "")
        messages = dashboard.get_messages(limit=limit, phone=phone)
        return self.json({"messages": messages})


class MordomoApiStats(HomeAssistantView):
    """API: Get statistics."""

    url = "/api/mordomo_ha/stats"
    name = "mordomo_ha_api_stats"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        dashboard = mordomo.get("dashboard")
        if not dashboard:
            return self.json({"error": "Dashboard not initialized"}, status_code=500)

        stats = dashboard.get_stats()

        # Add live info
        scheduler = mordomo.get("scheduler")
        if scheduler:
            jobs = scheduler.get_jobs()
            stats["active_jobs"] = len([j for j in jobs if j.enabled])
            stats["jobs"] = [j.to_dict() for j in jobs]

        # Connection status
        stats["whatsapp_gateway"] = mordomo.get("config", {}).get("whatsapp_gateway", "unknown")
        stats["llm_provider"] = mordomo.get("config", {}).get("llm_provider", "unknown")
        stats["llm_model"] = mordomo.get("config", {}).get("llm_model", "unknown")
        stats["webhook_id"] = mordomo.get("webhook_id", "")

        return self.json(stats)


class MordomoApiChat(HomeAssistantView):
    """API: Send a message as if from the dashboard (admin chat)."""

    url = "/api/mordomo_ha/chat"
    name = "mordomo_ha_api_chat"
    requires_auth = True

    async def post(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        if not mordomo:
            return self.json({"error": "Not initialized"}, status_code=500)

        data = await request.json()
        message = data.get("message", "").strip()
        if not message:
            return self.json({"error": "Empty message"}, status_code=400)

        llm = mordomo["llm"]
        cmd_processor = mordomo["command_processor"]
        system_prompt = mordomo["system_prompt"]
        dashboard = mordomo.get("dashboard")

        # Log incoming
        if dashboard:
            dashboard.log_incoming("dashboard", message)

        # Get HA context
        ha_context = await cmd_processor.get_ha_context()

        # Send to LLM
        try:
            response = await llm.chat(message, system_prompt, "dashboard", ha_context)
        except Exception as err:
            _LOGGER.error("Dashboard chat LLM error: %s", err)
            return self.json({"error": str(err)}, status_code=500)

        # Process commands
        clean_response, commands = cmd_processor.extract_commands(response)
        command_results = []

        if commands:
            results = await cmd_processor.execute_commands(commands)
            command_results = results
            if dashboard:
                for cmd in commands:
                    dashboard.log_command(cmd.get("action", ""))

        # Log outgoing
        if dashboard:
            dashboard.log_outgoing("dashboard", clean_response)
            await dashboard.async_save()

        return self.json({
            "response": clean_response,
            "commands_executed": len(commands),
            "command_results": command_results,
        })


class MordomoApiConfig(HomeAssistantView):
    """API: Get/update configuration."""

    url = "/api/mordomo_ha/config"
    name = "mordomo_ha_api_config"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        config = mordomo.get("config", {})

        # Mask sensitive data
        safe_config = {
            "llm_provider": config.get("llm_provider", ""),
            "llm_model": config.get("llm_model", ""),
            "llm_api_key_set": bool(config.get("llm_api_key", "")),
            "whatsapp_gateway": config.get("whatsapp_gateway", ""),
            "whatsapp_api_url": config.get("whatsapp_api_url", ""),
            "whatsapp_api_key_set": bool(config.get("whatsapp_api_key", "")),
            "whatsapp_phone_id": config.get("whatsapp_phone_id", ""),
            "allowed_numbers": config.get("allowed_numbers", ""),
            "system_prompt": config.get("system_prompt", ""),
            "webhook_url": f"/api/webhook/{mordomo.get('webhook_id', '')}",
        }
        return self.json(safe_config)


class MordomoApiQrCode(HomeAssistantView):
    """API: Get QR code for WhatsApp pairing."""

    url = "/api/mordomo_ha/qrcode"
    name = "mordomo_ha_api_qrcode"
    requires_auth = True

    async def get(self, request):
        """Fetch QR code - Baileys bridge (primary) or external gateway."""
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        config = mordomo.get("config", {})
        gateway_type = config.get("whatsapp_gateway", "")
        wa = mordomo.get("whatsapp")

        try:
            # -- Baileys Direct (like OpenClaw) --
            if gateway_type == "baileys_direct" and hasattr(wa, "get_qr_code"):
                data = await wa.get_qr_code()
                status = data.get("status", "bridge_unreachable")

                if status == "connected":
                    return self.json({
                        "status": "connected",
                        "gateway": "baileys_direct",
                    })
                elif status == "qr_ready":
                    return self.json({
                        "status": "qr_ready",
                        "qr_base64": data.get("qr_base64", ""),
                        "qr_code": data.get("qr", ""),
                        "gateway": "baileys_direct",
                    })
                else:
                    return self.json({
                        "status": status,
                        "gateway": "baileys_direct",
                        "error": data.get("error", ""),
                    })

            # -- External gateways (fallback) --
            import aiohttp

            api_url = config.get("whatsapp_api_url", "").rstrip("/")
            api_key = config.get("whatsapp_api_key", "")
            instance = config.get("whatsapp_phone_id", "")

            async with aiohttp.ClientSession() as session:
                if gateway_type == "evolution_api":
                    headers = {"apikey": api_key}
                    url = f"{api_url}/instance/connect/{instance}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            qr_base64 = data.get("base64", "")
                            qr_code = data.get("code", "")
                            return self.json({
                                "status": "qr_ready" if (qr_base64 or qr_code) else "connected",
                                "qr_base64": qr_base64,
                                "qr_code": qr_code,
                                "gateway": "evolution_api",
                            })
                        error = await resp.text()
                        return self.json({"status": "error", "error": error}, status_code=resp.status)

                elif gateway_type == "waha":
                    headers = {}
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    session_name = instance or "default"

                    url = f"{api_url}/api/sessions/{session_name}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("status") == "WORKING":
                                return self.json({"status": "connected", "gateway": "waha"})

                    url = f"{api_url}/api/sessions/{session_name}/auth/qr"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            if "image" in resp.content_type:
                                import base64
                                image_data = await resp.read()
                                qr_b64 = base64.b64encode(image_data).decode()
                                return self.json({
                                    "status": "qr_ready",
                                    "qr_base64": f"data:image/png;base64,{qr_b64}",
                                    "gateway": "waha",
                                })
                            data = await resp.json()
                            return self.json({
                                "status": "qr_ready",
                                "qr_code": data.get("value", ""),
                                "gateway": "waha",
                            })
                        return self.json({"status": "error", "error": "QR not available"})

                elif gateway_type == "meta_cloud":
                    return self.json({
                        "status": "not_applicable",
                        "message": "A Meta Cloud API nao usa QR code. Configura via Facebook Business.",
                        "gateway": "meta_cloud",
                    })

                return self.json({
                    "status": "unsupported",
                    "message": f"QR code nao suportado para: {gateway_type}",
                })

        except Exception as err:
            _LOGGER.error("QR code fetch error: %s", err)
            return self.json({"status": "error", "error": str(err)}, status_code=500)


class MordomoApiJobs(HomeAssistantView):
    """API: Manage scheduled jobs."""

    url = "/api/mordomo_ha/jobs"
    name = "mordomo_ha_api_jobs"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        scheduler = mordomo.get("scheduler")
        if not scheduler:
            return self.json({"jobs": []})
        jobs = [j.to_dict() for j in scheduler.get_jobs()]
        return self.json({"jobs": jobs})

    async def delete(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        scheduler = mordomo.get("scheduler")
        if not scheduler:
            return self.json({"error": "Scheduler not found"}, status_code=500)
        data = await request.json()
        job_id = data.get("job_id", "")
        if job_id:
            success = await scheduler.remove_job(job_id)
            return self.json({"success": success})
        return self.json({"error": "job_id required"}, status_code=400)


class MordomoApiHouseState(HomeAssistantView):
    """API: Get current house state for the dashboard."""

    url = "/api/mordomo_ha/house"
    name = "mordomo_ha_api_house"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        mordomo = _get_mordomo(hass)
        cmd_processor = mordomo.get("command_processor")
        if not cmd_processor:
            return self.json({"error": "Not initialized"}, status_code=500)

        detail = request.query.get("detail", "summary")
        area = request.query.get("area", "")

        if area:
            result = await cmd_processor.home_awareness.get_area_context(area)
        elif detail == "full":
            result = await cmd_processor.home_awareness.get_full_house_context()
        else:
            result = await cmd_processor.home_awareness.get_summary_context()

        areas_list = await cmd_processor.home_awareness.get_areas_list()

        return self.json({
            "state": result,
            "areas": areas_list,
        })
