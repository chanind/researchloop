"""Integration tests for the full sprint lifecycle via real SLURM."""

from __future__ import annotations

import asyncio
import re
import socket

import pytest
import uvicorn

from researchloop.clusters.ssh import SSHManager
from researchloop.core.config import Config
from researchloop.core.orchestrator import Orchestrator, create_app
from researchloop.db import queries
from researchloop.db.database import Database
from researchloop.schedulers.slurm import SlurmScheduler
from researchloop.sprints.manager import SprintManager
from researchloop.studies.manager import StudyManager

pytestmark = pytest.mark.integration


async def _wait_job_done(ssh, job_id: str, timeout: int = 30) -> str:  # type: ignore[no-untyped-def]
    """Poll scontrol until the job reaches a terminal state."""
    for _ in range(timeout):
        stdout, _, _ = await ssh.run(f"scontrol show job {job_id} -o 2>/dev/null")
        match = re.search(r"JobState=(\S+)", stdout)
        if match:
            state = match.group(1)
            if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
                return state
        await asyncio.sleep(1)
    return "TIMEOUT_POLLING"


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------
# Tier 2: Sprint submission through SprintManager
# ------------------------------------------------------------------


class TestSprintSubmission:
    """Test SprintManager submitting real jobs to SLURM via SSH."""

    async def test_submit_sprint_creates_job(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
    ):
        """submit_sprint() creates remote dirs, uploads script, gets a job ID."""
        ssh_mgr = SSHManager()
        try:
            cluster = integration_config.clusters[0]
            scheduler = SlurmScheduler()
            study_mgr = StudyManager(integration_db_with_study, integration_config)
            sprint_mgr = SprintManager(
                db=integration_db_with_study,
                config=integration_config,
                ssh_manager=ssh_mgr,
                schedulers={
                    cluster.name: scheduler,
                    cluster.scheduler_type: scheduler,
                },
                study_manager=study_mgr,
            )

            sprint = await sprint_mgr.create_sprint("integration-study", "count to ten")
            job_id = await sprint_mgr.submit_sprint(sprint.id)

            assert job_id.isdigit()

            # Verify DB was updated.
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            assert row["job_id"] == job_id
            assert row["status"] == "submitted"

            # Verify remote directory was created.
            conn = await ssh_mgr.get_connection(
                {
                    "host": cluster.host,
                    "port": cluster.port,
                    "user": cluster.user,
                    "key_path": cluster.key_path,
                }
            )
            sprint_dir = row["directory"]
            base = integration_config.studies[0].sprints_dir
            _, _, rc = await conn.run(f"test -d {base}/{sprint_dir}/.researchloop")
            assert rc == 0, "Sprint directory not created on cluster"

            # Verify job script was uploaded (prompts are embedded
            # as base64 inside the script, extracted at runtime).
            stdout, _, _ = await conn.run(
                f"test -f {base}/{sprint_dir}/run_sprint.sh && echo OK"
            )
            assert "OK" in stdout, "Job script not found on cluster"
        finally:
            await ssh_mgr.close_all()


# ------------------------------------------------------------------
# Tier 3: Full lifecycle with webhook
# ------------------------------------------------------------------


class TestFullLifecycle:
    """End-to-end: submit → SLURM runs mock claude → verify outputs."""

    async def test_sprint_job_runs_and_produces_output(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
    ):
        """Submit a sprint, let SLURM run it with mock claude, verify files."""
        ssh_mgr = SSHManager()
        try:
            cluster = integration_config.clusters[0]
            scheduler = SlurmScheduler()
            study_mgr = StudyManager(integration_db_with_study, integration_config)
            sprint_mgr = SprintManager(
                db=integration_db_with_study,
                config=integration_config,
                ssh_manager=ssh_mgr,
                schedulers={
                    cluster.name: scheduler,
                    cluster.scheduler_type: scheduler,
                },
                study_manager=study_mgr,
            )

            sprint = await sprint_mgr.create_sprint(
                "integration-study", "integration test"
            )
            job_id = await sprint_mgr.submit_sprint(sprint.id)
            assert job_id.isdigit()

            # Wait for the SLURM job to complete.
            conn = await ssh_mgr.get_connection(
                {
                    "host": cluster.host,
                    "port": cluster.port,
                    "user": cluster.user,
                    "key_path": cluster.key_path,
                }
            )
            state = await _wait_job_done(conn, job_id, timeout=60)
            assert state == "COMPLETED", f"Job did not complete: {state}"

            # Verify the mock claude produced output files.
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            sprint_dir = row["directory"]
            base = integration_config.studies[0].sprints_dir

            # Check sprint_log.txt was created with step entries.
            log_out, _, rc = await conn.run(f"cat {base}/{sprint_dir}/sprint_log.txt")
            assert rc == 0, "sprint_log.txt not found"
            assert ">>> Starting step: research" in log_out

            # Check summary.txt was created by mock claude.
            summary_out, _, rc = await conn.run(f"cat {base}/{sprint_dir}/summary.txt")
            assert rc == 0, "summary.txt not found"
            assert len(summary_out.strip()) > 0
        finally:
            await ssh_mgr.close_all()

    @pytest.mark.xfail(
        reason="Webhook unreliable under Docker emulation",
        strict=False,
    )
    @pytest.mark.timeout(120)
    async def test_sprint_completes_via_webhook(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
    ):
        """Submit a sprint with webhook, verify orchestrator gets notified."""
        server_port = _find_free_port()
        orchestrator_url = f"http://host.docker.internal:{server_port}"

        config = Config(
            studies=integration_config.studies,
            clusters=integration_config.clusters,
            db_path=":memory:",
            artifact_dir=integration_config.artifact_dir,
            orchestrator_url=orchestrator_url,
            claude_command=integration_config.claude_command,
        )

        orchestrator = Orchestrator(config)
        orchestrator.db = integration_db_with_study

        ssh_mgr = SSHManager()
        cluster = config.clusters[0]
        scheduler = SlurmScheduler()
        study_mgr = StudyManager(integration_db_with_study, config)
        sprint_mgr = SprintManager(
            db=integration_db_with_study,
            config=config,
            ssh_manager=ssh_mgr,
            schedulers={
                cluster.name: scheduler,
                cluster.scheduler_type: scheduler,
            },
            study_manager=study_mgr,
        )
        orchestrator.sprint_manager = sprint_mgr
        orchestrator.study_manager = study_mgr

        app = create_app(orchestrator)

        uv_config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=server_port,
            log_level="warning",
        )
        server = uvicorn.Server(uv_config)
        server_task = asyncio.create_task(server.serve())

        try:
            # Wait for server to start.
            for _ in range(20):
                try:
                    with socket.create_connection(
                        ("localhost", server_port), timeout=1
                    ):
                        break
                except OSError:
                    await asyncio.sleep(0.5)

            sprint = await sprint_mgr.create_sprint("integration-study", "webhook test")
            job_id = await sprint_mgr.submit_sprint(sprint.id)

            # Poll DB for webhook-driven status update.
            final_status = None
            for _ in range(60):
                row = await queries.get_sprint(integration_db_with_study, sprint.id)
                if row and row["status"] in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    final_status = row["status"]
                    break
                await asyncio.sleep(1)

            assert final_status == "completed", (
                f"Webhook not received. Status: {final_status}. Job ID: {job_id}"
            )

            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            assert row["completed_at"] is not None
        finally:
            server.should_exit = True
            await server_task
            await ssh_mgr.close_all()

    async def test_sprint_cancel_updates_db(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
    ):
        """Cancel a submitted sprint and verify the DB is updated."""
        ssh_mgr = SSHManager()
        try:
            cluster = integration_config.clusters[0]
            scheduler = SlurmScheduler()
            study_mgr = StudyManager(integration_db_with_study, integration_config)
            sprint_mgr = SprintManager(
                db=integration_db_with_study,
                config=integration_config,
                ssh_manager=ssh_mgr,
                schedulers={
                    cluster.name: scheduler,
                    cluster.scheduler_type: scheduler,
                },
                study_manager=study_mgr,
            )

            sprint = await sprint_mgr.create_sprint("integration-study", "cancel test")
            await sprint_mgr.submit_sprint(sprint.id)

            # Cancel via sprint manager.
            success = await sprint_mgr.cancel_sprint(sprint.id)
            assert success is True

            # Verify DB reflects cancellation.
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            assert row["status"] == "cancelled"
            assert row["completed_at"] is not None
        finally:
            await ssh_mgr.close_all()
