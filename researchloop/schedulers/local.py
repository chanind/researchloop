"""Local subprocess scheduler for testing without a real cluster."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import textwrap

from researchloop.schedulers.base import BaseScheduler

logger = logging.getLogger(__name__)


class LocalScheduler(BaseScheduler):
    """Scheduler that runs commands as local subprocesses.

    Intended for development and testing.  Job IDs are OS process IDs (PIDs)
    stored as strings for consistency with the ``BaseScheduler`` interface.
    The *ssh* parameter is accepted but ignored -- all commands run locally.
    """

    def __init__(self) -> None:
        # Track running processes keyed by PID string.
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    # ------------------------------------------------------------------
    # submit
    # ------------------------------------------------------------------

    async def submit(
        self,
        ssh: object,
        script: str,
        job_name: str,
        working_dir: str,
        env: dict[str, str] | None = None,
    ) -> str:
        """Run *script* as a local background subprocess.

        Returns the PID of the spawned process as a string.
        """
        # Build the environment for the subprocess.
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        # Ensure the working directory exists.
        os.makedirs(working_dir, exist_ok=True)

        # Write the script to a temporary file in working_dir so it can
        # be executed as a proper bash script.
        script_path = os.path.join(working_dir, f".researchloop_local_{job_name}.sh")
        with open(script_path, "w") as f:
            f.write(script)
        os.chmod(script_path, 0o755)

        stdout_path = os.path.join(working_dir, f"{job_name}.out")
        stderr_path = os.path.join(working_dir, f"{job_name}.err")

        stdout_file = open(stdout_path, "w")
        stderr_file = open(stderr_path, "w")

        proc = await asyncio.create_subprocess_exec(
            "bash",
            script_path,
            cwd=working_dir,
            env=proc_env,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )

        stdout_file.close()
        stderr_file.close()

        job_id = str(proc.pid)
        self._processes[job_id] = proc

        logger.info(
            "Local job submitted: pid=%s name=%s dir=%s", job_id, job_name, working_dir
        )
        return job_id

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    async def status(self, ssh: object, job_id: str) -> str:
        """Check whether the process identified by *job_id* (PID) is alive."""
        proc = self._processes.get(job_id)

        if proc is not None:
            # We have a handle -- check if it has terminated.
            if proc.returncode is None:
                # Process still running (or hasn't been awaited yet).
                # Use a non-blocking poll.
                try:
                    # wait with timeout=0 raises TimeoutError if still running.
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    return "running"

            # Process has finished.
            if proc.returncode == 0:
                return "completed"
            return "failed"

        # No tracked handle -- try the OS.
        try:
            pid = int(job_id)
            os.kill(pid, 0)  # Signal 0: check existence without killing.
            return "running"
        except (OSError, ValueError):
            return "unknown"

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    async def cancel(self, ssh: object, job_id: str) -> bool:
        """Kill the local process identified by *job_id* (PID)."""
        proc = self._processes.get(job_id)

        if proc is not None:
            try:
                proc.terminate()
                # Give the process a moment to exit gracefully.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                logger.info("Cancelled local job pid=%s", job_id)
                return True
            except ProcessLookupError:
                logger.warning("Process pid=%s already exited", job_id)
                return True

        # Fall back to OS signal.
        try:
            pid = int(job_id)
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to pid=%s", job_id)
            return True
        except (OSError, ValueError) as exc:
            logger.error("Failed to cancel local job pid=%s: %s", job_id, exc)
            return False

    # ------------------------------------------------------------------
    # generate_script
    # ------------------------------------------------------------------

    def generate_script(
        self,
        command: str,
        job_name: str,
        working_dir: str,
        time_limit: str = "8:00:00",
        env: dict[str, str] | None = None,
    ) -> str:
        """Generate a simple bash script for local execution."""
        env_exports = ""
        if env:
            lines = [
                f"export {key}={_shell_quote(value)}" for key, value in env.items()
            ]
            env_exports = "\n".join(lines) + "\n"

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail

            echo "=== Local job '{job_name}' started at $(date -u) ==="
            echo "Host: $(hostname)"
            echo "Working directory: $(pwd)"

            {env_exports}# --- Run the command ---
            {command}

            echo "=== Local job '{job_name}' finished at $(date -u) ==="
        """)
        return script


def _shell_quote(value: str) -> str:
    """Wrap *value* in single quotes, escaping embedded single quotes."""
    return "'" + value.replace("'", "'\\''") + "'"
