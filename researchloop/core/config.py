"""Configuration loading for researchloop."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError as exc:
        raise ImportError(
            "tomli is required for Python < 3.11: pip install tomli"
        ) from exc

CONFIG_FILENAMES = ["researchloop.toml"]
CONFIG_SEARCH_PATHS = [
    Path.cwd(),
    Path.home() / ".config" / "researchloop",
]


@dataclass
class ClusterConfig:
    """Configuration for a compute cluster."""

    name: str
    host: str
    port: int = 22
    user: str = ""
    key_path: str = ""
    scheduler_type: str = "slurm"  # "slurm", "sge", "local"
    working_dir: str = ""
    max_concurrent_jobs: int = 4
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class StudyConfig:
    """Configuration for a research study."""

    name: str
    cluster: str
    claude_md_path: str = ""
    sprints_dir: str = ""
    max_sprint_duration_hours: int = 8
    red_team_max_rounds: int = 3
    description: str = ""


@dataclass
class SlackConfig:
    """Slack notification settings."""

    bot_token: str = ""
    signing_secret: str = ""
    channel_id: str | None = None


@dataclass
class NtfyConfig:
    """Ntfy notification settings."""

    url: str = "https://ntfy.sh"
    topic: str = ""


@dataclass
class DashboardConfig:
    """Dashboard web UI settings."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    password_hash: str | None = None


@dataclass
class Config:
    """Top-level researchloop configuration."""

    studies: list[StudyConfig] = field(default_factory=list)
    clusters: list[ClusterConfig] = field(default_factory=list)
    slack: SlackConfig | None = None
    ntfy: NtfyConfig | None = None
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    db_path: str = "researchloop.db"
    artifact_dir: str = "artifacts"
    api_key: str | None = None
    orchestrator_url: str | None = None


def _parse_cluster(data: dict) -> ClusterConfig:
    return ClusterConfig(
        name=data["name"],
        host=data.get("host", ""),
        port=data.get("port", 22),
        user=data.get("user", ""),
        key_path=data.get("key_path", ""),
        scheduler_type=data.get("scheduler_type", "slurm"),
        working_dir=data.get("working_dir", ""),
        max_concurrent_jobs=data.get("max_concurrent_jobs", 4),
        environment=data.get("environment", {}),
    )


def _parse_study(data: dict) -> StudyConfig:
    return StudyConfig(
        name=data["name"],
        cluster=data.get("cluster", ""),
        claude_md_path=data.get("claude_md_path", ""),
        sprints_dir=data.get("sprints_dir", ""),
        max_sprint_duration_hours=data.get("max_sprint_duration_hours", 8),
        red_team_max_rounds=data.get("red_team_max_rounds", 3),
        description=data.get("description", ""),
    )


def _parse_config(data: dict) -> Config:
    clusters = [_parse_cluster(c) for c in data.get("cluster", [])]
    studies = [_parse_study(s) for s in data.get("study", [])]

    slack = None
    if "slack" in data:
        s = data["slack"]
        slack = SlackConfig(
            bot_token=s.get("bot_token", ""),
            signing_secret=s.get("signing_secret", ""),
            channel_id=s.get("channel_id"),
        )

    ntfy = None
    if "ntfy" in data:
        n = data["ntfy"]
        ntfy = NtfyConfig(
            url=n.get("url", "https://ntfy.sh"),
            topic=n.get("topic", ""),
        )

    dashboard_data = data.get("dashboard", {})
    dashboard = DashboardConfig(
        enabled=dashboard_data.get("enabled", True),
        host=dashboard_data.get("host", "0.0.0.0"),
        port=dashboard_data.get("port", 8080),
        password_hash=dashboard_data.get("password_hash"),
    )

    return Config(
        studies=studies,
        clusters=clusters,
        slack=slack,
        ntfy=ntfy,
        dashboard=dashboard,
        db_path=data.get("db_path", "researchloop.db"),
        artifact_dir=data.get("artifact_dir", "artifacts"),
        api_key=data.get("api_key"),
        orchestrator_url=data.get("orchestrator_url"),
    )


def load_config(path: str | None = None) -> Config:
    """Load configuration from a researchloop.toml file.

    Search order:
      1. Explicit ``path`` argument.
      2. ``researchloop.toml`` in the current working directory.
      3. ``~/.config/researchloop/researchloop.toml``.

    Returns a ``Config`` instance.  Raises ``FileNotFoundError`` if no
    configuration file can be located.
    """
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
    else:
        config_path = None
        for search_dir in CONFIG_SEARCH_PATHS:
            for filename in CONFIG_FILENAMES:
                candidate = search_dir / filename
                if candidate.exists():
                    config_path = candidate
                    break
            if config_path is not None:
                break

        if config_path is None:
            raise FileNotFoundError(
                "No researchloop.toml found. Searched: "
                + ", ".join(str(p) for p in CONFIG_SEARCH_PATHS)
            )

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    return _parse_config(data)
