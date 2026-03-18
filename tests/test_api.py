"""Tests for the FastAPI application (orchestrator routes)."""

from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

from researchloop.core.config import (
    ClusterConfig,
    Config,
    DashboardConfig,
    StudyConfig,
)
from researchloop.core.orchestrator import Orchestrator, create_app
from researchloop.dashboard.auth import hash_password
from researchloop.db import queries


def _make_app(
    shared_secret: str | None = "test-key",
    password_hash: str | None = None,
) -> tuple[TestClient, Orchestrator]:
    """Create a TestClient with an in-memory orchestrator."""
    config = Config(
        studies=[
            StudyConfig(
                name="test",
                cluster="local",
                sprints_dir="./sp",
            )
        ],
        clusters=[
            ClusterConfig(
                name="local",
                host="localhost",
                scheduler_type="local",
            )
        ],
        db_path=":memory:",
        artifact_dir=tempfile.mkdtemp(),
        shared_secret=shared_secret,
        dashboard=DashboardConfig(
            password_hash=password_hash,
        ),
    )
    orch = Orchestrator(config)
    app = create_app(orch)
    return TestClient(app), orch


class TestStudiesAPI:
    def test_list_studies(self):
        client, _ = _make_app()
        with client:
            resp = client.get("/api/studies", headers={"x-shared-secret": "test-key"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["studies"]) == 1
            assert data["studies"][0]["name"] == "test"


class TestSprintsAPI:
    def test_list_sprints_empty(self):
        client, _ = _make_app()
        with client:
            resp = client.get("/api/sprints", headers={"x-shared-secret": "test-key"})
            assert resp.status_code == 200
            assert resp.json()["sprints"] == []

    def test_get_sprint_not_found(self):
        client, _ = _make_app()
        with client:
            h = {"x-shared-secret": "test-key"}
            resp = client.get("/api/sprints/sp-nonexistent", headers=h)
            assert resp.status_code == 404

    async def test_list_sprints_with_data(self):
        client, orch = _make_app()
        with client:
            # Insert a sprint directly into DB
            await queries.create_sprint(orch.db, "sp-test01", "test", "idea 1")
            resp = client.get("/api/sprints", headers={"x-shared-secret": "test-key"})
            assert resp.status_code == 200
            assert len(resp.json()["sprints"]) == 1

    async def test_get_sprint(self):
        client, orch = _make_app()
        with client:
            await queries.create_sprint(orch.db, "sp-test01", "test", "idea 1")
            h = {"x-shared-secret": "test-key"}
            resp = client.get("/api/sprints/sp-test01", headers=h)
            assert resp.status_code == 200
            assert resp.json()["sprint"]["idea"] == "idea 1"

    async def test_list_sprints_filter(self):
        client, orch = _make_app()
        with client:
            await queries.create_sprint(orch.db, "sp-001", "test", "idea 1")
            h = {"x-shared-secret": "test-key"}
            resp = client.get("/api/sprints?study_name=test", headers=h)
            assert len(resp.json()["sprints"]) == 1
            resp = client.get("/api/sprints?study_name=other", headers=h)
            assert len(resp.json()["sprints"]) == 0


class TestWebhookAuth:
    async def test_webhook_rejects_no_token(self):
        client, orch = _make_app()
        with client:
            await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            resp = client.post(
                "/api/webhook/sprint-complete",
                json={
                    "sprint_id": "sp-001",
                    "status": "completed",
                },
            )
            assert resp.status_code == 401

    async def test_webhook_rejects_wrong_token(self):
        client, orch = _make_app()
        with client:
            await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            resp = client.post(
                "/api/webhook/sprint-complete",
                json={
                    "sprint_id": "sp-001",
                    "status": "completed",
                },
                headers={"x-webhook-token": "wrong"},
            )
            assert resp.status_code == 401

    async def test_webhook_accepts_correct_token(self):
        client, orch = _make_app()
        with client:
            row = await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            token = row["webhook_token"]
            resp = client.post(
                "/api/webhook/sprint-complete",
                json={
                    "sprint_id": "sp-001",
                    "status": "completed",
                },
                headers={"x-webhook-token": token},
            )
            assert resp.status_code == 200


class TestWebhookSprintComplete:
    async def test_updates_sprint(self):
        client, orch = _make_app()
        with client:
            row = await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            token = row["webhook_token"]
            resp = client.post(
                "/api/webhook/sprint-complete",
                json={
                    "sprint_id": "sp-001",
                    "status": "completed",
                    "summary": "All good",
                },
                headers={"x-webhook-token": token},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

            sprint = await queries.get_sprint(orch.db, "sp-001")
            assert sprint["status"] == "completed"
            assert sprint["summary"] == "All good"

    async def test_missing_sprint_id(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/api/webhook/sprint-complete",
                json={"status": "completed"},
            )
            assert resp.status_code == 400


class TestWebhookHeartbeat:
    async def test_heartbeat(self):
        client, orch = _make_app()
        with client:
            row = await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            token = row["webhook_token"]
            resp = client.post(
                "/api/webhook/heartbeat",
                json={
                    "sprint_id": "sp-001",
                    "phase": "research",
                },
                headers={"x-webhook-token": token},
            )
            assert resp.status_code == 200
            sprint = await queries.get_sprint(orch.db, "sp-001")
            assert sprint["status"] == "research"


class TestArtifactUpload:
    async def test_upload(self):
        client, orch = _make_app()
        with client:
            row = await queries.create_sprint(orch.db, "sp-001", "test", "idea")
            token = row["webhook_token"]
            resp = client.post(
                "/api/artifacts/sp-001",
                files={
                    "file": (
                        "report.md",
                        b"# Report\nContent here.",
                        "text/markdown",
                    )
                },
                headers={"x-webhook-token": token},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["filename"] == "report.md"
            assert data["size"] > 0

            arts = await queries.list_artifacts(orch.db, "sp-001")
            assert len(arts) == 1

    async def test_upload_sprint_not_found(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/api/artifacts/sp-nonexistent",
                files={
                    "file": (
                        "f.txt",
                        b"data",
                        "text/plain",
                    )
                },
                headers={"x-webhook-token": "anything"},
            )
            assert resp.status_code == 404


class TestCreateSprint:
    """POST /api/sprints creates and returns sprint."""

    def test_create_sprint_success(self):
        from unittest.mock import AsyncMock, patch

        from researchloop.core.models import (
            Sprint,
            SprintStatus,
        )

        client, orch = _make_app()
        mock_sprint = Sprint(
            id="sp-new01",
            study_name="test",
            idea="test idea",
            status=SprintStatus.SUBMITTED,
            job_id="42",
        )
        with client:
            with patch.object(
                orch.sprint_manager,
                "run_sprint",
                new_callable=AsyncMock,
                return_value=mock_sprint,
            ) as mock_run:
                resp = client.post(
                    "/api/sprints",
                    json={
                        "study_name": "test",
                        "idea": "test idea",
                    },
                    headers={"x-shared-secret": "test-key"},
                )
                assert resp.status_code == 201
                data = resp.json()
                assert data["sprint_id"] == "sp-new01"
                assert data["status"] == "submitted"
                assert data["job_id"] == "42"
                mock_run.assert_called_once()

    def test_create_sprint_missing_fields(self):
        client, _ = _make_app()
        with client:
            h = {"x-shared-secret": "test-key"}
            resp = client.post(
                "/api/sprints",
                json={"study_name": "test"},
                headers=h,
            )
            assert resp.status_code == 400

            resp = client.post(
                "/api/sprints",
                json={"idea": "something"},
                headers=h,
            )
            assert resp.status_code == 400


class TestCancelSprint:
    """POST /api/sprints/{id}/cancel."""

    def test_cancel_sprint(self):
        from unittest.mock import AsyncMock, patch

        client, orch = _make_app()
        with client:
            with patch.object(
                orch.sprint_manager,
                "cancel_sprint",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_cancel:
                resp = client.post(
                    "/api/sprints/sp-001/cancel",
                    headers={"x-shared-secret": "test-key"},
                )
                assert resp.status_code == 200
                assert resp.json()["cancelled"] is True
                mock_cancel.assert_called_once_with("sp-001")

    def test_cancel_sprint_returns_false(self):
        from unittest.mock import AsyncMock, patch

        client, orch = _make_app()
        with client:
            with patch.object(
                orch.sprint_manager,
                "cancel_sprint",
                new_callable=AsyncMock,
                return_value=False,
            ):
                resp = client.post(
                    "/api/sprints/sp-001/cancel",
                    headers={"x-shared-secret": "test-key"},
                )
                assert resp.status_code == 200
                assert resp.json()["cancelled"] is False


class TestCreateLoop:
    """POST /api/loops creates loop."""

    def test_create_loop_success(self):
        from unittest.mock import AsyncMock, patch

        client, orch = _make_app()
        with client:
            with patch.object(
                orch.auto_loop,
                "start",
                new_callable=AsyncMock,
                return_value="loop-abc",
            ) as mock_start:
                resp = client.post(
                    "/api/loops",
                    json={
                        "study_name": "test",
                        "count": 3,
                    },
                    headers={"x-shared-secret": "test-key"},
                )
                assert resp.status_code == 201
                assert resp.json()["loop_id"] == "loop-abc"
                mock_start.assert_called_once()

    def test_create_loop_missing_study(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/api/loops",
                json={"count": 5},
                headers={"x-shared-secret": "test-key"},
            )
            assert resp.status_code == 400


class TestStopLoop:
    """POST /api/loops/{id}/stop."""

    def test_stop_loop(self):
        from unittest.mock import AsyncMock, patch

        client, orch = _make_app()
        with client:
            with patch.object(
                orch.auto_loop,
                "stop",
                new_callable=AsyncMock,
            ) as mock_stop:
                resp = client.post(
                    "/api/loops/loop-001/stop",
                    headers={"x-shared-secret": "test-key"},
                )
                assert resp.status_code == 200
                assert resp.json()["stopped"] is True
                mock_stop.assert_called_once_with("loop-001")


class TestAuthNoPassword:
    """POST /api/auth returns 400 when no password configured."""

    def test_auth_no_password_configured(self):
        client, _ = _make_app(password_hash=None)
        with client:
            resp = client.post(
                "/api/auth",
                json={"password": "anything"},
            )
            assert resp.status_code == 400
            assert "No password" in resp.json()["detail"]


class TestTokenAuth:
    """Bearer token auth via POST /api/auth."""

    def test_get_token_with_password(self):
        pw_hash = hash_password("mypassword")
        client, _ = _make_app(shared_secret="secret", password_hash=pw_hash)
        with client:
            resp = client.post(
                "/api/auth",
                json={"password": "mypassword"},
            )
            assert resp.status_code == 200
            assert "token" in resp.json()

    def test_wrong_password_rejected(self):
        pw_hash = hash_password("mypassword")
        client, _ = _make_app(shared_secret="secret", password_hash=pw_hash)
        with client:
            resp = client.post(
                "/api/auth",
                json={"password": "wrong"},
            )
            assert resp.status_code == 401

    def test_token_grants_api_access(self):
        """Token from /api/auth works on protected endpoints."""
        pw_hash = hash_password("mypassword")
        client, _ = _make_app(shared_secret="secret", password_hash=pw_hash)
        with client:
            # Get token.
            auth_resp = client.post(
                "/api/auth",
                json={"password": "mypassword"},
            )
            token = auth_resp.json()["token"]

            # Use token (not shared_secret) to access API.
            resp = client.get(
                "/api/studies",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert "studies" in resp.json()

    def test_invalid_token_rejected(self):
        client, _ = _make_app(shared_secret="secret")
        with client:
            resp = client.get(
                "/api/studies",
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert resp.status_code == 401

    def test_no_credentials_rejected(self):
        client, _ = _make_app(shared_secret="secret")
        with client:
            resp = client.get("/api/studies")
            assert resp.status_code == 401

    def test_shared_secret_still_works(self):
        """Shared secret auth continues to work alongside tokens."""
        pw_hash = hash_password("mypassword")
        client, _ = _make_app(shared_secret="secret", password_hash=pw_hash)
        with client:
            resp = client.get(
                "/api/studies",
                headers={"x-shared-secret": "secret"},
            )
            assert resp.status_code == 200
