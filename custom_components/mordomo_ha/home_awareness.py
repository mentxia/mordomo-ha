"""Home Awareness for Mordomo HA - Complete house visibility organized by areas/rooms."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Domain categories for organized display
DOMAIN_CATEGORIES = {
    "iluminação": ["light"],
    "climatização": ["climate", "fan", "humidifier"],
    "sensores": ["sensor", "binary_sensor"],
    "interruptores": ["switch", "input_boolean"],
    "estores_e_portões": ["cover"],
    "fechaduras": ["lock"],
    "alarme": ["alarm_control_panel"],
    "media": ["media_player"],
    "câmaras": ["camera"],
    "aspiradores": ["vacuum"],
    "electrodomésticos": ["water_heater", "valve"],
    "automações": ["automation"],
    "scripts": ["script"],
    "outros": [],
}

# Sensor types that are most relevant for context
IMPORTANT_SENSOR_CLASSES = {
    "temperature", "humidity", "illuminance", "power", "energy",
    "battery", "motion", "door", "window", "occupancy", "presence",
    "gas", "smoke", "moisture", "co", "co2", "pm25", "pm10",
    "voltage", "current", "pressure",
}


class HomeAwareness:
    """Provides comprehensive home awareness organized by areas/floors."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._cache: dict[str, Any] = {}
        self._cache_time: datetime | None = None
        self._cache_ttl = timedelta(seconds=30)

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache_time:
            return False
        return dt_util.utcnow() - self._cache_time < self._cache_ttl

    def _get_registries(self):
        """Get all HA registries."""
        return {
            "areas": ar.async_get(self.hass),
            "devices": dr.async_get(self.hass),
            "entities": er.async_get(self.hass),
            "floors": fr.async_get(self.hass),
        }

    def _get_area_for_entity(
        self,
        entity_id: str,
        entity_reg: er.EntityRegistry,
        device_reg: dr.DeviceRegistry,
        area_reg: ar.AreaRegistry,
    ) -> tuple[str | None, str | None]:
        """Get area ID and name for an entity."""
        entry = entity_reg.async_get(entity_id)
        if not entry:
            return None, None

        # Entity-level area assignment
        if entry.area_id:
            area = area_reg.async_get_area(entry.area_id)
            return entry.area_id, area.name if area else None

        # Device-level area assignment
        if entry.device_id:
            device = device_reg.async_get(entry.device_id)
            if device and device.area_id:
                area = area_reg.async_get_area(device.area_id)
                return device.area_id, area.name if area else None

        return None, None

    def _get_floor_for_area(
        self,
        area_id: str,
        area_reg: ar.AreaRegistry,
        floor_reg: fr.FloorRegistry,
    ) -> tuple[str | None, str | None]:
        """Get floor ID and name for an area."""
        area = area_reg.async_get_area(area_id)
        if not area or not area.floor_id:
            return None, None

        floor = floor_reg.async_get_floor(area.floor_id)
        return area.floor_id, floor.name if floor else None

    def _format_state(self, state: State) -> dict[str, Any]:
        """Format an entity state into a readable dict."""
        attrs = state.attributes
        domain = state.entity_id.split(".")[0]

        info: dict[str, Any] = {
            "entity_id": state.entity_id,
            "name": attrs.get("friendly_name", state.entity_id),
            "state": state.state,
        }

        # Add relevant attributes based on domain
        if domain == "light":
            if state.state == "on":
                if "brightness" in attrs:
                    info["brightness"] = round(attrs["brightness"] / 255 * 100)
                if "color_temp_kelvin" in attrs:
                    info["color_temp_kelvin"] = attrs["color_temp_kelvin"]
                if "rgb_color" in attrs:
                    info["rgb_color"] = attrs["rgb_color"]

        elif domain == "climate":
            info["current_temp"] = attrs.get("current_temperature")
            info["target_temp"] = attrs.get("temperature")
            info["hvac_mode"] = state.state
            info["hvac_action"] = attrs.get("hvac_action")
            if "humidity" in attrs:
                info["humidity"] = attrs["humidity"]

        elif domain == "cover":
            if "current_position" in attrs:
                info["position"] = attrs["current_position"]

        elif domain == "media_player":
            if state.state not in ("off", "unavailable", "unknown"):
                info["media_title"] = attrs.get("media_title")
                info["media_artist"] = attrs.get("media_artist")
                info["source"] = attrs.get("source")
                info["volume"] = attrs.get("volume_level")

        elif domain == "sensor":
            unit = attrs.get("unit_of_measurement", "")
            if unit:
                info["unit"] = unit
            device_class = attrs.get("device_class", "")
            if device_class:
                info["device_class"] = device_class

        elif domain == "binary_sensor":
            device_class = attrs.get("device_class", "")
            if device_class:
                info["device_class"] = device_class

        elif domain == "lock":
            pass  # state is enough

        elif domain == "alarm_control_panel":
            info["code_arm_required"] = attrs.get("code_arm_required")

        elif domain == "vacuum":
            info["battery"] = attrs.get("battery_level")
            info["status"] = attrs.get("status")

        elif domain == "fan":
            if state.state == "on":
                info["speed"] = attrs.get("percentage")
                info["preset_mode"] = attrs.get("preset_mode")

        # Remove None values
        return {k: v for k, v in info.items() if v is not None}

    async def get_full_house_context(self) -> str:
        """Get complete house context organized by floors and areas."""
        if self._is_cache_valid() and "full_context" in self._cache:
            return self._cache["full_context"]

        regs = self._get_registries()
        area_reg = regs["areas"]
        device_reg = regs["devices"]
        entity_reg = regs["entities"]
        floor_reg = regs["floors"]

        # Build structure: floor -> area -> category -> entities
        house: dict[str, dict[str, dict[str, list]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        unassigned: dict[str, list] = defaultdict(list)

        # Get all states
        all_states = self.hass.states.async_all()

        for state in all_states:
            if state.state in ("unavailable", "unknown"):
                continue

            domain = state.entity_id.split(".")[0]

            # Skip less relevant domains
            skip_domains = {
                "persistent_notification", "zone", "sun", "weather",
                "update", "button", "number", "select", "text",
                "input_number", "input_select", "input_text", "input_datetime",
                "scene", "group", "device_tracker", "person", "tts",
                "stt", "conversation", "tag",
            }
            if domain in skip_domains:
                continue

            # For sensors, filter to important ones
            if domain == "sensor":
                device_class = state.attributes.get("device_class", "")
                # Skip sensors without device_class or with unimportant classes
                if device_class and device_class not in IMPORTANT_SENSOR_CLASSES:
                    continue
                # Skip if no unit and no device class (likely non-numeric)
                if not device_class and not state.attributes.get("unit_of_measurement"):
                    continue

            # Get area
            area_id, area_name = self._get_area_for_entity(
                state.entity_id, entity_reg, device_reg, area_reg
            )

            # Determine category
            category = "outros"
            for cat, domains in DOMAIN_CATEGORIES.items():
                if domain in domains:
                    category = cat
                    break

            formatted = self._format_state(state)

            if area_id and area_name:
                # Get floor
                floor_id, floor_name = self._get_floor_for_area(
                    area_id, area_reg, floor_reg
                )
                floor_label = floor_name or "Sem Piso"
                house[floor_label][area_name][category].append(formatted)
            else:
                unassigned[category].append(formatted)

        # Build text output
        output = self._build_context_text(house, unassigned)

        # Cache it
        self._cache["full_context"] = output
        self._cache_time = dt_util.utcnow()

        return output

    def _build_context_text(
        self,
        house: dict[str, dict[str, dict[str, list]]],
        unassigned: dict[str, list],
    ) -> str:
        """Build the context text from the organized structure."""
        parts = []

        # Summary first
        total_entities = sum(
            len(entities)
            for floor in house.values()
            for area in floor.values()
            for entities in area.values()
        ) + sum(len(entities) for entities in unassigned.values())

        total_areas = sum(len(areas) for areas in house.values())
        total_floors = len([f for f in house if f != "Sem Piso"])

        parts.append(f"## 🏠 Visão Geral da Casa")
        parts.append(f"Pisos: {total_floors} | Divisões: {total_areas} | Dispositivos ativos: {total_entities}")
        parts.append("")

        # Quick status summary
        summary = self._build_quick_summary(house, unassigned)
        if summary:
            parts.append(summary)
            parts.append("")

        # Detailed by floor and area
        for floor_name in sorted(house.keys(), key=lambda x: (x == "Sem Piso", x)):
            areas = house[floor_name]
            if floor_name != "Sem Piso":
                parts.append(f"### 🏢 {floor_name}")
            else:
                parts.append(f"### 📦 Sem Piso Atribuído")

            for area_name in sorted(areas.keys()):
                categories = areas[area_name]
                parts.append(f"\n#### 🚪 {area_name}")

                for category in DOMAIN_CATEGORIES:
                    if category not in categories:
                        continue
                    entities = categories[category]
                    if not entities:
                        continue

                    parts.append(f"  **{category.capitalize()}:**")
                    for entity in entities:
                        line = self._entity_to_line(entity)
                        parts.append(f"    - {line}")

        # Unassigned entities
        if unassigned:
            unassigned_count = sum(len(e) for e in unassigned.values())
            if unassigned_count > 0:
                parts.append(f"\n### 📦 Sem Divisão Atribuída ({unassigned_count} dispositivos)")
                for category, entities in unassigned.items():
                    if not entities:
                        continue
                    parts.append(f"  **{category.capitalize()}:**")
                    for entity in entities[:10]:  # Limit unassigned
                        line = self._entity_to_line(entity)
                        parts.append(f"    - {line}")
                    if len(entities) > 10:
                        parts.append(f"    ... e mais {len(entities) - 10}")

        return "\n".join(parts)

    def _build_quick_summary(
        self,
        house: dict,
        unassigned: dict,
    ) -> str:
        """Build a quick summary of the house state."""
        lights_on = 0
        lights_total = 0
        temps = []
        open_covers = 0
        active_media = 0
        open_doors = 0
        open_windows = 0
        motion_detected = []
        climate_active = 0
        alarms = []

        all_entities = []
        for floor in house.values():
            for area_name, categories in floor.items():
                for entities in categories.values():
                    for e in entities:
                        all_entities.append({**e, "_area": area_name})
        for entities in unassigned.values():
            all_entities.extend(entities)

        for entity in all_entities:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "")
            domain = eid.split(".")[0]
            device_class = entity.get("device_class", "")
            area = entity.get("_area", "")

            if domain == "light":
                lights_total += 1
                if state == "on":
                    lights_on += 1

            elif domain == "sensor" and device_class == "temperature":
                try:
                    val = float(state)
                    name = entity.get("name", eid)
                    temps.append((area or name, val, entity.get("unit", "°C")))
                except (ValueError, TypeError):
                    pass

            elif domain == "cover":
                if state == "open":
                    open_covers += 1

            elif domain == "media_player" and state == "playing":
                active_media += 1

            elif domain == "binary_sensor":
                if device_class == "door" and state == "on":
                    open_doors += 1
                elif device_class == "window" and state == "on":
                    open_windows += 1
                elif device_class == "motion" and state == "on":
                    motion_detected.append(area or entity.get("name", ""))

            elif domain == "climate" and state not in ("off", "unavailable"):
                climate_active += 1

            elif domain == "alarm_control_panel":
                alarms.append(state)

        summary_lines = ["**📊 Resumo Rápido:**"]

        # Lights
        summary_lines.append(f"  💡 Luzes: {lights_on}/{lights_total} ligadas")

        # Temperatures
        if temps:
            temp_parts = [f"{area}: {val}{unit}" for area, val, unit in temps[:8]]
            summary_lines.append(f"  🌡️ Temperaturas: {', '.join(temp_parts)}")

        # Climate
        if climate_active:
            summary_lines.append(f"  ❄️ Climatização: {climate_active} equipamento(s) ativo(s)")

        # Covers
        if open_covers:
            summary_lines.append(f"  🪟 Estores/Portões abertos: {open_covers}")

        # Security
        security_parts = []
        if open_doors:
            security_parts.append(f"{open_doors} porta(s) aberta(s)")
        if open_windows:
            security_parts.append(f"{open_windows} janela(s) aberta(s)")
        if motion_detected:
            areas_with_motion = list(set(motion_detected))[:5]
            security_parts.append(f"movimento em: {', '.join(areas_with_motion)}")
        if alarms:
            security_parts.append(f"alarme: {', '.join(set(alarms))}")
        if security_parts:
            summary_lines.append(f"  🔒 Segurança: {'; '.join(security_parts)}")

        # Media
        if active_media:
            summary_lines.append(f"  🎵 Media: {active_media} a reproduzir")

        return "\n".join(summary_lines)

    def _entity_to_line(self, entity: dict) -> str:
        """Convert an entity dict to a readable line."""
        name = entity.get("name", entity.get("entity_id", "?"))
        state = entity.get("state", "?")
        eid = entity.get("entity_id", "")
        domain = eid.split(".")[0]

        parts = [f"{name}"]

        if domain == "light":
            if state == "on":
                brightness = entity.get("brightness")
                parts.append(f"💡 ON" + (f" ({brightness}%)" if brightness else ""))
            else:
                parts.append("OFF")

        elif domain == "climate":
            current = entity.get("current_temp")
            target = entity.get("target_temp")
            action = entity.get("hvac_action", "")
            parts.append(f"{state}")
            if current:
                parts.append(f"atual: {current}°C")
            if target:
                parts.append(f"alvo: {target}°C")
            if action:
                parts.append(f"({action})")

        elif domain == "sensor":
            unit = entity.get("unit", "")
            parts.append(f"{state} {unit}".strip())

        elif domain == "binary_sensor":
            device_class = entity.get("device_class", "")
            label_map = {
                "door": ("🚪 Aberta", "🚪 Fechada"),
                "window": ("🪟 Aberta", "🪟 Fechada"),
                "motion": ("🏃 Movimento", "✨ Sem movimento"),
                "occupancy": ("👤 Ocupado", "Desocupado"),
                "smoke": ("🚨 FUMO!", "OK"),
                "gas": ("🚨 GÁS!", "OK"),
                "moisture": ("💧 Húmido", "Seco"),
                "lock": ("🔓 Destrancada", "🔒 Trancada"),
            }
            if device_class in label_map:
                label = label_map[device_class][0 if state == "on" else 1]
                parts.append(label)
            else:
                parts.append("ON" if state == "on" else "OFF")

        elif domain == "cover":
            position = entity.get("position")
            if position is not None:
                parts.append(f"{state} ({position}%)")
            else:
                parts.append(state)

        elif domain == "lock":
            parts.append("🔒 Trancada" if state == "locked" else "🔓 Destrancada")

        elif domain == "media_player":
            if state == "playing":
                title = entity.get("media_title", "")
                artist = entity.get("media_artist", "")
                if title:
                    parts.append(f"▶️ {title}")
                    if artist:
                        parts.append(f"por {artist}")
                else:
                    parts.append("▶️ A reproduzir")
            else:
                parts.append(state)

        elif domain == "vacuum":
            battery = entity.get("battery")
            status = entity.get("status", state)
            parts.append(f"{status}")
            if battery:
                parts.append(f"🔋 {battery}%")

        elif domain == "alarm_control_panel":
            alarm_labels = {
                "armed_home": "🟢 Armado (casa)",
                "armed_away": "🔴 Armado (fora)",
                "armed_night": "🟡 Armado (noite)",
                "disarmed": "⚪ Desarmado",
                "triggered": "🚨 DISPARADO!",
                "arming": "⏳ A armar...",
                "pending": "⏳ Pendente...",
            }
            parts.append(alarm_labels.get(state, state))

        else:
            parts.append(state)

        parts.append(f"[{eid}]")
        return " | ".join(parts)

    async def get_area_context(self, area_name: str) -> str:
        """Get context for a specific area/room."""
        regs = self._get_registries()
        area_reg = regs["areas"]
        device_reg = regs["devices"]
        entity_reg = regs["entities"]

        # Find the area
        target_area = None
        for area in area_reg.async_list_areas():
            if area.name.lower() == area_name.lower():
                target_area = area
                break

        if not target_area:
            # Try fuzzy match
            for area in area_reg.async_list_areas():
                if area_name.lower() in area.name.lower():
                    target_area = area
                    break

        if not target_area:
            return f"Divisão '{area_name}' não encontrada. Divisões disponíveis: {', '.join(a.name for a in area_reg.async_list_areas())}"

        # Get all entities in this area
        entities_in_area = []
        all_states = self.hass.states.async_all()

        for state in all_states:
            if state.state in ("unavailable", "unknown"):
                continue

            area_id, _ = self._get_area_for_entity(
                state.entity_id, entity_reg, device_reg, area_reg
            )
            if area_id == target_area.id:
                entities_in_area.append(self._format_state(state))

        if not entities_in_area:
            return f"Divisão '{target_area.name}' não tem dispositivos ativos."

        parts = [f"#### 🚪 {target_area.name} ({len(entities_in_area)} dispositivos)"]
        for entity in entities_in_area:
            line = self._entity_to_line(entity)
            parts.append(f"  - {line}")

        return "\n".join(parts)

    async def get_areas_list(self) -> str:
        """Get a simple list of all areas."""
        regs = self._get_registries()
        area_reg = regs["areas"]
        floor_reg = regs["floors"]

        floors_areas: dict[str, list[str]] = defaultdict(list)

        for area in area_reg.async_list_areas():
            floor_id, floor_name = self._get_floor_for_area(
                area.id, area_reg, floor_reg
            )
            floor_label = floor_name or "Sem Piso"
            floors_areas[floor_label].append(area.name)

        if not floors_areas:
            return "Nenhuma divisão configurada no Home Assistant."

        parts = ["🏠 **Divisões da Casa:**"]
        for floor_name in sorted(floors_areas.keys(), key=lambda x: (x == "Sem Piso", x)):
            parts.append(f"\n  🏢 {floor_name}:")
            for area in sorted(floors_areas[floor_name]):
                parts.append(f"    - {area}")

        return "\n".join(parts)

    async def get_summary_context(self) -> str:
        """Get a compact summary suitable for every LLM call (token-efficient)."""
        if self._is_cache_valid() and "summary" in self._cache:
            return self._cache["summary"]

        regs = self._get_registries()
        area_reg = regs["areas"]
        device_reg = regs["devices"]
        entity_reg = regs["entities"]

        # Build area -> key states mapping
        area_states: dict[str, list[str]] = defaultdict(list)
        no_area_states: list[str] = []

        all_states = self.hass.states.async_all()

        for state in all_states:
            if state.state in ("unavailable", "unknown"):
                continue

            domain = state.entity_id.split(".")[0]

            # Only include the most relevant domains for the summary
            if domain not in (
                "light", "climate", "cover", "lock", "alarm_control_panel",
                "sensor", "binary_sensor", "media_player",
            ):
                continue

            # Filter sensors to important ones
            if domain == "sensor":
                dc = state.attributes.get("device_class", "")
                if dc not in ("temperature", "humidity", "power", "energy", "battery"):
                    continue

            if domain == "binary_sensor":
                dc = state.attributes.get("device_class", "")
                if dc not in ("door", "window", "motion", "occupancy", "smoke", "gas"):
                    continue

            # Get area
            area_id, area_name = self._get_area_for_entity(
                state.entity_id, entity_reg, device_reg, area_reg
            )

            formatted = self._format_state(state)
            line = self._entity_to_compact_line(formatted)

            if area_name:
                area_states[area_name].append(line)
            else:
                no_area_states.append(line)

        # Build compact output
        parts = ["## Casa - Estado Atual"]

        for area_name in sorted(area_states.keys()):
            entities = area_states[area_name]
            parts.append(f"\n**{area_name}:** {' | '.join(entities)}")

        if no_area_states:
            parts.append(f"\n**Outros:** {' | '.join(no_area_states[:15])}")

        result = "\n".join(parts)

        self._cache["summary"] = result
        self._cache_time = dt_util.utcnow()

        return result

    def _entity_to_compact_line(self, entity: dict) -> str:
        """Ultra-compact entity representation for token efficiency."""
        name = entity.get("name", "?")
        state = entity.get("state", "?")
        eid = entity.get("entity_id", "")
        domain = eid.split(".")[0]

        if domain == "light":
            b = entity.get("brightness", "")
            return f"{name}: {'ON' + (f' {b}%' if b else '') if state == 'on' else 'OFF'}"

        elif domain == "sensor":
            unit = entity.get("unit", "")
            return f"{name}: {state}{unit}"

        elif domain == "binary_sensor":
            dc = entity.get("device_class", "")
            if dc in ("door", "window"):
                return f"{name}: {'ABERTO' if state == 'on' else 'FECHADO'}"
            elif dc == "motion":
                return f"{name}: {'SIM' if state == 'on' else 'NÃO'}"
            return f"{name}: {'ON' if state == 'on' else 'OFF'}"

        elif domain == "climate":
            ct = entity.get("current_temp", "?")
            tt = entity.get("target_temp", "")
            return f"{name}: {state} {ct}°C" + (f"→{tt}°C" if tt else "")

        elif domain == "cover":
            pos = entity.get("position", "")
            return f"{name}: {state}" + (f" {pos}%" if pos else "")

        elif domain == "lock":
            return f"{name}: {'🔒' if state == 'locked' else '🔓'}"

        elif domain == "alarm_control_panel":
            return f"{name}: {state}"

        elif domain == "media_player":
            if state == "playing":
                title = entity.get("media_title", "")
                return f"{name}: ▶️ {title}" if title else f"{name}: ▶️"
            return f"{name}: {state}"

        return f"{name}: {state}"
