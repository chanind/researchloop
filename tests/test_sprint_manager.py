"""Tests for researchloop.sprints.manager."""

from unittest.mock import AsyncMock

from researchloop.core.models import SprintStatus
from researchloop.db import queries
from researchloop.sprints.manager import SprintManager


class TestSprintManagerCreate:
    async def test_create_sprint(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        sprint = await mgr.create_sprint("test-study", "explore SAE features")
        assert sprint.id.startswith("sp-")
        assert sprint.study_name == "test-study"
        assert sprint.idea == "explore SAE features"
        assert sprint.status == SprintStatus.PENDING
        assert sprint.directory is not None

        # Verify in DB
        row = await queries.get_sprint(db_with_study, sprint.id)
        assert row is not None
        assert row["idea"] == "explore SAE features"


class TestSprintManagerQuery:
    async def test_get_sprint(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        sprint = await mgr.create_sprint("test-study", "idea")
        result = await mgr.get_sprint(sprint.id)
        assert result is not None
        assert result["id"] == sprint.id

    async def test_get_sprint_nonexistent(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        assert await mgr.get_sprint("sp-nonexistent") is None

    async def test_list_sprints(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        await mgr.create_sprint("test-study", "idea 1")
        await mgr.create_sprint("test-study", "idea 2")
        sprints = await mgr.list_sprints()
        assert len(sprints) == 2

    async def test_list_sprints_filter(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        await mgr.create_sprint("test-study", "idea")
        sprints = await mgr.list_sprints(study_name="test-study")
        assert len(sprints) == 1
        sprints = await mgr.list_sprints(study_name="other")
        assert len(sprints) == 0


class TestSprintManagerCompletion:
    async def test_handle_completion_completed(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        sprint = await mgr.create_sprint("test-study", "idea")

        await mgr.handle_completion(
            sprint.id, status="completed", summary="Great results!"
        )

        row = await queries.get_sprint(db_with_study, sprint.id)
        assert row["status"] == "completed"
        assert row["summary"] == "Great results!"
        assert row["completed_at"] is not None

        # Check event was created
        events = await queries.list_events(db_with_study, sprint_id=sprint.id)
        assert len(events) == 1
        assert events[0]["event_type"] == "sprint_completed"

    async def test_handle_completion_failed(self, db_with_study, sample_config):
        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
        )
        sprint = await mgr.create_sprint("test-study", "idea")

        await mgr.handle_completion(sprint.id, status="failed", error="OOM on GPU")

        row = await queries.get_sprint(db_with_study, sprint.id)
        assert row["status"] == "failed"
        assert row["error"] == "OOM on GPU"

    async def test_handle_completion_with_notifier(self, db_with_study, sample_config):
        from researchloop.comms.router import NotificationRouter

        router = NotificationRouter()
        mock_notifier = AsyncMock()
        router.add_notifier(mock_notifier)

        mgr = SprintManager(
            db=db_with_study,
            config=sample_config,
            ssh_manager=AsyncMock(),
            schedulers={},
            notification_router=router,
        )
        sprint = await mgr.create_sprint("test-study", "idea")

        await mgr.handle_completion(sprint.id, status="completed", summary="Done!")
        mock_notifier.notify_sprint_completed.assert_called_once()
