"""Tests for researchloop.core.config."""

import pytest

from researchloop.core.config import load_config


class TestLoadConfig:
    def test_loads_from_path(self, toml_config_file):
        config = load_config(str(toml_config_file))
        assert len(config.clusters) == 1
        assert config.clusters[0].name == "local"
        assert config.clusters[0].scheduler_type == "local"
        assert len(config.studies) == 1
        assert config.studies[0].name == "my-study"
        assert config.studies[0].cluster == "local"

    def test_ntfy_parsed(self, toml_config_file):
        config = load_config(str(toml_config_file))
        assert config.ntfy is not None
        assert config.ntfy.topic == "test-topic"

    def test_dashboard_parsed(self, toml_config_file):
        config = load_config(str(toml_config_file))
        assert config.dashboard.port == 9090
        assert config.dashboard.enabled is True

    def test_api_key_parsed(self, toml_config_file):
        config = load_config(str(toml_config_file))
        assert config.api_key == "test-key"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.toml"))

    def test_defaults(self, tmp_path):
        # Minimal valid TOML
        p = tmp_path / "researchloop.toml"
        p.write_text(
            '[[cluster]]\nname = "c1"\nhost = "h1"\n\n'
            '[[study]]\nname = "s1"\n'
            'cluster = "c1"\nsprints_dir = "./sp"\n'
        )
        config = load_config(str(p))
        assert config.db_path == "researchloop.db"
        assert config.artifact_dir == "artifacts"
        assert config.dashboard.host == "0.0.0.0"
        assert config.dashboard.port == 8080
        assert config.api_key is None
        assert config.slack is None
