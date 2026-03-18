"""Tests for ConversationManager action execution."""

from __future__ import annotations

from unittest.mock import AsyncMock

from researchloop.comms.conversation import ConversationManager
from researchloop.db import queries
from researchloop.db.database import Database

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


async def _make_cm(
    db: Database,
    sprint_manager: AsyncMock | None = None,
) -> ConversationManager:
    """Build a ConversationManager with a connected DB."""
    if sprint_manager is None:
        sprint_manager = AsyncMock()
    return ConversationManager(db, sprint_manager=sprint_manager)


# ---------------------------------------------------------------
# _execute_actions
# ---------------------------------------------------------------


class TestExecuteActions:
    """_execute_actions parses [ACTION: ...] tags."""

    async def test_parses_sprint_list_action(self, db_with_study):
        mgr = AsyncMock()
        mgr.list_sprints.return_value = []
        cm = await _make_cm(db_with_study, mgr)

        text = 'Let me list them. [ACTION: sprint_list {"study": "test-study"}]'
        results = await cm._execute_actions(text)
        assert len(results) == 1
        assert "No sprints found" in results[0]

    async def test_parses_multiple_actions(self, db_with_study):
        mgr = AsyncMock()
        mgr.list_sprints.return_value = []
        mgr.get_sprint.return_value = None
        cm = await _make_cm(db_with_study, mgr)

        text = (
            '[ACTION: sprint_list {"study": "s"}]'
            " and "
            '[ACTION: sprint_show {"id": "sp-nope"}]'
        )
        results = await cm._execute_actions(text)
        assert len(results) == 2

    async def test_invalid_json_returns_warning(self, db_with_study):
        cm = await _make_cm(db_with_study)

        text = "[ACTION: sprint_list {bad json!!!}]"
        results = await cm._execute_actions(text)
        assert len(results) == 1
        assert "warning" in results[0].lower()
        assert "sprint_list" in results[0]

    async def test_no_actions_returns_empty(self, db_with_study):
        cm = await _make_cm(db_with_study)
        results = await cm._execute_actions("No actions here.")
        assert results == []


# ---------------------------------------------------------------
# _run_action: sprint_run
# ---------------------------------------------------------------


class TestRunActionSprintRun:
    async def test_sprint_run_success(self, db_with_study):
        from researchloop.core.models import (
            Sprint,
            SprintStatus,
        )

        mock_sprint = Sprint(
            id="sp-abc123",
            study_name="test-study",
            idea="test idea",
            status=SprintStatus.SUBMITTED,
        )
        mgr = AsyncMock()
        mgr.run_sprint.return_value = mock_sprint
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action(
            "sprint_run",
            {"study": "test-study", "idea": "test idea"},
        )
        assert "sp-abc123" in result
        assert "test-study" in result
        mgr.run_sprint.assert_called_once_with("test-study", "test idea")

    async def test_sprint_run_missing_study(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("sprint_run", {"idea": "some idea"})
        assert "warning" in result.lower()
        assert "study" in result.lower()

    async def test_sprint_run_missing_idea(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("sprint_run", {"study": "test-study"})
        assert "warning" in result.lower()
        assert "idea" in result.lower()


# ---------------------------------------------------------------
# _run_action: sprint_list
# ---------------------------------------------------------------


class TestRunActionSprintList:
    async def test_sprint_list_empty(self, db_with_study):
        mgr = AsyncMock()
        mgr.list_sprints.return_value = []
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_list", {"study": "test-study"})
        assert "No sprints found" in result

    async def test_sprint_list_with_data(self, db_with_study):
        mgr = AsyncMock()
        mgr.list_sprints.return_value = [
            {
                "id": "sp-001",
                "status": "completed",
                "idea": "explore features",
            },
        ]
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_list", {"study": "test-study"})
        assert "sp-001" in result
        assert "completed" in result


# ---------------------------------------------------------------
# _run_action: sprint_show
# ---------------------------------------------------------------


class TestRunActionSprintShow:
    async def test_sprint_show_found(self, db_with_study):
        mgr = AsyncMock()
        mgr.get_sprint.return_value = {
            "id": "sp-001",
            "status": "completed",
            "study_name": "test-study",
            "idea": "explore features",
            "created_at": "2026-03-18T00:00:00",
            "summary": "Good results",
        }
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_show", {"id": "sp-001"})
        assert "sp-001" in result
        assert "completed" in result
        assert "Good results" in result

    async def test_sprint_show_not_found(self, db_with_study):
        mgr = AsyncMock()
        mgr.get_sprint.return_value = None
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_show", {"id": "sp-nope"})
        assert "warning" in result.lower()
        assert "not found" in result.lower()

    async def test_sprint_show_missing_id(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("sprint_show", {})
        assert "warning" in result.lower()
        assert "id" in result.lower()


# ---------------------------------------------------------------
# _run_action: sprint_cancel
# ---------------------------------------------------------------


class TestRunActionSprintCancel:
    async def test_sprint_cancel_success(self, db_with_study):
        mgr = AsyncMock()
        mgr.cancel_sprint.return_value = True
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_cancel", {"id": "sp-001"})
        assert "cancelled" in result.lower()
        mgr.cancel_sprint.assert_called_once_with("sp-001")

    async def test_sprint_cancel_failure(self, db_with_study):
        mgr = AsyncMock()
        mgr.cancel_sprint.return_value = False
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("sprint_cancel", {"id": "sp-001"})
        assert "failed" in result.lower() or "warning" in result.lower()

    async def test_sprint_cancel_missing_id(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("sprint_cancel", {})
        assert "warning" in result.lower()


# ---------------------------------------------------------------
# _run_action: study_show
# ---------------------------------------------------------------


class TestRunActionStudyShow:
    async def test_study_show_found(self, db_with_study):
        mgr = AsyncMock()
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("study_show", {"name": "test-study"})
        assert "test-study" in result

    async def test_study_show_not_found(self, db_with_study):
        mgr = AsyncMock()
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("study_show", {"name": "nonexistent"})
        assert "warning" in result.lower()
        assert "not found" in result.lower()

    async def test_study_show_missing_name(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("study_show", {})
        assert "warning" in result.lower()


# ---------------------------------------------------------------
# _run_action: loop_start
# ---------------------------------------------------------------


class TestRunActionLoopStart:
    async def test_loop_start_success(self, db_with_study):
        from unittest.mock import patch

        mgr = AsyncMock()
        mgr.db = db_with_study

        from researchloop.core.config import (
            ClusterConfig,
            Config,
            StudyConfig,
        )

        mgr.config = Config(
            studies=[
                StudyConfig(
                    name="test-study",
                    cluster="local",
                    sprints_dir="./sp",
                ),
            ],
            clusters=[
                ClusterConfig(
                    name="local",
                    host="localhost",
                    scheduler_type="local",
                ),
            ],
        )
        cm = await _make_cm(db_with_study, mgr)

        with patch(
            "researchloop.sprints.auto_loop.AutoLoopController.start",
            new_callable=AsyncMock,
            return_value="loop-abc",
        ):
            result = await cm._run_action(
                "loop_start",
                {
                    "study": "test-study",
                    "count": 3,
                    "context": "focus on F1",
                },
            )
        assert "loop-abc" in result
        assert "test-study" in result

    async def test_loop_start_missing_study(self, db_with_study):
        cm = await _make_cm(db_with_study)
        result = await cm._run_action("loop_start", {"count": 3})
        assert "warning" in result.lower()


# ---------------------------------------------------------------
# _run_action: unknown action
# ---------------------------------------------------------------


class TestRunActionUnknown:
    async def test_unknown_action(self, db_with_study):
        mgr = AsyncMock()
        cm = await _make_cm(db_with_study, mgr)

        result = await cm._run_action("do_something_weird", {"x": 1})
        assert "unknown" in result.lower()


# ---------------------------------------------------------------
# _run_action: no sprint manager
# ---------------------------------------------------------------


class TestRunActionNoManager:
    async def test_returns_warning(self, db_with_study):
        cm = ConversationManager(db_with_study, sprint_manager=None)
        result = await cm._run_action("sprint_list", {"study": "test-study"})
        assert "warning" in result.lower()
        assert "not available" in result.lower()


# ---------------------------------------------------------------
# _build_context
# ---------------------------------------------------------------


class TestBuildContext:
    async def test_includes_studies(self, db_with_study):
        cm = await _make_cm(db_with_study)
        context = await cm._build_context()
        assert "test-study" in context
        assert "Available Studies" in context

    async def test_includes_sprints(self, db_with_study):
        await queries.create_sprint(
            db_with_study,
            "sp-ctx01",
            "test-study",
            "context test idea",
        )
        cm = await _make_cm(db_with_study)
        context = await cm._build_context()
        assert "sp-ctx01" in context
        assert "Recent Sprints" in context

    async def test_no_studies_no_section(self, db):
        cm = await _make_cm(db)
        context = await cm._build_context()
        assert "Available Studies" not in context

    async def test_contains_assistant_intro(self, db):
        cm = await _make_cm(db)
        context = await cm._build_context()
        assert "ResearchLoop assistant" in context

    async def test_contains_action_docs(self, db):
        cm = await _make_cm(db)
        context = await cm._build_context()
        assert "sprint_run" in context
        assert "sprint_list" in context
        assert "study_show" in context
        assert "loop_start" in context
