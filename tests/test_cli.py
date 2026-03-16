"""Tests for the researchloop CLI."""

from click.testing import CliRunner

from researchloop.cli import cli


class TestInit:
    def test_init_creates_config(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--path", str(tmp_path / "project")])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert (tmp_path / "project" / "researchloop.toml").exists()
        assert (tmp_path / "project" / "artifacts").is_dir()

    def test_init_existing_config_fails(self, tmp_path):
        # Create config first
        (tmp_path / "researchloop.toml").write_text("# existing")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "already exists" in result.output


class TestVersion:
    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestStudyCommands:
    def test_study_list(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(cli, ["-c", str(toml_config_file), "study", "list"])
        assert result.exit_code == 0
        assert "my-study" in result.output

    def test_study_show(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["-c", str(toml_config_file), "study", "show", "my-study"]
        )
        assert result.exit_code == 0
        assert "my-study" in result.output
        assert "local" in result.output

    def test_study_show_not_found(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["-c", str(toml_config_file), "study", "show", "nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestSprintCommands:
    def test_sprint_run(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "sprint",
                "run",
                "test idea",
                "-s",
                "my-study",
            ],
        )
        assert result.exit_code == 0
        assert "sp-" in result.output
        assert "test idea" in result.output

    def test_sprint_list(self, toml_config_file):
        runner = CliRunner()
        # Create a sprint first
        runner.invoke(
            cli,
            ["-c", str(toml_config_file), "sprint", "run", "idea", "-s", "my-study"],
        )
        result = runner.invoke(cli, ["-c", str(toml_config_file), "sprint", "list"])
        assert result.exit_code == 0
        assert "sp-" in result.output

    def test_sprint_show(self, toml_config_file):
        runner = CliRunner()
        # Create a sprint
        create_result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "sprint",
                "run",
                "idea",
                "-s",
                "my-study",
            ],
        )
        # Extract sprint ID from output (looks like "sp-xxxxxx")
        import re

        match = re.search(r"(sp-[0-9a-f]{6})", create_result.output)
        assert match, f"Could not find sprint ID in: {create_result.output}"
        sprint_id = match.group(1)

        result = runner.invoke(
            cli, ["-c", str(toml_config_file), "sprint", "show", sprint_id]
        )
        assert result.exit_code == 0
        assert sprint_id in result.output

    def test_sprint_cancel(self, toml_config_file):
        runner = CliRunner()
        create_result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "sprint",
                "run",
                "idea",
                "-s",
                "my-study",
            ],
        )
        import re

        match = re.search(r"(sp-[0-9a-f]{6})", create_result.output)
        sprint_id = match.group(1)

        result = runner.invoke(
            cli, ["-c", str(toml_config_file), "sprint", "cancel", sprint_id]
        )
        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_sprint_cancel_already_cancelled(self, toml_config_file):
        runner = CliRunner()
        create_result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "sprint",
                "run",
                "idea",
                "-s",
                "my-study",
            ],
        )
        import re

        match = re.search(r"(sp-[0-9a-f]{6})", create_result.output)
        sprint_id = match.group(1)

        # Cancel once
        runner.invoke(cli, ["-c", str(toml_config_file), "sprint", "cancel", sprint_id])
        # Try to cancel again
        result = runner.invoke(
            cli, ["-c", str(toml_config_file), "sprint", "cancel", sprint_id]
        )
        assert result.exit_code != 0
        assert "cannot cancel" in result.output.lower()

    def test_sprint_run_unknown_study(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "sprint",
                "run",
                "idea",
                "-s",
                "nonexistent",
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestClusterCommands:
    def test_cluster_list(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(cli, ["-c", str(toml_config_file), "cluster", "list"])
        assert result.exit_code == 0
        assert "local" in result.output


class TestLoopCommands:
    def test_loop_start(self, toml_config_file):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "-c",
                str(toml_config_file),
                "loop",
                "start",
                "-s",
                "my-study",
                "-n",
                "3",
            ],
        )
        assert result.exit_code == 0
        assert "loop-" in result.output

    def test_loop_status(self, toml_config_file):
        runner = CliRunner()
        runner.invoke(
            cli, ["-c", str(toml_config_file), "loop", "start", "-s", "my-study"]
        )
        result = runner.invoke(cli, ["-c", str(toml_config_file), "loop", "status"])
        assert result.exit_code == 0
        assert "loop-" in result.output
