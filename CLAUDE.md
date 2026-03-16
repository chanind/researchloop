# CLAUDE.md

## Project overview

ResearchLoop is an automated research sprint platform for HPC clusters. It orchestrates multi-step AI research pipelines on SLURM/SGE clusters using `claude -p` for all AI work. The orchestrator is a lightweight Docker container; all heavy compute runs on HPC.

## Architecture

Two processes:
1. **Orchestrator** (`researchloop serve`) — FastAPI server that manages studies/sprints in SQLite, submits jobs via SSH, receives webhooks from runners, stores artifacts.
2. **Sprint Runner** (`researchloop-runner run`) — runs inside each SLURM/SGE job on HPC. Chains `claude -p` calls through a pipeline (research → red-team → fix → validate → report → summarize), then uploads artifacts and sends a completion webhook.

Key design decisions:
- All AI work runs on HPC, never on the orchestrator
- `claude -p --output-format json` for all agent invocations (no Agent SDK dependency)
- SSH to HPC login nodes for sbatch/squeue/scancel
- Job completion via webhook (runner → orchestrator), SSH polling as fallback
- SQLite (aiosqlite, WAL mode) for metadata
- Jinja2 templates for all prompts and job scripts

## Tech stack

Python 3.10+, uv, asyncio throughout. Key deps: click (CLI), FastAPI (API), aiosqlite (DB), asyncssh (SSH), httpx (HTTP client), Jinja2 (templates).

## Commands

```bash
uv sync                              # install deps
uv run pytest tests/ -v              # run tests (123 tests, ~1s)
uv run ruff check .                  # lint
uv run ruff format .                 # format
uv run researchloop --help           # CLI help
```

## Package layout

```
researchloop/
  core/config.py        — TOML config loading into dataclasses
  core/models.py        — SprintStatus enum, Sprint/Study/AutoLoop dataclasses, ID generation
  core/orchestrator.py  — Orchestrator class (ties subsystems together) + create_app() FastAPI factory
  db/database.py        — async SQLite wrapper (WAL mode, auto-migrations)
  db/migrations.py      — CREATE TABLE statements (6 tables + indexes)
  db/queries.py         — async CRUD functions (all take Database as first arg, return dicts)
  clusters/ssh.py       — SSHConnection + SSHManager (connection pooling via asyncssh)
  clusters/monitor.py   — JobMonitor (polls active jobs, detects abandoned via heartbeat)
  schedulers/base.py    — BaseScheduler ABC (submit/status/cancel/generate_script)
  schedulers/slurm.py   — SlurmScheduler (sbatch/squeue/sacct/scancel)
  schedulers/local.py   — LocalScheduler (subprocesses, for testing)
  sprints/manager.py    — SprintManager (create/submit/cancel/handle_completion)
  sprints/auto_loop.py  — AutoLoopController (start/stop, idea generation stubbed for Phase 2)
  studies/manager.py    — StudyManager (config→DB sync, cluster config resolution)
  runner/pipeline.py    — Pipeline class (runs the 5-step research pipeline)
  runner/claude.py      — run_claude() wrapper + render_template()
  runner/upload.py      — upload_artifacts(), send_webhook(), send_heartbeat()
  runner/templates/     — 7 Jinja2 prompt templates (.md.j2)
  runner/job_templates/ — SLURM job script template (slurm.sh.j2)
  comms/base.py         — BaseNotifier ABC
  comms/ntfy.py         — NtfyNotifier (ntfy.sh push notifications)
  comms/router.py       — NotificationRouter (fan-out to all backends)
  dashboard/app.py      — ASGI app entry point for `researchloop serve`
  cli.py                — Click CLI (init, serve, study, sprint, loop, cluster commands)
```

## Database

SQLite with 6 tables: `studies`, `sprints`, `auto_loops`, `artifacts`, `slack_sessions`, `events`. Schema in `db/migrations.py`. All queries in `db/queries.py` use parameterized SQL and return plain dicts.

## Key patterns

- All source files use `from __future__ import annotations` for 3.10 compat
- Config is loaded from `researchloop.toml` (TOML) via `core/config.py`
- Database queries are plain async functions in `db/queries.py`, not methods on Database
- Schedulers take an SSH connection object but LocalScheduler ignores it
- The sprint manager renders SLURM job scripts from Jinja2 templates, writes them to the cluster via SSH, then submits with sbatch
- SprintManager.submit_sprint() handles the full workflow: render template → SSH mkdir → write script → sbatch → update DB → notify
- The runner pipeline writes `.researchloop/status.json` for heartbeat tracking and sends HTTP heartbeats to the orchestrator
- Tests use in-memory SQLite (`:memory:`) and mock SSH via AsyncMock

## Testing

123 pytest tests across 10 files. Tests cover: models, config parsing, database operations, all query functions, SLURM scheduler (mock SSH), local scheduler (real subprocesses), study/sprint managers, notification router, FastAPI endpoints (TestClient), CLI commands (CliRunner), runner output parsing, and template rendering.

## CI

GitHub Actions: lint (ruff check + format) and test (pytest on Python 3.10, 3.12, 3.13).

## Current status

Phase 1 is complete. Phases 2-5 are planned:
- Phase 2: Auto-loop with LLM idea generation between sprints
- Phase 3: Slack integration (Events API)
- Phase 4: Web dashboard (HTMX + password auth)
- Phase 5: SGE scheduler, polish, PyPI
