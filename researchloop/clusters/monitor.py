"""Job monitoring - polls active jobs via SSH and updates the database."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from researchloop.clusters.ssh import SSHManager
from researchloop.db import queries
from researchloop.schedulers.base import BaseScheduler

logger = logging.getLogger(__name__)

# If a job's heartbeat is older than this many seconds AND the job is not
# visible in the scheduler queue, consider it abandoned.
_HEARTBEAT_STALE_SECONDS = 5 * 60  # 5 minutes


class JobMonitor:
    """Monitors submitted jobs by periodically polling their status."""

    def __init__(
        self,
        ssh_manager: SSHManager,
        db: Any,
        schedulers: dict[str, BaseScheduler],
        config: Any = None,
    ) -> None:
        self.ssh_manager = ssh_manager
        self.db = db
        self.schedulers = schedulers
        self.config = config
        self._polling_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Single-job check
    # ------------------------------------------------------------------

    async def check_job(self, sprint: dict[str, Any]) -> str:
        """Check the status of the job for *sprint*.

        Returns the scheduler-normalised status string:
        ``pending``, ``running``, ``completed``, ``failed``, or ``unknown``.
        """
        job_id: str | None = sprint.get("job_id")
        if not job_id:
            logger.warning("Sprint %s has no job_id", sprint["id"])
            return "unknown"

        # Resolve scheduler for this sprint's study/cluster.
        study = await queries.get_study(self.db, sprint["study_name"])
        if study is None:
            return "unknown"

        cluster_name = study["cluster"]
        scheduler = self.schedulers.get(cluster_name)
        if scheduler is None:
            logger.debug("No scheduler for cluster %r", cluster_name)
            return "unknown"

        # Resolve cluster config for SSH connection.
        cluster_cfg = None
        if self.config:
            for c in self.config.clusters:
                if c.name == cluster_name:
                    cluster_cfg = c
                    break

        if cluster_cfg is None:
            logger.debug("No cluster config for %r", cluster_name)
            return "unknown"

        try:
            ssh = await self.ssh_manager.get_connection(
                {
                    "host": cluster_cfg.host,
                    "port": cluster_cfg.port,
                    "user": cluster_cfg.user,
                    "key_path": cluster_cfg.key_path,
                }
            )
            status = await scheduler.status(ssh, job_id)
        except Exception:
            logger.debug("SSH check failed for job %s", job_id, exc_info=True)
            return "unknown"

        logger.info("Sprint %s (job %s) status: %s", sprint["id"], job_id, status)
        return status

    # ------------------------------------------------------------------
    # Poll all active jobs
    # ------------------------------------------------------------------

    async def poll_active_jobs(self) -> None:
        """Check every active sprint, update the DB, and detect abandoned jobs."""
        sprints = await queries.get_active_sprints(self.db)
        if not sprints:
            logger.debug("No active sprints to poll.")
            return

        logger.info("Polling %d active sprint(s)...", len(sprints))
        now = datetime.now(timezone.utc)

        for sprint in sprints:
            sprint_id: str = sprint["id"]
            try:
                status = await self.check_job(sprint)
            except Exception:
                logger.exception("Error checking status for sprint %s", sprint_id)
                status = "unknown"

            # --- Abandoned-job detection ---
            if status == "unknown":
                # Check heartbeat from metadata_json
                metadata_str = sprint.get("metadata_json")
                heartbeat_str: str | None = None
                if metadata_str:
                    try:
                        metadata = json.loads(metadata_str)
                        heartbeat_str = metadata.get("last_heartbeat")
                    except (json.JSONDecodeError, TypeError):
                        pass

                if heartbeat_str is not None:
                    heartbeat = datetime.fromisoformat(heartbeat_str)
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)

                    stale_seconds = (now - heartbeat).total_seconds()
                    if stale_seconds > _HEARTBEAT_STALE_SECONDS:
                        logger.warning(
                            "Sprint %s appears abandoned: heartbeat %.0fs ago "
                            "and job not in scheduler queue. Marking as failed.",
                            sprint_id,
                            stale_seconds,
                        )
                        status = "failed"

            # Persist the updated status if it changed.
            if status in ("completed", "failed"):
                try:
                    await queries.update_sprint(
                        self.db,
                        sprint_id,
                        status=status,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception:
                    logger.exception(
                        "Failed to update DB status for sprint %s", sprint_id
                    )

    # ------------------------------------------------------------------
    # Background polling loop
    # ------------------------------------------------------------------

    async def start_polling(self, interval: int = 120) -> None:
        """Start a background task that polls active jobs every *interval* seconds."""
        if self._polling_task is not None and not self._polling_task.done():
            logger.warning("Polling is already running.")
            return

        self._stop_event.clear()
        self._polling_task = asyncio.create_task(
            self._poll_loop(interval), name="job-monitor-poll"
        )
        logger.info("Job monitor polling started (interval=%ds).", interval)

    async def _poll_loop(self, interval: int) -> None:
        """Internal loop executed by the background task."""
        while not self._stop_event.is_set():
            try:
                await self.poll_active_jobs()
            except Exception:
                logger.exception("Unhandled error during job polling")

            # Wait for the interval, but break early if stop is requested.
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                # If we reach here, stop was requested.
                break
            except asyncio.TimeoutError:
                # Normal timeout - continue polling.
                pass

    async def stop_polling(self) -> None:
        """Stop the background polling task."""
        self._stop_event.set()
        if self._polling_task is not None:
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            logger.info("Job monitor polling stopped.")
