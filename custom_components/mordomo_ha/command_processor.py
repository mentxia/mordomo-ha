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

        Supports:
        - Fenced code blocks: ```json { ... } ```
        - Bare fenced blocks:  ``` { ... } ```
        - Inline JSON objects at any nesting depth
        """
        commands = []
        clean_text = text
        seen_spans: list[tuple[int, int]] = []  # avoid double-processing

        # -- Pattern 1: fenced code blocks (handles nested structures) --
        fenced_pattern = re.compile(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```')
        for match in fenced_pattern.finditer(text):
            start, end = match.span()
            if any(s <= start < e for s, e in seen_spans):
                continue
            try:
                cmd = json.loads(match.group(1))
                if isinstance(cmd, dict) and "action" in cmd:
                    commands.append(cmd)
                    clean_text = clean_text.replace(match.group(0), "", 1)
                    seen_spans.append((start, end))
            except json.JSONDecodeError:
                pass

        # -- Pattern 2: balanced-brace JSON scanner (handles nested arrays/dicts) --
        # Walk the text character by character to find top-level { ... } objects.
        i = 0
        while i < len(text):
            if text[i] == '{':
                # Check if this position is already captured
                if any(s <= i < e for s, e in seen_spans):
                    i += 1
                    continue
                # Try to extract a balanced JSON object from position i
                depth = 0
                in_string = False
                escape_next = False
                j = i
                while j < len(text):
                    ch = text[j]
                    if escape_next:
                        escape_next = False
                    elif ch == '\\' and in_string:
                        escape_next = True
                    elif ch == '"':
                        in_string = not in_string
                    elif not in_string:
                        if ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                break
                    j += 1

                if depth == 0 and j < len(text):
                    candidate = text[i:j + 1]
                    try:
                        cmd = json.loads(candidate)
                        if isinstance(cmd, dict) and "action" in cmd:
                            # Only add if not already captured from fenced block
                            if cmd not in commands:
                                commands.append(cmd)
                                # Remove from clean_text
                                clean_text = clean_text.replace(candidate, "", 1)
                                seen_spans.append((i, j + 1))
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                else:
                    i += 1
            else:
                i += 1

        # Clean up extra whitespace left by removed blocks
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
                    result = f"Acao desconhecida: {action}"

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
            return "Erro: dominio e servico sao obrigatorios."

        service_data = {**data}
        if target:
            service_data.update(target)

        try:
            await self.hass.services.async_call(
                domain, service, service_data, blocking=True
            )
            entity = target.get("entity_id", "desconhecido")
            return f"OK: {domain}.{service} em {entity}"
        except Exception as err:
            return f"Erro ao executar {domain}.{service}: {err}"

    async def _get_state(self, cmd: dict) -> str:
        """Get state of an entity."""
        entity_id = cmd.get("entity_id", "")
        if not entity_id:
            return "Erro: entity_id e obrigatorio."

        state = self.hass.states.get(entity_id)
        if state is None:
            return f"Entidade '{entity_id}' nao encontrada."

        attrs = dict(state.attributes)
        friendly_name = attrs.pop("friendly_name", entity_id)
        unit = attrs.pop("unit_of_measurement", "")

        result = f"{friendly_name}: {state.state}"
        if unit:
            result += f" {unit}"

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
                    results.append(f"  - {name}: {state.state} {unit}".strip())
        elif domain_filter:
            all_states = self.hass.states.async_all(domain_filter)
            for state in all_states[:20]:
                name = state.attributes.get("friendly_name", state.entity_id)
                unit = state.attributes.get("unit_of_measurement", "")
                results.append(f"  - {name}: {state.state} {unit}".strip())

        if not results:
            return "Nenhuma entidade encontrada."

        return "Estados:\n" + "\n".join(results)

    async def _create_automation(self, cmd: dict) -> str:
        """Create a Home Assistant automation.

        Accepts the automation's action steps under either "automation_action"
        (preferred, avoids dict-key collision with the command "action") or
        the legacy "action" key when the LLM sends raw YAML-style JSON.
        """
        import uuid
        import os

        alias = cmd.get("alias", "Mordomo Automation")
        trigger = cmd.get("trigger", [])
        condition = cmd.get("condition", [])
        # Support both key names: "automation_action" (service) and "action" is
        # already consumed as the command verb, so LLM-generated payloads that
        # contain the HA action list should use "automation_action".
        # As a fallback, if the value of "action" is a list it's the HA actions.
        action_value = cmd.get("automation_action") or cmd.get("ha_action", [])
        if not action_value and isinstance(cmd.get("action"), list):
            action_value = cmd["action"]
        description_text = cmd.get("description", "Criada pelo Mordomo HA")
        mode = cmd.get("mode", "single")

        if not trigger or not action_value:
            return "Erro: trigger e automation_action sao obrigatorios para criar uma automacao."

        automation_config = {
            "alias": alias,
            "description": description_text,
            "trigger": trigger,
            "condition": condition,
            "action": action_value,
            "mode": mode,
        }

        try:
            config_result = await self._write_automation_config(automation_config)
            return f"Automacao '{alias}' criada com sucesso!\n{config_result}"
        except Exception as err:
            _LOGGER.error("Failed to create automation: %s", err)
            return f"Erro ao criar automacao: {err}"

    async def _write_automation_config(self, config: dict) -> str:
        """Write automation to automations.yaml and reload.

        File I/O is offloaded to the executor to avoid blocking the event loop.
        """
        import uuid

        automation_id = str(uuid.uuid4()).replace("-", "")[:12]
        config_path = self.hass.config.path("automations.yaml")

        def _sync_write() -> str:
            """Blocking file operations - runs in executor."""
            import yaml
            import os

            existing: list = []
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as fh:
                    content = yaml.safe_load(fh) or []
                    if isinstance(content, list):
                        existing = content

            new_automation = {"id": automation_id, **config}
            existing.append(new_automation)

            with open(config_path, "w", encoding="utf-8") as fh:
                yaml.dump(existing, fh, default_flow_style=False, allow_unicode=True)

            return automation_id

        try:
            aid = await self.hass.async_add_executor_job(_sync_write)
        except Exception as err:
            _LOGGER.error("Failed to write automation config: %s", err)
            return "Nota: nao foi possivel escrever o ficheiro de automacoes."

        # Reload automations AFTER writing (not before)
        try:
            await self.hass.services.async_call(
                "automation", "reload", blocking=True
            )
        except Exception as err:
            _LOGGER.warning("Automation reload failed: %s", err)
            return f"ID: {aid} (reload manual necessario)"

        return f"ID: {aid}"

    async def _schedule_job(self, cmd: dict) -> str:
        """Schedule a cron job (delegates to scheduler component)."""
        cron_expr = cmd.get("cron", "")
        description = cmd.get("description", "Tarefa agendada")
        commands = cmd.get("commands", [])

        if not cron_expr:
            return "Erro: expressao cron e obrigatoria."

        self.hass.bus.async_fire(
            "mordomo_ha_schedule_job",
            {
                "cron": cron_expr,
                "description": description,
                "commands": commands,
            },
        )

        return f"Tarefa agendada: '{description}' com cron '{cron_expr}'"

    async def _remove_job(self, cmd: dict) -> str:
        """Remove a scheduled job."""
        job_id = cmd.get("job_id", "")
        if not job_id:
            return "Erro: job_id e obrigatorio."

        self.hass.bus.async_fire(
            "mordomo_ha_remove_job",
            {"job_id": job_id},
        )

        return f"Tarefa '{job_id}' removida."

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
            return "Nenhuma entidade encontrada com esses criterios."

        entities = entities[:30]
        return "Entidades:\n" + "\n".join(f"  - {e}" for e in sorted(entities))

    async def _get_area(self, cmd: dict) -> str:
        """Get detailed info about a specific area/room."""
        area_name = cmd.get("area", "")
        if not area_name:
            return "Erro: nome da divisao e obrigatorio."
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
