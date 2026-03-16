"""Tests for the dashboard web UI routes."""

from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

from researchloop.core.config import (
    ClusterConfig,
    Config,
    DashboardConfig,
    StudyConfig,
)
from researchloop.core.orchestrator import (
    Orchestrator,
    create_app,
)
from researchloop.dashboard.auth import (
    SESSION_COOKIE,
    hash_password,
)
from researchloop.db import queries


def _make_app(
    password_hash: str | None = None,
) -> tuple[TestClient, Orchestrator]:
    """Create a TestClient with dashboard routes."""
    config = Config(
        studies=[
            StudyConfig(
                name="test",
                cluster="local",
                sprints_dir="./sp",
                description="A test study",
            ),
        ],
        clusters=[
            ClusterConfig(
                name="local",
                host="localhost",
                scheduler_type="local",
            ),
        ],
        db_path=":memory:",
        artifact_dir=tempfile.mkdtemp(),
        shared_secret="test-key",
        dashboard=DashboardConfig(
            password_hash=password_hash,
        ),
    )
    orch = Orchestrator(config)
    app = create_app(orch)
    return TestClient(app), orch


def _make_app_with_password(
    password: str = "secret",
) -> tuple[TestClient, Orchestrator, str]:
    """Create app with a pre-set password, return client + hash."""
    pw_hash = hash_password(password)
    client, orch = _make_app(password_hash=pw_hash)
    return client, orch, pw_hash


class TestFirstRunSetup:
    def test_redirects_to_setup_when_no_password(self):
        client, _ = _make_app()
        with client:
            resp = client.get("/dashboard/", follow_redirects=False)
            assert resp.status_code == 303
            assert "/dashboard/setup" in resp.headers["location"]

    def test_setup_page_renders(self):
        client, _ = _make_app()
        with client:
            resp = client.get("/dashboard/setup")
            assert resp.status_code == 200
            assert "Set password" in resp.text

    def test_setup_sets_password_and_logs_in(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/dashboard/setup",
                data={
                    "password": "mypassword",
                    "confirm": "mypassword",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == "/dashboard/"
            assert SESSION_COOKIE in resp.cookies

    def test_setup_rejects_short_password(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/dashboard/setup",
                data={"password": "short", "confirm": "short"},
            )
            assert resp.status_code == 400
            assert "at least 8" in resp.text

    def test_setup_rejects_mismatched_passwords(self):
        client, _ = _make_app()
        with client:
            resp = client.post(
                "/dashboard/setup",
                data={
                    "password": "mypassword",
                    "confirm": "different",
                },
            )
            assert resp.status_code == 400
            assert "do not match" in resp.text

    def test_setup_blocked_after_password_set(self):
        client, _, _ = _make_app_with_password()
        with client:
            resp = client.get("/dashboard/setup", follow_redirects=False)
            assert resp.status_code == 303
            assert "/dashboard/" in resp.headers["location"]


class TestLoginPage:
    def test_login_page_renders(self):
        client, _, _ = _make_app_with_password()
        with client:
            resp = client.get("/dashboard/login")
            assert resp.status_code == 200
            assert "Sign in" in resp.text

    def test_login_redirects_to_setup_if_no_password(self):
        client, _ = _make_app()
        with client:
            resp = client.get("/dashboard/login", follow_redirects=False)
            assert resp.status_code == 303
            assert "/dashboard/setup" in resp.headers["location"]

    def test_login_correct_password(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == "/dashboard/"
            assert SESSION_COOKIE in resp.cookies

    def test_login_incorrect_password(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            resp = client.post(
                "/dashboard/login",
                data={"password": "wrong"},
            )
            assert resp.status_code == 401
            assert "Invalid password" in resp.text


class TestAuthRedirect:
    def test_redirect_when_password_required(self):
        client, _, _ = _make_app_with_password()
        with client:
            resp = client.get("/dashboard/", follow_redirects=False)
            assert resp.status_code == 303
            assert "/dashboard/login" in resp.headers["location"]

    def test_authenticated_access(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            assert cookie is not None

            resp = client.get(
                "/dashboard/",
                cookies={SESSION_COOKIE: cookie},
                follow_redirects=False,
            )
            assert resp.status_code == 200


class TestStudiesPage:
    def test_studies_page_shows_study(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            resp = client.get(
                "/dashboard/",
                cookies={SESSION_COOKIE: cookie},
            )
            assert resp.status_code == 200
            assert "A test study" in resp.text


class TestSprintsPage:
    async def test_sprints_page_with_data(self):
        client, orch, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            await queries.create_sprint(
                orch.db,
                "sp-dash01",
                "test",
                "dashboard test idea",
            )
            resp = client.get(
                "/dashboard/sprints",
                cookies={SESSION_COOKIE: cookie},
            )
            assert resp.status_code == 200
            assert "sp-dash01" in resp.text


class TestSprintDetailPage:
    async def test_sprint_detail_page(self):
        client, orch, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            await queries.create_sprint(
                orch.db,
                "sp-det01",
                "test",
                "detail test idea",
            )
            resp = client.get(
                "/dashboard/sprints/sp-det01",
                cookies={SESSION_COOKIE: cookie},
            )
            assert resp.status_code == 200
            assert "detail test idea" in resp.text

    def test_sprint_detail_not_found(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            resp = client.get(
                "/dashboard/sprints/sp-nope",
                cookies={SESSION_COOKIE: cookie},
            )
            assert resp.status_code == 404


class TestLogout:
    def test_logout_clears_cookie(self):
        client, _, _ = _make_app_with_password()
        with client:
            resp = client.get("/dashboard/logout", follow_redirects=False)
            assert resp.status_code == 303
            assert "/dashboard/login" in resp.headers["location"]


class TestLoopsPage:
    def test_loops_page_returns_200(self):
        client, _, _ = _make_app_with_password("secret")
        with client:
            login_resp = client.post(
                "/dashboard/login",
                data={"password": "secret"},
                follow_redirects=False,
            )
            cookie = login_resp.cookies.get(SESSION_COOKIE)
            resp = client.get(
                "/dashboard/loops",
                cookies={SESSION_COOKIE: cookie},
            )
            assert resp.status_code == 200
            assert "Auto-Loops" in resp.text
