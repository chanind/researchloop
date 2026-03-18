"""Async query functions for the researchloop database.

Every public function takes a :class:`Database` instance as its first
argument and returns plain ``dict`` rows (converted from ``aiosqlite.Row``).
All queries use parameterized placeholders to prevent SQL injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import Database

# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


async def create_study(
    db: Database,
    name: str,
    cluster: str,
    description: str | None,
    claude_md_path: str | None,
    sprints_dir: str,
    config_json: str | None = None,
) -> dict[str, Any]:
    """Insert a new study and return it."""
    await db.execute(
        """
        INSERT INTO studies
            (name, cluster, description, claude_md_path,
             sprints_dir, config_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, cluster, description, claude_md_path, sprints_dir, config_json),
    )
    return await get_study(db, name)  # type: ignore[return-value]


async def get_study(db: Database, name: str) -> dict[str, Any] | None:
    """Return a single study by name, or ``None``."""
    return await db.fetch_one("SELECT * FROM studies WHERE name = ?", (name,))


async def list_studies(db: Database) -> list[dict[str, Any]]:
    """Return all studies ordered by creation time (newest first)."""
    return await db.fetch_all("SELECT * FROM studies ORDER BY created_at DESC")


async def update_study(db: Database, name: str, **kwargs: Any) -> dict[str, Any] | None:
    """Update arbitrary columns on a study.  Returns the updated row."""
    if not kwargs:
        return await get_study(db, name)
    columns = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values())
    values.append(name)
    await db.execute(
        f"UPDATE studies SET {columns} WHERE name = ?",
        values,
    )
    return await get_study(db, name)


# ---------------------------------------------------------------------------
# Sprints
# ---------------------------------------------------------------------------


async def create_sprint(
    db: Database,
    id: str,
    study_name: str,
    idea: str | None = None,
    directory: str | None = None,
) -> dict[str, Any]:
    """Insert a new sprint and return it."""
    import secrets

    webhook_token = secrets.token_hex(16)
    await db.execute(
        """
        INSERT INTO sprints (id, study_name, idea, directory, webhook_token)
        VALUES (?, ?, ?, ?, ?)
        """,
        (id, study_name, idea, directory, webhook_token),
    )
    return await get_sprint(db, id)  # type: ignore[return-value]


async def get_sprint(db: Database, id: str) -> dict[str, Any] | None:
    """Return a single sprint by id, or ``None``."""
    return await db.fetch_one("SELECT * FROM sprints WHERE id = ?", (id,))


async def list_sprints(
    db: Database,
    study_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return sprints with optional filters, newest first."""
    clauses: list[str] = []
    params: list[Any] = []

    if study_name is not None:
        clauses.append("study_name = ?")
        params.append(study_name)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    return await db.fetch_all(
        f"SELECT * FROM sprints {where} ORDER BY created_at DESC LIMIT ?",
        params,
    )


async def update_sprint(db: Database, id: str, **kwargs: Any) -> dict[str, Any] | None:
    """Update arbitrary columns on a sprint.  Returns the updated row."""
    if not kwargs:
        return await get_sprint(db, id)
    columns = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values())
    values.append(id)
    await db.execute(
        f"UPDATE sprints SET {columns} WHERE id = ?",
        values,
    )
    return await get_sprint(db, id)


async def delete_sprint(db: Database, id: str) -> None:
    """Delete a sprint and its related artifacts and events."""
    await db.execute("DELETE FROM artifacts WHERE sprint_id = ?", [id])
    await db.execute("DELETE FROM events WHERE sprint_id = ?", [id])
    await db.execute("DELETE FROM sprints WHERE id = ?", [id])


async def get_active_sprints(db: Database) -> list[dict[str, Any]]:
    """Return all sprints whose status is 'running'."""
    return await db.fetch_all(
        "SELECT * FROM sprints WHERE status = 'running' ORDER BY created_at DESC",
    )


# ---------------------------------------------------------------------------
# Auto-loops
# ---------------------------------------------------------------------------


async def create_auto_loop(
    db: Database,
    id: str,
    study_name: str,
    total_count: int,
) -> dict[str, Any]:
    """Insert a new auto-loop and return it."""
    await db.execute(
        """
        INSERT INTO auto_loops (id, study_name, total_count)
        VALUES (?, ?, ?)
        """,
        (id, study_name, total_count),
    )
    return await get_auto_loop(db, id)  # type: ignore[return-value]


async def get_auto_loop(db: Database, id: str) -> dict[str, Any] | None:
    """Return a single auto-loop by id, or ``None``."""
    return await db.fetch_one("SELECT * FROM auto_loops WHERE id = ?", (id,))


async def list_auto_loops(
    db: Database,
    study_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return auto-loops, optionally filtered by study name."""
    if study_name is not None:
        return await db.fetch_all(
            "SELECT * FROM auto_loops WHERE study_name = ? ORDER BY created_at DESC",
            (study_name,),
        )
    return await db.fetch_all(
        "SELECT * FROM auto_loops ORDER BY created_at DESC",
    )


async def update_auto_loop(
    db: Database, id: str, **kwargs: Any
) -> dict[str, Any] | None:
    """Update arbitrary columns on an auto-loop.  Returns the updated row."""
    if not kwargs:
        return await get_auto_loop(db, id)
    columns = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values())
    values.append(id)
    await db.execute(
        f"UPDATE auto_loops SET {columns} WHERE id = ?",
        values,
    )
    return await get_auto_loop(db, id)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


async def create_artifact(
    db: Database,
    sprint_id: str,
    filename: str,
    path: str,
    size: int | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Insert a new artifact and return it."""
    cursor = await db.execute(
        """
        INSERT INTO artifacts (sprint_id, filename, path, size, content_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sprint_id, filename, path, size, content_type),
    )
    row_id = cursor.lastrowid
    return await get_artifact(db, row_id)  # type: ignore[return-value]


async def list_artifacts(db: Database, sprint_id: str) -> list[dict[str, Any]]:
    """Return all artifacts for a given sprint."""
    return await db.fetch_all(
        "SELECT * FROM artifacts WHERE sprint_id = ? ORDER BY uploaded_at DESC",
        (sprint_id,),
    )


async def get_artifact(db: Database, id: int) -> dict[str, Any] | None:
    """Return a single artifact by id, or ``None``."""
    return await db.fetch_one("SELECT * FROM artifacts WHERE id = ?", (id,))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def create_event(
    db: Database,
    sprint_id: str | None,
    event_type: str,
    data_json: str | None = None,
) -> dict[str, Any]:
    """Insert a new event and return it."""
    cursor = await db.execute(
        """
        INSERT INTO events (sprint_id, event_type, data_json)
        VALUES (?, ?, ?)
        """,
        (sprint_id, event_type, data_json),
    )
    row_id = cursor.lastrowid
    result = await db.fetch_one("SELECT * FROM events WHERE id = ?", (row_id,))
    return result  # type: ignore[return-value]


async def list_events(
    db: Database,
    sprint_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return events, optionally filtered by sprint id, newest first."""
    if sprint_id is not None:
        return await db.fetch_all(
            "SELECT * FROM events WHERE sprint_id = ? ORDER BY created_at DESC LIMIT ?",
            (sprint_id, limit),
        )
    return await db.fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
