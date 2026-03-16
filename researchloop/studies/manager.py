"""Study management -- syncs study configuration to the database."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from researchloop.core.config import ClusterConfig, Config
    from researchloop.db.database import Database

from researchloop.db import queries

logger = logging.getLogger(__name__)


class StudyManager:
    """High-level operations on research studies.

    Reads study definitions from :class:`Config` and persists them into
    the database so they can be referenced at runtime.
    """

    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config

    # ------------------------------------------------------------------
    # Sync from config
    # ------------------------------------------------------------------

    async def sync_from_config(self) -> None:
        """Upsert every study from the TOML config into the database.

        Studies that already exist are updated with the latest values;
        new studies are created.
        """
        for study_cfg in self.config.studies:
            existing = await queries.get_study(self.db, study_cfg.name)
            config_json = json.dumps(asdict(study_cfg))

            if existing is None:
                logger.info("Creating study %r in database", study_cfg.name)
                await queries.create_study(
                    self.db,
                    name=study_cfg.name,
                    cluster=study_cfg.cluster,
                    description=study_cfg.description or None,
                    claude_md_path=study_cfg.claude_md_path or None,
                    sprints_dir=study_cfg.sprints_dir,
                    config_json=config_json,
                )
            else:
                logger.info("Updating study %r in database", study_cfg.name)
                await queries.update_study(
                    self.db,
                    study_cfg.name,
                    cluster=study_cfg.cluster,
                    description=study_cfg.description or None,
                    claude_md_path=study_cfg.claude_md_path or None,
                    sprints_dir=study_cfg.sprints_dir,
                    config_json=config_json,
                )

        logger.info(
            "Study sync complete: %d study/studies processed",
            len(self.config.studies),
        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def get(self, name: str) -> dict | None:
        """Return a single study by name, or ``None``."""
        return await queries.get_study(self.db, name)

    async def list_all(self) -> list[dict]:
        """Return all studies."""
        return await queries.list_studies(self.db)

    async def get_cluster_config(self, study_name: str) -> ClusterConfig:
        """Return the :class:`ClusterConfig` for the cluster associated
        with *study_name*.

        Raises :class:`ValueError` if the study or cluster is not found.
        """
        study = await queries.get_study(self.db, study_name)
        if study is None:
            raise ValueError(f"Study not found: {study_name}")

        cluster_name = study["cluster"]
        for cluster in self.config.clusters:
            if cluster.name == cluster_name:
                return cluster

        raise ValueError(
            f"Cluster {cluster_name!r} (referenced by study "
            f"{study_name!r}) not found in config"
        )
