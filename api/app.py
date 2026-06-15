"""FastAPI application factory.

Wires together storage, agents, and routes. App state holds all injected
dependencies so routes can access them via request.app.state.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from observability.logger import get_logger

log = get_logger("api.app")


def create_app(
    event_store=None,
    vector_store=None,
    cache=None,
    conversational_agent=None,
    daily_pipeline=None,
    rss_crawler=None,
    tavily_search=None,
    extraction_agent=None,
    competitors=None,
    discovery_agent=None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Dependencies (event_store, etc.) are passed in from main.py after
    they have been initialised. This factory pattern keeps the app
    testable: tests can inject mock dependencies.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        log.info("api_startup", action="lifespan", status="starting")
        yield
        log.info("api_shutdown", action="lifespan", status="shutting_down")

    app = FastAPI(
        title="Market Intelligence Agent API",
        version="1.0.0",
        description=(
            "AI-powered competitive market intelligence. "
            "Research is offline; answers are online."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — restrict in production via env config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach injected dependencies to app state
    app.state.event_store = event_store
    app.state.vector_store = vector_store
    app.state.cache = cache
    app.state.conversational_agent = conversational_agent
    app.state.daily_pipeline = daily_pipeline
    app.state.rss_crawler = rss_crawler
    app.state.tavily_search = tavily_search
    app.state.extraction_agent = extraction_agent
    app.state.competitors = competitors or []
    app.state.discovery_agent = discovery_agent

    app.include_router(router, prefix="/api/v1")

    # Expose app.state to the log stream endpoint
    import api.routes as _routes
    _routes._app_state = app.state

    return app
