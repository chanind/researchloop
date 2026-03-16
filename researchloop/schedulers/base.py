"""Abstract base class for job schedulers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseScheduler(ABC):
    """Interface that every scheduler backend must implement."""

    @abstractmethod
    async def submit(
        self,
        ssh: object,
        script: str,
        job_name: str,
        working_dir: str,
        env: dict[str, str] | None = None,
    ) -> str:
        """Submit a job and return the scheduler-assigned job ID."""

    @abstractmethod
    async def status(self, ssh: object, job_id: str) -> str:
        """Return the current status of a job.

        Must return one of:
        ``"pending"``, ``"running"``, ``"completed"``, ``"failed"``, ``"unknown"``.
        """

    @abstractmethod
    async def cancel(self, ssh: object, job_id: str) -> bool:
        """Cancel a job. Return ``True`` on success."""

    @abstractmethod
    def generate_script(
        self,
        command: str,
        job_name: str,
        working_dir: str,
        time_limit: str = "8:00:00",
        env: dict[str, str] | None = None,
    ) -> str:
        """Generate the contents of a submission script."""
