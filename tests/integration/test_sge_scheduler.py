"""Integration tests for SGE scheduler operations via real SSH + SGE."""

from __future__ import annotations

import asyncio

import pytest

from researchloop.clusters.ssh import SSHManager
from researchloop.core.config import ClusterConfig
from researchloop.schedulers.sge import SGEScheduler

pytestmark = pytest.mark.integration


@pytest.fixture
async def ssh(sge_cluster_config: ClusterConfig):
    """SSH connection to the SGE container."""
    mgr = SSHManager()
    conn = await mgr.get_connection(
        {
            "host": sge_cluster_config.host,
            "port": sge_cluster_config.port,
            "user": sge_cluster_config.user,
            "key_path": sge_cluster_config.key_path,
        }
    )
    yield conn
    await mgr.close_all()


@pytest.fixture
def scheduler() -> SGEScheduler:
    return SGEScheduler()


async def _wait_sge_job_done(ssh, job_id: str, timeout: int = 30) -> str:  # type: ignore[no-untyped-def]
    """Poll until the SGE job finishes."""
    was_seen = False
    for _ in range(timeout):
        stdout, _, rc = await ssh.run(f"qstat -j {job_id} 2>/dev/null")
        if rc == 0 and stdout.strip():
            was_seen = True
        else:
            # Job not in queue.
            if was_seen:
                return "completed"
            # Try qacct.
            acct_out, _, acct_rc = await ssh.run(f"qacct -j {job_id} 2>/dev/null")
            if acct_rc == 0 and acct_out.strip():
                for line in acct_out.splitlines():
                    if "exit_status" in line:
                        code = line.split()[-1].strip()
                        return "completed" if code == "0" else "failed"
                return "completed"
        await asyncio.sleep(1)
    return "completed" if was_seen else "timeout"


class TestSGESubmitAndStatus:
    async def test_submit_simple_job(self, ssh, scheduler):
        """Submit a trivial job and verify we get a job ID."""
        await ssh.run("mkdir -p /tmp/test-sge")
        await ssh.run(
            "cat > /tmp/test-sge/count.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "for i in $(seq 1 5); do echo $i; sleep 0.1; done\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-sge/count.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-sge/count.sh",
            job_name="test-count",
            working_dir="/tmp/test-sge",
        )
        assert job_id.isdigit()

    async def test_submit_and_wait_for_completion(self, ssh, scheduler):
        """Submit a fast job and verify it completes."""
        await ssh.run("mkdir -p /tmp/test-sge-complete")
        await ssh.run(
            "cat > /tmp/test-sge-complete/fast.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "echo done\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-sge-complete/fast.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-sge-complete/fast.sh",
            job_name="test-fast",
            working_dir="/tmp/test-sge-complete",
        )

        state = await _wait_sge_job_done(ssh, job_id)
        assert state == "completed"

    async def test_qstat_shows_running_job(self, ssh, scheduler):
        """qstat shows a running job's status correctly."""
        await ssh.run("mkdir -p /tmp/test-sge-qstat")
        await ssh.run(
            "cat > /tmp/test-sge-qstat/slow.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "sleep 30\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-sge-qstat/slow.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-sge-qstat/slow.sh",
            job_name="test-qstat",
            working_dir="/tmp/test-sge-qstat",
        )

        # Wait for it to start.
        status = "pending"
        for _ in range(15):
            status = await scheduler.status(ssh, job_id)
            if status == "running":
                break
            await asyncio.sleep(1)

        assert status in ("running", "pending")

        # Clean up.
        await scheduler.cancel(ssh, job_id)

    async def test_cancel_job(self, ssh, scheduler):
        """Cancel a job and verify it's gone."""
        await ssh.run("mkdir -p /tmp/test-sge-cancel")
        await ssh.run(
            "cat > /tmp/test-sge-cancel/slow.sh << 'SCRIPT'\n"
            "#!/bin/bash\n"
            "sleep 300\n"
            "SCRIPT"
        )
        await ssh.run("chmod +x /tmp/test-sge-cancel/slow.sh")

        job_id = await scheduler.submit(
            ssh,
            script="/tmp/test-sge-cancel/slow.sh",
            job_name="test-cancel",
            working_dir="/tmp/test-sge-cancel",
        )

        # Wait for it to start.
        for _ in range(15):
            status = await scheduler.status(ssh, job_id)
            if status == "running":
                break
            await asyncio.sleep(1)

        success = await scheduler.cancel(ssh, job_id)
        assert success is True

        # Job should no longer be in qstat.
        await asyncio.sleep(2)
        status = await scheduler.status(ssh, job_id)
        # After cancel, job is gone from qstat; qacct may show failed
        assert status in ("unknown", "failed", "completed")

    async def test_submit_invalid_script_fails(self, ssh, scheduler):
        """Submitting a non-existent script raises RuntimeError."""
        await ssh.run("mkdir -p /tmp/test-sge-invalid")
        with pytest.raises(RuntimeError, match="qsub failed"):
            await scheduler.submit(
                ssh,
                script="/tmp/test-sge-invalid/does_not_exist.sh",
                job_name="test-invalid",
                working_dir="/tmp/test-sge-invalid",
            )

    async def test_mock_claude_runs(self, ssh):
        """Verify the mock claude CLI is installed and works."""
        stdout, _, rc = await ssh.run("claude -p 'test' --output-format stream-json")
        assert rc == 0
        assert '"type"' in stdout
        assert '"result"' in stdout

    async def test_generate_script_format(self, scheduler):
        """Verify generate_script produces valid SGE directives."""
        script = scheduler.generate_script(
            command="echo hello",
            job_name="test-job",
            working_dir="/tmp/test",
            time_limit="2:00:00",
            env={"MY_VAR": "value"},
        )
        assert "#$ -N test-job" in script
        assert "#$ -l h_rt=2:00:00" in script
        assert "#$ -S /bin/bash" in script
        assert "export MY_VAR='value'" in script
        assert "echo hello" in script
