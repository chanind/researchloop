"""Auto-loop controller -- manages multi-sprint automated research loops."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from researchloop.core.config import Config
    from researchloop.db.database import Database
    from researchloop.sprints.manager import SprintManager

from researchloop.db import queries

logger = logging.getLogger(__name__)


def _generate_loop_id() -> str:
    """Generate a short hex loop ID like ``loop-b4e1c9``."""
    return f"loop-{secrets.token_hex(3)}"


class AutoLoopController:
    """Controls automated multi-sprint research loops.

    Phase 1 implements ``start`` and ``stop`` fully.  The
    ``on_sprint_complete`` callback that chains sprints together by
    generating new ideas is stubbed for Phase 2.
    """

    def __init__(
        self,
        db: Database,
        sprint_manager: SprintManager,
        config: Config,
    ) -> None:
        self.db = db
        self.sprint_manager = sprint_manager
        self.config = config

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(self, study_name: str, count: int) -> str:
        """Start a new auto-loop for *study_name* with *count* sprints.

        Creates the auto-loop record in the database, kicks off the
        first sprint with a placeholder idea, and returns the loop ID.
        """
        loop_id = _generate_loop_id()

        await queries.create_auto_loop(
            self.db,
            id=loop_id,
            study_name=study_name,
            total_count=count,
        )

        logger.info(
            "Auto-loop %s started for study %r with %d sprints",
            loop_id,
            study_name,
            count,
        )

        # Kick off the first sprint with a placeholder idea.
        first_idea = f"Auto-loop {loop_id} sprint 1/{count}"
        sprint = await self.sprint_manager.run_sprint(study_name, first_idea)

        await queries.update_auto_loop(
            self.db,
            loop_id,
            current_sprint_id=sprint.id,
            status="running",
        )

        logger.info("Auto-loop %s: first sprint %s submitted", loop_id, sprint.id)

        return loop_id

    # ------------------------------------------------------------------
    # Sprint completion callback (Phase 2 stub)
    # ------------------------------------------------------------------

    async def on_sprint_complete(self, sprint_id: str) -> None:
        """Handle completion of a sprint that belongs to an auto-loop.

        In Phase 2 this will:
        1. Look up the auto-loop that owns this sprint.
        2. Increment ``completed_count``.
        3. Generate the next research idea (via LLM or heuristic).
        4. Start the next sprint.

        For now, it logs a placeholder message.
        """
        # Find auto-loops where this sprint is the current one.
        all_loops = await queries.list_auto_loops(self.db)
        parent_loop = None
        for loop in all_loops:
            if loop.get("current_sprint_id") == sprint_id:
                parent_loop = loop
                break

        if parent_loop is None:
            logger.debug("Sprint %s is not part of any auto-loop", sprint_id)
            return

        loop_id = parent_loop["id"]
        completed = parent_loop.get("completed_count", 0) + 1
        total = parent_loop["total_count"]

        await queries.update_auto_loop(
            self.db,
            loop_id,
            completed_count=completed,
        )

        if completed >= total:
            await queries.update_auto_loop(
                self.db,
                loop_id,
                status="completed",
                stopped_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.info(
                "Auto-loop %s completed (%d/%d sprints done)",
                loop_id,
                completed,
                total,
            )
            return

        # Phase 2: generate next idea and start next sprint.
        logger.info(
            "Auto-loop %s: sprint %s complete (%d/%d). "
            "Auto-loop idea generation not yet implemented.",
            loop_id,
            sprint_id,
            completed,
            total,
        )

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(self, loop_id: str) -> None:
        """Stop a running auto-loop.

        Marks the loop as ``stopped`` and cancels the current sprint if
        one is in progress.
        """
        loop = await queries.get_auto_loop(self.db, loop_id)
        if loop is None:
            raise ValueError(f"Auto-loop not found: {loop_id}")

        if loop["status"] not in ("running", "pending"):
            logger.warning(
                "Auto-loop %s is already in status %r, not stopping",
                loop_id,
                loop["status"],
            )
            return

        # Cancel the current sprint if one exists.
        current_sprint_id = loop.get("current_sprint_id")
        if current_sprint_id:
            try:
                await self.sprint_manager.cancel_sprint(current_sprint_id)
                logger.info(
                    "Auto-loop %s: cancelled current sprint %s",
                    loop_id,
                    current_sprint_id,
                )
            except Exception:
                logger.exception(
                    "Auto-loop %s: failed to cancel sprint %s",
                    loop_id,
                    current_sprint_id,
                )

        await queries.update_auto_loop(
            self.db,
            loop_id,
            status="stopped",
            stopped_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info("Auto-loop %s stopped", loop_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self, loop_id: str) -> dict:
        """Return the current status of an auto-loop.

        Raises :class:`ValueError` if the loop is not found.
        """
        loop = await queries.get_auto_loop(self.db, loop_id)
        if loop is None:
            raise ValueError(f"Auto-loop not found: {loop_id}")
        return loop
