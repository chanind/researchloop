"""Integration tests for auto-loop advancement, sprint cancellation,
job configuration, and abandoned job detection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from researchloop.clusters.monitor import JobMonitor
from researchloop.clusters.ssh import SSHManager
from researchloop.core.config import ClusterConfig, Config, StudyConfig
from researchloop.db import queries
from researchloop.db.database import Database
from researchloop.schedulers.slurm import SlurmScheduler
from researchloop.sprints.auto_loop import AutoLoopController
from researchloop.sprints.manager import SprintManager
from researchloop.studies.manager import StudyManager

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------
# 1. Auto-Loop Sprint Advancement
# ------------------------------------------------------------------


class TestAutoLoopAdvancement:
    """Test auto-loop progression through multiple sprints."""

    async def test_on_sprint_complete_starts_next_sprint(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
        sprint_manager: SprintManager,
    ):
        """After the first sprint completes, on_sprint_complete() starts
        the next sprint: increments completed_count, creates a new sprint
        with loop_id, submits it to SLURM, and updates current_sprint_id."""
        ctrl = AutoLoopController(
            db=integration_db_with_study,
            sprint_manager=sprint_manager,
            config=integration_config,
        )

        loop_id = await ctrl.start("integration-study", count=3)
        loop = await queries.get_auto_loop(integration_db_with_study, loop_id)
        first_sprint_id = loop["current_sprint_id"]

        # Simulate the first sprint completing (webhook would do this
        # in production, but we do it manually for the test).
        await queries.update_sprint(
            integration_db_with_study,
            first_sprint_id,
            status="completed",
        )

        # Trigger the advancement callback.
        await ctrl.on_sprint_complete(first_sprint_id)

        # Verify loop state was updated.
        loop = await queries.get_auto_loop(integration_db_with_study, loop_id)
        assert loop["completed_count"] == 1
        assert loop["status"] == "running"

        # A new sprint should have been created and submitted.
        second_sprint_id = loop["current_sprint_id"]
        assert second_sprint_id is not None
        assert second_sprint_id != first_sprint_id

        second_sprint = await queries.get_sprint(
            integration_db_with_study, second_sprint_id
        )
        assert second_sprint is not None
        assert second_sprint["loop_id"] == loop_id
        assert second_sprint["idea"] is None  # auto-generated on cluster
        assert second_sprint["job_id"] is not None  # submitted to SLURM
        assert second_sprint["status"] == "submitted"

    async def test_loop_completes_after_final_sprint(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
        sprint_manager: SprintManager,
    ):
        """A loop with count=1 completes after the first (and only) sprint
        finishes: loop status -> 'completed', stopped_at set, no new sprint."""
        ctrl = AutoLoopController(
            db=integration_db_with_study,
            sprint_manager=sprint_manager,
            config=integration_config,
        )

        loop_id = await ctrl.start("integration-study", count=1)
        loop = await queries.get_auto_loop(integration_db_with_study, loop_id)
        sprint_id = loop["current_sprint_id"]

        # Simulate completion.
        await queries.update_sprint(
            integration_db_with_study, sprint_id, status="completed"
        )

        # Count sprints before the callback.
        sprints_before = await queries.list_sprints(
            integration_db_with_study, study_name="integration-study"
        )
        count_before = len(sprints_before)

        await ctrl.on_sprint_complete(sprint_id)

        # Loop should be completed.
        loop = await queries.get_auto_loop(integration_db_with_study, loop_id)
        assert loop["status"] == "completed"
        assert loop["stopped_at"] is not None
        assert loop["completed_count"] == 1

        # No new sprint should have been created.
        sprints_after = await queries.list_sprints(
            integration_db_with_study, study_name="integration-study"
        )
        assert len(sprints_after) == count_before


# ------------------------------------------------------------------
# 2. Sprint Cancellation Scenarios
# ------------------------------------------------------------------


class TestSprintCancellation:
    """Test sprint cancellation under different conditions."""

    async def test_cancel_pending_sprint_no_job_id(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
        sprint_manager: SprintManager,
    ):
        """Cancelling a sprint that has never been submitted (no job_id)
        updates the DB status to cancelled without any SSH/scheduler calls."""
        sprint = await sprint_manager.create_sprint(
            "integration-study", "never submitted"
        )

        # Verify it has no job_id yet.
        row = await queries.get_sprint(integration_db_with_study, sprint.id)
        assert row is not None
        assert row["job_id"] is None
        assert row["status"] == "pending"

        success = await sprint_manager.cancel_sprint(sprint.id)
        assert success is True

        row = await queries.get_sprint(integration_db_with_study, sprint.id)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["completed_at"] is not None

    async def test_cancel_running_slurm_job(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
        sprint_manager: SprintManager,
    ):
        """Cancelling a submitted sprint sends scancel to SLURM and
        updates the DB. The SLURM job should be in CANCELLED state."""
        cluster = integration_config.clusters[0]
        ssh_mgr = SSHManager()

        try:
            sprint = await sprint_manager.create_sprint(
                "integration-study", "cancel me"
            )
            job_id = await sprint_manager.submit_sprint(sprint.id)
            assert job_id.isdigit()

            # Mark as running (simulating SLURM picking it up).
            await queries.update_sprint(
                integration_db_with_study, sprint.id, status="running"
            )

            # Cancel via sprint manager.
            success = await sprint_manager.cancel_sprint(sprint.id)
            assert success is True

            # DB should reflect cancellation.
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            assert row["status"] == "cancelled"
            assert row["completed_at"] is not None

            # Verify SLURM job state via scontrol.
            conn = await ssh_mgr.get_connection(
                {
                    "host": cluster.host,
                    "port": cluster.port,
                    "user": cluster.user,
                    "key_path": cluster.key_path,
                }
            )
            stdout, _, _ = await conn.run(f"scontrol show job {job_id} -o 2>/dev/null")
            match = re.search(r"JobState=(\S+)", stdout)
            if match:
                # Job may be CANCELLED or already cleaned up.
                assert match.group(1) in (
                    "CANCELLED",
                    "COMPLETED",
                    "FAILED",
                ), f"Unexpected SLURM state: {match.group(1)}"
        finally:
            await ssh_mgr.close_all()


# ------------------------------------------------------------------
# 3. Job Script Configuration
# ------------------------------------------------------------------


class TestJobScriptConfiguration:
    """Test that job scripts contain the expected configuration."""

    async def test_job_script_contains_environment_vars(
        self,
        integration_db_with_study: Database,
        slurm_cluster_config: ClusterConfig,
        tmp_path,
    ):
        """Environment variables from cluster config appear as
        'export KEY="value"' lines in the generated job script."""
        # Create a config with custom environment variables.
        cluster = ClusterConfig(
            name=slurm_cluster_config.name,
            host=slurm_cluster_config.host,
            port=slurm_cluster_config.port,
            user=slurm_cluster_config.user,
            key_path=slurm_cluster_config.key_path,
            scheduler_type=slurm_cluster_config.scheduler_type,
            working_dir=slurm_cluster_config.working_dir,
            environment={"MY_VAR": "test123", "ANOTHER_VAR": "hello_world"},
        )
        config = Config(
            studies=[
                StudyConfig(
                    name="integration-study",
                    cluster="test-slurm",
                    description="Integration test study",
                    sprints_dir="/tmp/researchloop/integration-study",
                    red_team_max_rounds=1,
                ),
            ],
            clusters=[cluster],
            db_path=":memory:",
            artifact_dir=str(tmp_path / "artifacts"),
            orchestrator_url="",
            claude_command="claude --dangerously-skip-permissions",
        )

        ssh_mgr = SSHManager()
        try:
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

            sprint = await sprint_mgr.create_sprint("integration-study", "env var test")
            await sprint_mgr.submit_sprint(sprint.id)

            # SSH in and read the generated job script.
            conn = await ssh_mgr.get_connection(
                {
                    "host": cluster.host,
                    "port": cluster.port,
                    "user": cluster.user,
                    "key_path": cluster.key_path,
                }
            )
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            base = config.studies[0].sprints_dir
            sprint_dir = row["directory"]
            script_out, _, rc = await conn.run(f"cat {base}/{sprint_dir}/run_sprint.sh")
            assert rc == 0

            assert 'export MY_VAR="test123"' in script_out
            assert 'export ANOTHER_VAR="hello_world"' in script_out
        finally:
            await ssh_mgr.close_all()

    async def test_job_script_contains_study_context(
        self,
        integration_db_with_study: Database,
        slurm_cluster_config: ClusterConfig,
        tmp_path,
    ):
        """When study.context is set, the orchestrator uploads CLAUDE.md
        to the sprint directory on the cluster containing that context."""
        config = Config(
            studies=[
                StudyConfig(
                    name="integration-study",
                    cluster="test-slurm",
                    description="Integration test study",
                    sprints_dir="/tmp/researchloop/integration-study",
                    red_team_max_rounds=1,
                    context="Test context for study",
                ),
            ],
            clusters=[slurm_cluster_config],
            db_path=":memory:",
            artifact_dir=str(tmp_path / "artifacts"),
            orchestrator_url="",
            claude_command="claude --dangerously-skip-permissions",
        )

        ssh_mgr = SSHManager()
        try:
            scheduler = SlurmScheduler()
            study_mgr = StudyManager(integration_db_with_study, config)
            sprint_mgr = SprintManager(
                db=integration_db_with_study,
                config=config,
                ssh_manager=ssh_mgr,
                schedulers={
                    slurm_cluster_config.name: scheduler,
                    slurm_cluster_config.scheduler_type: scheduler,
                },
                study_manager=study_mgr,
            )

            sprint = await sprint_mgr.create_sprint("integration-study", "context test")
            await sprint_mgr.submit_sprint(sprint.id)

            # SSH in and read CLAUDE.md on the cluster.
            conn = await ssh_mgr.get_connection(
                {
                    "host": slurm_cluster_config.host,
                    "port": slurm_cluster_config.port,
                    "user": slurm_cluster_config.user,
                    "key_path": slurm_cluster_config.key_path,
                }
            )
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            base = config.studies[0].sprints_dir
            sprint_dir = row["directory"]
            claude_md_out, _, rc = await conn.run(f"cat {base}/{sprint_dir}/CLAUDE.md")
            assert rc == 0
            assert "Test context for study" in claude_md_out
        finally:
            await ssh_mgr.close_all()

    async def test_job_script_has_correct_red_team_rounds(
        self,
        integration_db_with_study: Database,
        slurm_cluster_config: ClusterConfig,
        tmp_path,
    ):
        """The job script should contain prompt files for the configured
        number of red-team rounds and no more."""
        config = Config(
            studies=[
                StudyConfig(
                    name="integration-study",
                    cluster="test-slurm",
                    description="Integration test study",
                    sprints_dir="/tmp/researchloop/integration-study",
                    red_team_max_rounds=2,
                ),
            ],
            clusters=[slurm_cluster_config],
            db_path=":memory:",
            artifact_dir=str(tmp_path / "artifacts"),
            orchestrator_url="",
            claude_command="claude --dangerously-skip-permissions",
        )

        ssh_mgr = SSHManager()
        try:
            scheduler = SlurmScheduler()
            study_mgr = StudyManager(integration_db_with_study, config)
            sprint_mgr = SprintManager(
                db=integration_db_with_study,
                config=config,
                ssh_manager=ssh_mgr,
                schedulers={
                    slurm_cluster_config.name: scheduler,
                    slurm_cluster_config.scheduler_type: scheduler,
                },
                study_manager=study_mgr,
            )

            sprint = await sprint_mgr.create_sprint(
                "integration-study", "red team test"
            )
            await sprint_mgr.submit_sprint(sprint.id)

            # SSH in and read the job script.
            conn = await ssh_mgr.get_connection(
                {
                    "host": slurm_cluster_config.host,
                    "port": slurm_cluster_config.port,
                    "user": slurm_cluster_config.user,
                    "key_path": slurm_cluster_config.key_path,
                }
            )
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            base = config.studies[0].sprints_dir
            sprint_dir = row["directory"]
            script_out, _, rc = await conn.run(f"cat {base}/{sprint_dir}/run_sprint.sh")
            assert rc == 0

            # Rounds 1 and 2 should be present.
            assert "prompt_red_team_1.md" in script_out
            assert "prompt_red_team_2.md" in script_out
            assert "prompt_fix_1.md" in script_out
            assert "prompt_fix_2.md" in script_out

            # Round 3 should NOT be present.
            assert "prompt_red_team_3.md" not in script_out
            assert "prompt_fix_3.md" not in script_out

            # The RED_TEAM_ROUNDS variable should be set to 2.
            assert "RED_TEAM_ROUNDS=2" in script_out
        finally:
            await ssh_mgr.close_all()


# ------------------------------------------------------------------
# 4. Abandoned Job Detection
# ------------------------------------------------------------------


class TestAbandonedJobDetection:
    """Test the JobMonitor's abandoned job detection."""

    async def test_monitor_marks_job_abandoned_with_stale_heartbeat(
        self,
        integration_db_with_study: Database,
        integration_config: Config,
        sprint_manager: SprintManager,
    ):
        """A sprint with status='running', a stale heartbeat (>5 min old),
        and a job that no longer exists in the scheduler queue should be
        marked as 'failed' by poll_active_jobs()."""
        cluster = integration_config.clusters[0]
        scheduler = SlurmScheduler()
        ssh_mgr = SSHManager()

        try:
            # Create a sprint and mark it as running with a fake job_id
            # that doesn't correspond to any real SLURM job.
            sprint = await sprint_manager.create_sprint(
                "integration-study", "abandoned test"
            )

            # Set a stale heartbeat (10 minutes ago).
            stale_time = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            metadata = json.dumps({"last_heartbeat": stale_time})

            await queries.update_sprint(
                integration_db_with_study,
                sprint.id,
                status="running",
                job_id="999999",  # non-existent SLURM job
                metadata_json=metadata,
            )

            # Verify it shows up as an active sprint.
            active = await queries.get_active_sprints(integration_db_with_study)
            active_ids = [s["id"] for s in active]
            assert sprint.id in active_ids

            # Run the job monitor poll.
            monitor = JobMonitor(
                ssh_manager=ssh_mgr,
                db=integration_db_with_study,
                schedulers={
                    cluster.name: scheduler,
                    cluster.scheduler_type: scheduler,
                },
                config=integration_config,
            )
            await monitor.poll_active_jobs()

            # The sprint should now be marked as failed due to
            # stale heartbeat + unknown scheduler status.
            row = await queries.get_sprint(integration_db_with_study, sprint.id)
            assert row is not None
            assert row["status"] == "failed"
            assert row["completed_at"] is not None
        finally:
            await ssh_mgr.close_all()
