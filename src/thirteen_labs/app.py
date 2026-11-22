"""FastAPI app: /api routes plus the built web app on one origin.

Lane routers register here. Each lane adds its own module under routes/ and
one include line below (E1: runs, V1: voice, P1: ledger).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings, get_settings
from .contracts import Capabilities, HealthResponse
from .engine.fixture import FixtureDriver
from .engine.service import ShiftEngine
from .integrations.anthropic import AnthropicDefenderDriver
from .ports import AuthorizationGate, DefenderDriver, TraceSink
from .routes import register_routers
from .store import RunStore

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def error_body(code: str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def create_app(
    *,
    settings: Settings | None = None,
    defender_driver: DefenderDriver | None = None,
    trace_sink: TraceSink | None = None,
    authorization_gate: AuthorizationGate | None = None,
    authorization_source: Literal["local", "alter"] = "local",
) -> FastAPI:
    settings = settings or get_settings()
    if defender_driver is None and settings.demo_mode != "fixture" and settings.anthropic_api_key:
        defender_driver = AnthropicDefenderDriver(settings.anthropic_api_key, settings.lab_model)
    fixture_mode = settings.demo_mode == "fixture"
    driver = FixtureDriver() if fixture_mode else defender_driver

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = ShiftEngine(
            RunStore(settings.state_db),
            driver=driver,
            mode="fixture" if fixture_mode else "live",
            source="fixture" if fixture_mode else "model",
            provider_config={} if fixture_mode else {
                "model": settings.lab_model, "agentId": settings.elevenlabs_lab_agent_id,
            },
            trace_sink=trace_sink,
            authorization=authorization_gate,
            authorization_source=authorization_source,
            voice_available=settings.capabilities["voice"],
            live_run_limit=settings.demo_run_limit,
        )
        app.state.shift_engine = engine
        try:
            yield
        finally:
            await engine.close()

    app = FastAPI(title="13 Labs", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = error_body(f"http_{exc.status_code}", str(exc.detail), exc.status_code in (429, 503))
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.get("/api/health", response_model=HealthResponse, response_model_by_alias=True)
    async def health() -> dict:
        return HealthResponse(
            status="ok",
            commit=settings.commit_sha,
            capabilities=Capabilities(**{**settings.capabilities, "batch": driver is not None}),
        ).dump()

    register_routers(app)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse(error_body("not_found", "Unknown API route."), status_code=404)

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str):
        if not WEB_DIST.is_dir():
            return JSONResponse(error_body("web_not_built", "Run npm run build --prefix web."), status_code=404)
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and WEB_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
