"""Mordomo HA - WhatsApp Smart Butler for Home Assistant."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType

from .command_processor import CommandProcessor
from .const import (
    CONF_ALLOWED_NUMBERS,
    CONF_LLM_API_KEY,
    CONF_LLM_MODEL,
    CONF_LLM_PROVIDER,
    CONF_SYSTEM_PROMPT,
    CONF_WHATSAPP_API_KEY,
    CONF_WHATSAPP_API_URL,
    CONF_WHATSAPP_PHONE_ID,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
    EVENT_MORDOMO_COMMAND,
    EVENT_MORDOMO_MESSAGE,
    LLM_OLLAMA,
    SERVICE_SEND_MESSAGE,
    SERVICE_CREATE_AUTOMATION,
    SERVICE_SCHEDULE_JOB,
    SERVICE_REMOVE_JOB,
    SERVICE_LIST_JOBS,
)
from .dashboard_api import setup_panel
from .llm_engine import create_llm_provider
from .scheduler import MordomoScheduler
from .whatsapp import create_whatsapp_gateway

_LOGGER = logging.getLogger(__name__)

WEBHOOK_ID_PREFIX = "mordomo_ha_"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Mordomo HA from yaml (if needed)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mordomo HA from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    config = {**entry.data, **entry.options}

    # Get LLM settings
    provider = config.get(CONF_LLM_PROVIDER, "openai")
    api_key = config.get(CONF_LLM_API_KEY, "")
    model = config.get(CONF_LLM_MODEL, "")
    system_prompt = config.get(CONF_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT)

    # Determine API URL for special providers
    llm_api_url = None
    if provider == LLM_OLLAMA:
        llm_api_url = config.get("ollama_url", "http://localhost:11434")
    elif provider == "custom_openai":
        llm_api_url = config.get("custom_api_url", "")

    # Get WhatsApp settings
    wa_gateway_type = config.get("whatsapp_gateway", "evolution_api")
    wa_api_url = config.get(CONF_WHATSAPP_API_URL, "")
    wa_api_key = config.get(CONF_WHATSAPP_API_KEY, "")
    wa_phone_id = config.get(CONF_WHATSAPP_PHONE_ID, "")

    # Security
    allowed_str = config.get(CONF_ALLOWED_NUMBERS, "")
    allowed_numbers = [
        n.strip().replace("+", "")
        for n in allowed_str.split(",")
        if n.strip()
    ]

    # Initialize components
    try:
        llm = create_llm_provider(provider, api_key, model, llm_api_url)
    except Exception as err:
        _LOGGER.error("Failed to create LLM provider: %s", err)
        return False

    # Build webhook URL for the bridge to forward messages to
    webhook_id = f"{WEBHOOK_ID_PREFIX}{entry.entry_id}"
    internal_webhook_url = f"http://homeassistant.local:8123/api/webhook/{webhook_id}"
    bridge_port = config.get("bridge_port", 3781)

    # If Baileys direct and API URL is set, use it as external bridge URL
    external_bridge_url = ""
    if wa_gateway_type == "baileys_direct" and wa_api_url:
        external_bridge_url = wa_api_url

    try:
        wa = create_whatsapp_gateway(
            wa_gateway_type, wa_api_url, wa_api_key, wa_phone_id,
            bridge_port=bridge_port,
            webhook_url=internal_webhook_url,
            ha_token="",
            auth_dir="",
        )
        # Override bridge URL if external bridge is configured
        if external_bridge_url and hasattr(wa, "bridge_url"):
            wa.bridge_url = external_bridge_url.rstrip("/")
    except Exception as err:
        _LOGGER.error("Failed to create WhatsApp gateway: %s", err)
        return False

    # Start Baileys bridge if using direct connection AND no external bridge
    if wa_gateway_type == "baileys_direct" and hasattr(wa, "start_bridge"):
        if external_bridge_url:
            _LOGGER.info("Using external Baileys bridge at %s", external_bridge_url)
        else:
            # Try to find the bridge add-on first (runs on host network port 3781)
            import aiohttp
            addon_bridge_url = f"http://127.0.0.1:{bridge_port}"
            addon_found = False
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{addon_bridge_url}/health",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        if resp.status == 200:
                            wa.bridge_url = addon_bridge_url
                            addon_found = True
                            _LOGGER.info(
                                "Found Mordomo Bridge add-on at %s", addon_bridge_url
                            )
            except Exception:
                pass

            if not addon_found:
                # Try starting local bridge (works on Docker/venv installs with Node.js)
                _LOGGER.info("Bridge add-on not found, trying local bridge...")
                try:
                    bridge_ok = await wa.start_bridge()
                    if not bridge_ok:
                        _LOGGER.warning(
                            "WhatsApp bridge not available. "
                            "Install the 'Mordomo Bridge' add-on from: "
                            "https://github.com/mentxia/mordomo-ha-addons"
                        )
                except Exception as bridge_err:
                    _LOGGER.warning(
                        "Bridge error: %s. Install the Mordomo Bridge add-on.",
                        bridge_err,
                    )

    cmd_processor = CommandProcessor(hass)

    # Initialize scheduler
    scheduler = MordomoScheduler(hass)
    scheduler.set_command_processor(cmd_processor)
    await scheduler.async_load()

    # Store references
    mordomo_data = {
        "llm": llm,
        "whatsapp": wa,
        "command_processor": cmd_processor,
        "scheduler": scheduler,
        "allowed_numbers": allowed_numbers,
        "system_prompt": system_prompt,
        "config": config,
    }
    hass.data[DOMAIN][entry.entry_id] = mordomo_data

    # Register webhook for incoming WhatsApp messages
    webhook_id = f"{WEBHOOK_ID_PREFIX}{entry.entry_id}"
    mordomo_data["webhook_id"] = webhook_id

    webhook.async_register(
        hass,
        DOMAIN,
        "Mordomo HA WhatsApp Webhook",
        webhook_id,
        _create_webhook_handler(hass, entry.entry_id),
    )

    webhook_url = webhook.async_generate_url(hass, webhook_id)
    _LOGGER.info("Mordomo HA webhook registered at: %s", webhook_url)

    # Register services
    await _register_services(hass, entry.entry_id)

    # Setup dashboard panel and API
    await setup_panel(hass, entry.entry_id)

    # Cleanup on shutdown
    async def _shutdown(event):
        await scheduler.async_save()
        dashboard = mordomo_data.get("dashboard")
        if dashboard:
            await dashboard.async_save()
        # Stop Baileys bridge
        wa_inst = mordomo_data.get("whatsapp")
        if hasattr(wa_inst, "stop_bridge"):
            await wa_inst.stop_bridge()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _shutdown)
    )

    _LOGGER.info("Mordomo HA initialized successfully!")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, {})

    # Unregister webhook
    webhook_id = data.get("webhook_id")
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)

    # Save scheduler state
    scheduler = data.get("scheduler")
    if scheduler:
        await scheduler.async_save()

    return True


def _create_webhook_handler(hass: HomeAssistant, entry_id: str):
    """Create a webhook handler closure."""

    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request
    ):
        """Handle incoming WhatsApp webhook."""
        try:
            data = await request.json()
        except Exception:
            _LOGGER.error("Failed to parse webhook JSON")
            return

        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            _LOGGER.error("Mordomo data not found for entry %s", entry_id)
            return

        wa = mordomo["whatsapp"]
        llm = mordomo["llm"]
        cmd_processor = mordomo["command_processor"]
        allowed_numbers = mordomo["allowed_numbers"]
        system_prompt = mordomo["system_prompt"]

        # Parse the incoming message
        parsed = wa.parse_webhook(data)
        if not parsed:
            _LOGGER.debug("No parseable message in webhook data")
            return

        sender = parsed["from"]
        message = parsed["message"]
        msg_type = parsed["type"]

        _LOGGER.info("Message from %s: %s", sender, message[:100])

        # Dashboard logging
        dashboard = mordomo.get("dashboard")
        if dashboard:
            dashboard.log_incoming(sender, message)

        # Security: check allowed numbers
        clean_sender = sender.replace("+", "").replace(" ", "")
        if allowed_numbers and clean_sender not in allowed_numbers:
            _LOGGER.warning("Unauthorized message from %s", sender)
            await wa.send_message(
                sender,
                "⛔ Não tens autorização para falar comigo. Contacta o administrador.",
            )
            return

        # Fire event for any listeners
        hass.bus.async_fire(
            EVENT_MORDOMO_MESSAGE,
            {"from": sender, "message": message, "type": msg_type},
        )

        # Special commands
        if message.strip().lower() in ("/reset", "/limpar", "/clear"):
            llm.clear_history(sender)
            await wa.send_message(sender, "🧹 Histórico de conversa limpo!")
            return

        if message.strip().lower() in ("/jobs", "/tarefas"):
            scheduler = mordomo["scheduler"]
            jobs = scheduler.get_jobs()
            if not jobs:
                await wa.send_message(sender, "📋 Nenhuma tarefa agendada.")
            else:
                text = "📋 *Tarefas agendadas:*\n\n"
                for job in jobs:
                    status = "✅" if job.enabled else "⏸️"
                    next_r = job.next_run.strftime("%d/%m %H:%M") if job.next_run else "N/A"
                    text += f"{status} *{job.description}*\n"
                    text += f"   ID: {job.job_id}\n"
                    text += f"   Cron: {job.cron_expression}\n"
                    text += f"   Próxima: {next_r}\n\n"
                await wa.send_message(sender, text)
            return

        if message.strip().lower() in ("/help", "/ajuda"):
            help_text = (
                "🤖 *Mordomo HA - Comandos:*\n\n"
                "Podes falar comigo normalmente! Alguns comandos especiais:\n\n"
                "/ajuda - Esta mensagem\n"
                "/limpar - Limpar histórico de conversa\n"
                "/tarefas - Ver tarefas agendadas\n"
                "/estado - Ver resumo rápido da casa\n"
                "/casa - Ver estado completo por divisão\n"
                "/divisões - Listar todas as divisões\n"
                "/divisão [nome] - Ver detalhe de uma divisão\n\n"
                "*Exemplos de pedidos:*\n"
                '• "Liga a luz da sala"\n'
                '• "Qual a temperatura do quarto?"\n'
                '• "O que está ligado na cozinha?"\n'
                '• "Cria uma automação para ligar a luz às 19h"\n'
                '• "Agenda para todos os dias às 8h abrir os estores"\n'
                '• "Que luzes estão ligadas?"\n'
                '• "Mostra-me o estado da sala"\n'
            )
            await wa.send_message(sender, help_text)
            return

        if message.strip().lower() in ("/estado", "/status", "/resumo"):
            context = await cmd_processor.home_awareness.get_summary_context()
            if len(context) > 3000:
                context = context[:3000] + "\n... (truncado)"
            await wa.send_message(sender, f"🏠 *Resumo da Casa:*\n{context}")
            return

        if message.strip().lower() in ("/casa", "/house", "/full"):
            context = await cmd_processor.home_awareness.get_full_house_context()
            # Split long messages
            if len(context) > 4000:
                parts = []
                current = ""
                for line in context.split("\n"):
                    if len(current) + len(line) > 3800:
                        parts.append(current)
                        current = line
                    else:
                        current += "\n" + line if current else line
                if current:
                    parts.append(current)
                for i, part in enumerate(parts):
                    header = f"🏠 *Casa ({i+1}/{len(parts)}):*\n" if len(parts) > 1 else "🏠 *Casa:*\n"
                    await wa.send_message(sender, header + part)
            else:
                await wa.send_message(sender, f"🏠 *Estado da Casa:*\n{context}")
            return

        if message.strip().lower() in ("/divisões", "/divisoes", "/areas", "/rooms"):
            areas_list = await cmd_processor.home_awareness.get_areas_list()
            await wa.send_message(sender, areas_list)
            return

        if message.strip().lower().startswith(("/divisão ", "/divisao ", "/area ", "/room ")):
            area_name = message.strip().split(" ", 1)[1] if " " in message.strip() else ""
            if area_name:
                area_context = await cmd_processor.home_awareness.get_area_context(area_name)
                await wa.send_message(sender, area_context)
            else:
                await wa.send_message(sender, "Indica o nome da divisão. Ex: /divisão Sala")
            return

        # Get HA context for the LLM
        ha_context = await cmd_processor.get_ha_context()

        # Send to LLM
        try:
            response = await llm.chat(message, system_prompt, sender, ha_context)
        except Exception as err:
            _LOGGER.error("LLM error: %s", err)
            await wa.send_message(
                sender, "Desculpa, tive um problema ao pensar na resposta. Tenta novamente."
            )
            return

        # Extract and execute commands from LLM response
        clean_response, commands = cmd_processor.extract_commands(response)

        if commands:
            _LOGGER.info("Executing %d commands from LLM response", len(commands))

            # Fire command event
            hass.bus.async_fire(
                EVENT_MORDOMO_COMMAND,
                {"from": sender, "commands": commands},
            )

            results = await cmd_processor.execute_commands(commands)

            # Dashboard command logging
            if dashboard:
                for cmd in commands:
                    dashboard.log_command(cmd.get("action", ""))

            # Append results to response
            if results:
                result_text = "\n".join(results)
                if clean_response:
                    clean_response += f"\n\n{result_text}"
                else:
                    clean_response = result_text

        # Send response via WhatsApp
        if clean_response:
            # Dashboard logging
            if dashboard:
                dashboard.log_outgoing(sender, clean_response)
                # Periodic save
                await dashboard.async_save()

            # WhatsApp has a ~4096 char limit
            if len(clean_response) > 4000:
                # Split into multiple messages
                parts = [
                    clean_response[i:i + 4000]
                    for i in range(0, len(clean_response), 4000)
                ]
                for part in parts:
                    await wa.send_message(sender, part)
            else:
                await wa.send_message(sender, clean_response)

    return handle_webhook


async def _register_services(hass: HomeAssistant, entry_id: str):
    """Register Mordomo HA services."""

    async def handle_send_message(call: ServiceCall):
        """Handle send_message service call."""
        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            return

        phone = call.data.get("phone", "")
        message = call.data.get("message", "")

        if phone and message:
            await mordomo["whatsapp"].send_message(phone, message)

    async def handle_create_automation(call: ServiceCall):
        """Handle create_automation service call."""
        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            return

        cmd = {
            "action": "create_automation",
            "alias": call.data.get("alias", "Mordomo Automation"),
            "trigger": call.data.get("trigger", []),
            "condition": call.data.get("condition", []),
            "action": call.data.get("automation_action", []),
        }
        await mordomo["command_processor"].execute_commands([cmd])

    async def handle_schedule_job(call: ServiceCall):
        """Handle schedule_job service call."""
        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            return

        await mordomo["scheduler"].add_job(
            cron_expression=call.data.get("cron", ""),
            description=call.data.get("description", ""),
            commands=call.data.get("commands", []),
            created_by="service",
        )

    async def handle_remove_job(call: ServiceCall):
        """Handle remove_job service call."""
        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            return

        await mordomo["scheduler"].remove_job(call.data.get("job_id", ""))

    async def handle_list_jobs(call: ServiceCall):
        """Handle list_jobs service call."""
        mordomo = hass.data.get(DOMAIN, {}).get(entry_id)
        if not mordomo:
            return

        jobs = mordomo["scheduler"].get_jobs()
        hass.bus.async_fire(
            "mordomo_ha_jobs_list",
            {"jobs": [j.to_dict() for j in jobs]},
        )

    # Register all services
    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, handle_send_message)
    hass.services.async_register(DOMAIN, SERVICE_CREATE_AUTOMATION, handle_create_automation)
    hass.services.async_register(DOMAIN, SERVICE_SCHEDULE_JOB, handle_schedule_job)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_JOB, handle_remove_job)
    hass.services.async_register(DOMAIN, SERVICE_LIST_JOBS, handle_list_jobs)
