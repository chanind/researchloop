"""Tests for researchloop.core.models."""

import re

from researchloop.core.models import (
    SprintStatus,
    format_sprint_dirname,
    generate_sprint_id,
)


class TestGenerateSprintId:
    def test_format(self):
        sid = generate_sprint_id()
        assert sid.startswith("sp-")
        assert len(sid) == 9  # "sp-" + 6 hex chars

    def test_uniqueness(self):
        ids = {generate_sprint_id() for _ in range(100)}
        assert len(ids) == 100


class TestFormatSprintDirname:
    def test_basic(self):
        dirname = format_sprint_dirname("sp-abc123", "Test Feature Absorption")
        assert dirname.startswith("sp-abc123--")
        assert "test-feature-absorption" in dirname

    def test_special_chars(self):
        dirname = format_sprint_dirname("sp-abc123", "try this: feature (v2)!")
        # Should only have alphanumeric and hyphens in the slug
        slug = dirname.split("--")[-1]
        assert re.match(r"^[a-z0-9-]+$", slug)

    def test_long_idea_truncated(self):
        long_idea = "a" * 200
        dirname = format_sprint_dirname("sp-abc123", long_idea)
        # Slug portion should be truncated to 60
        slug = dirname.split("--")[-1]
        assert len(slug) <= 60

    def test_date_time_parts(self):
        dirname = format_sprint_dirname("sp-abc123", "test")
        parts = dirname.split("--")
        assert len(parts) == 4
        # parts[1] should be YYYY-MM-DD
        assert re.match(r"\d{4}-\d{2}-\d{2}", parts[1])
        # parts[2] should be HH-MM
        assert re.match(r"\d{2}-\d{2}", parts[2])


class TestSprintStatus:
    def test_values(self):
        assert SprintStatus.PENDING.value == "pending"
        assert SprintStatus.COMPLETED.value == "completed"
        assert SprintStatus.FAILED.value == "failed"

    def test_is_str(self):
        # SprintStatus inherits from str
        assert isinstance(SprintStatus.RUNNING, str)
        assert SprintStatus.RUNNING == "running"
