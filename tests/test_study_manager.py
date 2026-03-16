"""Tests for researchloop.studies.manager."""

from researchloop.core.config import Config, StudyConfig
from researchloop.db import queries
from researchloop.studies.manager import StudyManager


class TestStudyManager:
    async def test_sync_creates_studies(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        await mgr.sync_from_config()

        study = await queries.get_study(db, "test-study")
        assert study is not None
        assert study["cluster"] == "local"
        assert study["description"] == "A test study"

    async def test_sync_updates_existing(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        await mgr.sync_from_config()

        # Modify config and re-sync
        sample_config.studies[0].description = "Updated description"
        await mgr.sync_from_config()

        study = await queries.get_study(db, "test-study")
        assert study["description"] == "Updated description"

    async def test_get(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        await mgr.sync_from_config()

        study = await mgr.get("test-study")
        assert study is not None
        assert study["name"] == "test-study"

    async def test_get_nonexistent(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        assert await mgr.get("nonexistent") is None

    async def test_list_all(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        await mgr.sync_from_config()

        studies = await mgr.list_all()
        assert len(studies) == 1

    async def test_get_cluster_config(self, db, sample_config):
        mgr = StudyManager(db, sample_config)
        await mgr.sync_from_config()

        cluster = await mgr.get_cluster_config("test-study")
        assert cluster.name == "local"
        assert cluster.host == "localhost"

    async def test_get_cluster_config_missing_study(self, db, sample_config):
        import pytest

        mgr = StudyManager(db, sample_config)
        with pytest.raises(ValueError, match="Study not found"):
            await mgr.get_cluster_config("nonexistent")

    async def test_get_cluster_config_missing_cluster(self, db):
        import pytest

        config = Config(
            studies=[
                StudyConfig(
                    name="orphan", cluster="missing-cluster", sprints_dir="./sp"
                )
            ],
            clusters=[],
        )
        mgr = StudyManager(db, config)
        await mgr.sync_from_config()
        with pytest.raises(ValueError, match="not found in config"):
            await mgr.get_cluster_config("orphan")
