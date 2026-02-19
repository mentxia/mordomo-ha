"""Command Processor for Mordomo HA - Executes actions from LLM responses."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .home_awareness import HomeAwareness

_LOGGER = logging.getLogger(__name__)


class CommandProcessor:
    """Processes commands extracted from LLM responses."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.home_awareness = HomeAwareness(hass)

    def extract_commands(self, text: str) -> tuple[str, list[dict]]:
        """Extract JSON command blocks from LLM response text.

        Returns the clean text (without JSON blocks) and list of commands.
        """
        commands = []
        clean_text = text

        # Find JSON blocks in the response (```json ... ``` or inline {...})
        # Pattern 1: fenced code blocks
        fenced_pattern = r'```(?:json)?\s*(\{[^`]+?\})\s*```'
        for match in re.finditer(fenced_pattern, text, re.DOTALL):
            try:
                cmd = json.loads(match.group(1))
                if "action" in cmd:
                    commands.append(cmd)
                    clean_text = clean_text.replace(match.group(0), "")
            except json.JSONDecodeError:
                pass

        # Pattern 2: inline JSON objects with "action" key
        inline_pattern = r'\{[^{}]*"action"\s*:\s*"[^"]*"[^{}]*\}'
        for match in re.finditer(inline_pattern, text):
            try:
                cmd = json.loads(match.group(0))
                if cmd not in commands:
                    commands.append(cmd)
                    clean_text = clean_text.replace(match.group(0), "")
            except json.JSONDecodeError:
                pass

        # Clean up extra whitespace
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

        return clean_text, commands

    async def execute_commands(self, commands: list[dict]) -> list[str]:
        """Execute a list of commands and return results."""
        results = []
        for cmd in commands:
            try:
                action = cmd.get("action", "")
                if action == "call_service":
                    result = await self._call_service(cmd)
                elif action == "get_state":
                    result = await self._get_state(cmd)
                elif action == "get_states":
                    result = await self._get_states(cmd)
                elif action == "get_area":
                    result = await self._get_area(cmd)
                elif action == "get_areas":
                    result = await self._get_areas()
                elif action == "get_house_summary":
                    result = await self._get_house_summary()
                elif action == "create_automation":
                    result = await self._create_automation(cmd)
                elif action == "schedule_job":
                    result = await self._schedule_job(cmd)
                elif action == "remove_job":
                    result = await self._remove_job(cmd)
                elif action == "list_entities":
                    result = await self._list_entities(cmd)
                else:
                    result = f"Ação desconhecida: {action}"

                results.append(result)
            except Exception as err:
                _LOGGER.error("Command execution error: %s", err)
                results.append(f"Erro ao executar comando: {err}")

        return results

    async def _call_service(self, cmd: dict) -> str:
        """Call a Home Assistant service."""
        domain = cmd.get("domain", "")
        service = cmd.get("service", "")
        target = cmd.get("target", {})
        data = cmd.get("data", {})

        if not domain or not service:
            return "Erro: domínio e serviço são obrigatórios."

        service_data = {**data}
        if target:
            service_data.update(target)

        try:
            await self.hass.services.async_call(
                domain, service, service_data, blocking=True
            )
            entity = target.get("entity_id", "desconhecido")
            return f"✅ Executado: {domain}.{service} em {entity}"
        except Exception as err:
            return f"❌ Erro ao executar {domain}.{service}: {err}"

    async def _get_state(self, cmd: dict) -> str:
        """Get state of an entity."""
        entity_id = cmd.get("entity_id", "")
        if not entity_id:
            return "Erro: entity_id é obrigatório."

        state = self.hass.states.get(entity_id)
        if state is None:
            return f"Entidade '{entity_id}' não encontrada."

        attrs = dict(state.attributes)
        friendly_name = attrs.pop("friendly_name", entity_id)
        unit = attrs.pop("unit_of_measurement", "")

        result = f"📊 {friendly_name}: {state.state}"
        if unit:
            result += f" {unit}"

        # Add relevant attributes
        relevant_attrs = {
            k: v for k, v in attrs.items()
            if k in ("temperature", "humidity", "brightness", "color_temp",
                     "battery_level", "current_temperature", "hvac_action",
                     "media_title", "source", "volume_level")
        }
        if relevant_attrs:
            for key, val in relevant_attrs.items():
                result += f"\n  {key}: {val}"

        return result

    async def _get_states(self, cmd: dict) -> str:
        """Get states of multiple entities or a domain."""
        domain_filter = cmd.get("domain", "")
        area_filter = cmd.get("area", "")
        entities = cmd.get("entity_ids", [])

        results = []

        if entities:
            for eid in entities:
                state = self.hass.states.get(eid)
                if state:
                    name = state.attributes.get("friendly_name", eid)
                    unit = state.attributes.get("unit_of_measurement", "")
                    results.append(f"  • {name}: {state.state} {unit}".strip())
        elif domain_filter:
            all_states = self.hass.states.async_all(domain_filter)
            for state in all_states[:20]:  # Limit to 20
                name = state.attributes.get("friendly_name", state.entity_id)
                unit = state.attributes.get("unit_of_measurement", "")
                results.append(f"  • {name}: {state.state} {unit}".strip())

        if not results:
            return "Nenhuma entidade encontrada."

        return "📊 Estados:\n" + "\n".join(results)

    async def _create_automation(self, cmd: dict) -> str:
        """Create a Home Assistant automation."""
        alias = cmd.get("alias", "Mordomo Automation")
        trigger = cmd.get("trigger", [])
        condition = cmd.get("condition", [])
        action = cmd.get("action", [])
        description = cmd.get("description", f"Criada pelo Mordomo HA")
        mode = cmd.get("mode", "single")

        if not trigger or not action:
            return "Erro: trigger e action são obrigatórios para criar automação."

        automation_config = {
            "alias": alias,
            "description": description,
            "trigger": trigger,
            "condition": condition,
            "action": action,
            "mode": mode,
        }

        try:
            # Use the automation component to create
            await self.hass.services.async_call(
                "automation",
                "reload",
                blocking=True,
            )

            # Write automation to automations.yaml via config entries
            # We'll fire an event that can be picked up by a script
            self.hass.bus.async_fire(
                "mordomo_ha_create_automation",
                automation_config,
            )

            # Also try to create via the config API
            config_result = await self._write_automation_config(automation_config)

            return f"✅ Automação '{alias}' criada com sucesso!\n{config_result}"
        except Exception as err:
            return f"❌ Erro ao criar automação: {err}"

    async def _write_automation_config(self, config: dict) -> str:
        """Write automation to HA config via websocket API."""
        import uuid

        try:
            # Create via the automation config store
            automation_id = str(uuid.uuid4()).replace("-", "")[:12]

            await self.hass.services.async_call(
                "automation",
                "reload",
                blocking=True,
            )

            # Store the automation config
            config_path = self.hass.config.path("automations.yaml")

            import yaml
            import os

            # Read existing automations
            existing = []
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    content = yaml.safe_load(f) or []
                    if isinstance(content, list):
                        existing = content

            # Add new automation
            new_automation = {
                "id": automation_id,
                **config,
            }
            existing.append(new_automation)

            # Write back
            with open(config_path, "w") as f:
                yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)

            # Reload automations
            await self.hass.services.async_call(
                "automation", "reload", blocking=True
            )

            return f"ID: {automation_id}"

        except Exception as err:
            _LOGGER.error("Failed to write automation config: %s", err)
            return f"Nota: automação registada mas pode precisar de reload manual."

    async def _schedule_job(self, cmd: dict) -> str:
        """Schedule a cron job (delegates to scheduler component)."""
        cron_expr = cmd.get("cron", "")
        description = cmd.get("description", "Tarefa agendada")
        commands = cmd.get("commands", [])

        if not cron_expr:
            return "Erro: expressão cron é obrigatória."

        # Fire event for the scheduler to pick up
        self.hass.bus.async_fire(
            "mordomo_ha_schedule_job",
            {
                "cron": cron_expr,
                "description": description,
                "commands": commands,
            },
        )

        return f"⏰ Tarefa agendada: '{description}' com cron '{cron_expr}'"

    async def _remove_job(self, cmd: dict) -> str:
        """Remove a scheduled job."""
        job_id = cmd.get("job_id", "")
        if not job_id:
            return "Erro: job_id é obrigatório."

        self.hass.bus.async_fire(
            "mordomo_ha_remove_job",
            {"job_id": job_id},
        )

        return f"🗑️ Tarefa '{job_id}' removida."

    async def _list_entities(self, cmd: dict) -> str:
        """List entities, optionally filtered."""
        domain = cmd.get("domain", "")
        search = cmd.get("search", "").lower()

        registry = er.async_get(self.hass)
        entities = []

        for entry in registry.entities.values():
            if domain and not entry.entity_id.startswith(f"{domain}."):
                continue
            if search and search not in entry.entity_id.lower():
                name = entry.name or entry.original_name or ""
                if search not in name.lower():
                    continue
            entities.append(entry.entity_id)

        if not entities:
            return "Nenhuma entidade encontrada com esses critérios."

        entities = entities[:30]  # Limit
        return "📋 Entidades:\n" + "\n".join(f"  • {e}" for e in sorted(entities))

    async def _get_area(self, cmd: dict) -> str:
        """Get detailed info about a specific area/room."""
        area_name = cmd.get("area", "")
        if not area_name:
            return "Erro: nome da divisão é obrigatório."
        return await self.home_awareness.get_area_context(area_name)

    async def _get_areas(self) -> str:
        """List all areas/rooms."""
        return await self.home_awareness.get_areas_list()

    async def _get_house_summary(self) -> str:
        """Get full house summary."""
        return await self.home_awareness.get_full_house_context()

    async def get_ha_context(self, areas: list[str] | None = None) -> str:
        """Get current HA context for the LLM - uses HomeAwareness for organized view."""
        return await self.home_awareness.get_summary_context()
