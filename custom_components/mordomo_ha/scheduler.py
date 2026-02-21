"""Cron Scheduler for Mordomo HA."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "mordomo_ha.scheduler"
STORAGE_VERSION = 1


class CronJob:
    """Represents a scheduled cron job."""

    def __init__(
        self,
        job_id: str,
        cron_expression: str,
        description: str,
        commands: list[dict],
        created_by: str = "",
        enabled: bool = True,
        one_shot: bool = False,
    ):
        self.job_id = job_id
        self.cron_expression = cron_expression
        self.description = description
        self.commands = commands
        self.created_by = created_by
        self.enabled = enabled
        self.one_shot = one_shot
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None
        self._cancel_callback: CALLBACK_TYPE | None = None

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "job_id": self.job_id,
            "cron_expression": self.cron_expression,
            "description": self.description,
            "commands": self.commands,
            "created_by": self.created_by,
            "enabled": self.enabled,
            "one_shot": self.one_shot,
            "last_run": self.last_run.isoformat() if self.last_run else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CronJob:
        """Deserialize from dict."""
        job = cls(
            job_id=data["job_id"],
            cron_expression=data["cron_expression"],
            description=data["description"],
            commands=data.get("commands", []),
            created_by=data.get("created_by", ""),
            enabled=data.get("enabled", True),
            one_shot=data.get("one_shot", False),
        )
        if data.get("last_run"):
            job.last_run = datetime.fromisoformat(data["last_run"])
        return job


class MordomoScheduler:
    """Manages cron jobs for Mordomo HA."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._jobs: dict[str, CronJob] = {}
        self._command_processor = None
        self._unsub_listeners: list[CALLBACK_TYPE] = []

    def set_command_processor(self, processor):
        """Set the command processor for executing jobs."""
        self._command_processor = processor

    async def async_load(self):
        """Load jobs from storage."""
        data = await self._store.async_load()
        if data and "jobs" in data:
            for job_data in data["jobs"]:
                try:
                    job = CronJob.from_dict(job_data)
                    self._jobs[job.job_id] = job
                    if job.enabled:
                        await self._schedule_next_run(job)
                except Exception as err:
                    _LOGGER.error("Failed to load job: %s", err)

        _LOGGER.info("Loaded %d scheduled jobs", len(self._jobs))

        # Listen for events - store removal callbacks for clean unload
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                "mordomo_ha_schedule_job", self._handle_schedule_event
            )
        )
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                "mordomo_ha_remove_job", self._handle_remove_event
            )
        )

    async def async_save(self):
        """Save jobs to storage."""
        data = {
            "jobs": [job.to_dict() for job in self._jobs.values()],
        }
        await self._store.async_save(data)

    async def async_unload(self):
        """Unload scheduler: cancel all timers and event listeners."""
        # Cancel event listeners
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

        # Cancel scheduled timers
        for job in self._jobs.values():
            if job._cancel_callback:
                job._cancel_callback()
                job._cancel_callback = None

    async def add_job(
        self,
        cron_expression: str,
        description: str,
        commands: list[dict],
        created_by: str = "",
        one_shot: bool = False,
    ) -> CronJob:
        """Add a new scheduled job."""
        job_id = str(uuid.uuid4())[:8]
        job = CronJob(
            job_id=job_id,
            cron_expression=cron_expression,
            description=description,
            commands=commands,
            created_by=created_by,
            one_shot=one_shot,
        )

        self._jobs[job_id] = job
        await self._schedule_next_run(job)
        await self.async_save()

        _LOGGER.info("Added job '%s': %s (%s)", job_id, description, cron_expression)
        return job

    async def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False

        if job._cancel_callback:
            job._cancel_callback()

        await self.async_save()
        _LOGGER.info("Removed job '%s'", job_id)
        return True

    def get_jobs(self) -> list[CronJob]:
        """Get all jobs."""
        return list(self._jobs.values())

    async def _schedule_next_run(self, job: CronJob):
        """Calculate and schedule the next run for a job."""
        try:
            from croniter import croniter

            now = dt_util.now()
            cron = croniter(job.cron_expression, now)
            next_time = cron.get_next(datetime)

            # Convert to HA timezone-aware
            if next_time.tzinfo is None:
                next_time = dt_util.as_local(next_time)

            job.next_run = next_time

            # Cancel previous schedule if any
            if job._cancel_callback:
                job._cancel_callback()

            # Schedule execution
            job._cancel_callback = async_track_point_in_time(
                self.hass,
                lambda now, j=job: self.hass.async_create_task(self._run_job(j)),
                next_time,
            )

            _LOGGER.debug(
                "Job '%s' scheduled for %s", job.job_id, next_time.isoformat()
            )

        except ImportError:
            _LOGGER.error("croniter not installed, using simple interval fallback")
            await self._schedule_simple_fallback(job)
        except Exception as err:
            _LOGGER.error("Failed to schedule job '%s': %s", job.job_id, err)

    async def _schedule_simple_fallback(self, job: CronJob):
        """Simple fallback scheduler when croniter is not available."""
        parts = job.cron_expression.split()
        if len(parts) != 5:
            _LOGGER.error("Invalid cron expression: %s", job.cron_expression)
            return

        # Very basic: schedule 1 hour from now as fallback
        next_time = dt_util.now() + timedelta(hours=1)
        job.next_run = next_time

        if job._cancel_callback:
            job._cancel_callback()

        job._cancel_callback = async_track_point_in_time(
            self.hass,
            lambda now, j=job: self.hass.async_create_task(self._run_job(j)),
            next_time,
        )

    async def _run_job(self, job: CronJob):
        """Execute a scheduled job."""
        _LOGGER.info("Running job '%s': %s", job.job_id, job.description)

        job.last_run = dt_util.now()

        if self._command_processor and job.commands:
            try:
                results = await self._command_processor.execute_commands(job.commands)
                _LOGGER.info("Job '%s' results: %s", job.job_id, results)

                self.hass.bus.async_fire(
                    "mordomo_ha_job_completed",
                    {
                        "job_id": job.job_id,
                        "description": job.description,
                        "results": results,
                    },
                )
            except Exception as err:
                _LOGGER.error("Job '%s' failed: %s", job.job_id, err)

        # Remove if one-shot, otherwise reschedule
        if job.one_shot:
            await self.remove_job(job.job_id)
        else:
            await self._schedule_next_run(job)
            await self.async_save()

    @callback
    def _handle_schedule_event(self, event):
        """Handle schedule job event."""
        data = event.data
        self.hass.async_create_task(
            self.add_job(
                cron_expression=data.get("cron", ""),
                description=data.get("description", ""),
                commands=data.get("commands", []),
                created_by=data.get("created_by", "whatsapp"),
                one_shot=data.get("one_shot", False),
            )
        )

    @callback
    def _handle_remove_event(self, event):
        """Handle remove job event."""
        job_id = event.data.get("job_id", "")
        if job_id:
            self.hass.async_create_task(self.remove_job(job_id))
