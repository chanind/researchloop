"""Integration tests for SLURM scheduler operations via real SSH + SLURM."""

from __future__ import annotations

import asyncio
import re

import pytest

from researchloop.clusters.ssh import SSHManager
from researchloop.core.config import ClusterConfig
from researchloop.schedulers.slurm import SlurmScheduler

pytestmark = pytest.mark.integration


@pytest.fixture
async def ssh(slurm_cluster_config: ClusterConfig):
    """SSH connection to the SLURM container."""
    mgr = SSHManager()
    conn = await mgr.get_connection(
        {
            "host": slurm_cluster_config.host,
            "port": slurm_cluster_config.port,
            "user": slurm_cluster_config.user,
            "key_path": slurm_cluster_config.key_path,
        }
    )
    yield conn
    await mgr.close_all()


@pytest.fixture
def scheduler() -> SlurmScheduler:
    return SlurmScheduler()


async def _wait_job_done(ssh, job_id: str, timeout: int = 30) -> str:
    """Poll scontrol until the job reaches a terminal state.

    Returns the SLURM state string (e.g. 'COMPLETED', 'CANCELLED').
    sacct isn't available in the minimal test container, so we use
    scontrol which keeps completed jobs for MinJobAge seconds.
    """
    for _ in range(timeout):
        stdout, _, _ = await ssh.run(f"scontrol show job {job_id} -o 2>/dev/null")
        match = re.search(r"JobState=(\S+)", stdout)
        if match:
            state = match.group(1)
            if state in (
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "TIMEOUT",
                "NODE_FAIL",
            ):
                return state
        await asyncio.sleep(1)
    return "TIMEOUT_POLLING"


class TestSlurmSubmitAndStatus:
    async def test_submit_simple_job(self, ssh, scheduler):
        """Submit a trivial job and verify we get a job ID back."""
        await ssh.run("mkdir -p /tmp/test-slurm")
        await ssh.run(
            "cat > /tmp/test-slurm/count.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "for i in $(seq 1 10); do echo $i; sleep 0.1; done\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-slurm/count.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-slurm/count.sh",
            job_name="test-count",
            working_dir="/tmp/test-slurm",
        )
        assert job_id.isdigit()

    async def test_submit_and_wait_for_completion(self, ssh, scheduler):
        """Submit a fast job and verify it completes."""
        await ssh.run("mkdir -p /tmp/test-complete")
        await ssh.run(
            "cat > /tmp/test-complete/fast.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "echo done\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-complete/fast.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-complete/fast.sh",
            job_name="test-fast",
            working_dir="/tmp/test-complete",
        )

        state = await _wait_job_done(ssh, job_id)
        assert state == "COMPLETED"

    async def test_squeue_shows_running_job(self, ssh, scheduler):
        """squeue shows a running job's status correctly."""
        await ssh.run("mkdir -p /tmp/test-squeue")
        await ssh.run(
            "cat > /tmp/test-squeue/slow.sh << 'SCRIPT'\n#!/bin/bash\nsleep 60\nSCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-squeue/slow.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-squeue/slow.sh",
            job_name="test-squeue",
            working_dir="/tmp/test-squeue",
        )

        # Wait for it to start.
        status = "pending"
        for _ in range(15):
            status = await scheduler.status(ssh, job_id)
            if status == "running":
                break
            await asyncio.sleep(1)

        assert status == "running"

        # Clean up.
        await scheduler.cancel(ssh, job_id)

    async def test_cancel_running_job(self, ssh, scheduler):
        """Cancel a running job and verify it's killed."""
        await ssh.run("mkdir -p /tmp/test-cancel")
        await ssh.run(
            "cat > /tmp/test-cancel/slow.sh << 'SCRIPT'\n#!/bin/bash\nsleep 300\nSCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-cancel/slow.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-cancel/slow.sh",
            job_name="test-cancel",
            working_dir="/tmp/test-cancel",
        )

        # Wait for it to start.
        for _ in range(15):
            status = await scheduler.status(ssh, job_id)
            if status == "running":
                break
            await asyncio.sleep(1)

        success = await scheduler.cancel(ssh, job_id)
        assert success is True

        state = await _wait_job_done(ssh, job_id, timeout=10)
        assert state == "CANCELLED"

    async def test_submit_invalid_script_fails(self, ssh, scheduler):
        """Submitting a non-existent script raises RuntimeError."""
        await ssh.run("mkdir -p /tmp/test-invalid")
        with pytest.raises(RuntimeError, match="sbatch failed"):
            await scheduler.submit(
                ssh,
                script="/tmp/test-invalid/does_not_exist.sh",
                job_name="test-invalid",
                working_dir="/tmp/test-invalid",
            )

    async def test_mock_claude_runs(self, ssh):
        """Verify the mock claude CLI is installed and works."""
        stdout, _, rc = await ssh.run("claude -p 'test' --output-format stream-json")
        assert rc == 0
        assert '"type"' in stdout
        assert '"result"' in stdout
