"""Dashboard FastAPI application -- used by ``researchloop serve``.

This module provides the ASGI ``app`` object that uvicorn imports.
It loads the configuration, creates an :class:`Orchestrator`, and
delegates to :func:`create_app` for route setup.
"""

from __future__ import annotations

from researchloop.core.config import load_config
from researchloop.core.orchestrator import Orchestrator, create_app

_config = load_config()
_orchestrator = Orchestrator(_config)
app = create_app(_orchestrator)
