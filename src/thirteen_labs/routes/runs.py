"""HTTP endpoints for the monitor probation shift."""

import hashlib
import re
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from thirteen_labs.contracts import EventsPage, RetestRequest, ReviewRequest, RunView, ScenariosResponse
from thirteen_labs.scenarios import CANDIDATE, public_scenarios
from thirteen_labs.store import RunError

router = APIRouter(prefix="/api")
SESSION_COOKIE = "thirteen_owner"
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_KEY = 128


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidateId: str = Field(min_length=1, max_length=80)
    suiteId: str = Field(min_length=1, max_length=80)


def owner_digest(token: str | None) -> str:
    if not token or not SESSION_PATTERN.fullmatch(token):
        return ""
    return hashlib.sha256(token.encode()).hexdigest()


def owner(request: Request) -> str:
    return owner_digest(request.cookies.get(SESSION_COOKIE))


def reply(body: dict, status: int = 200, **headers) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", **headers})


def error_reply(error: RunError) -> JSONResponse:
    headers = {"Retry-After": "15"} if error.status == 429 else {}
    return reply({"error": error.payload}, error.status, **headers)


def key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not value or len(value) > MAX_KEY or not value.isascii() or not value.isprintable() or not value.strip():
        raise RunError(422, "invalid_idempotency_key", "Send an Idempotency-Key of up to 128 printable characters.")
    return value


async def input_body(request: Request, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(await request.json())
    except ValueError:
        raise RunError(422, "invalid_request", "The request body does not match the API contract.") from None


@router.get("/scenarios", response_model=ScenariosResponse)
async def scenarios(request: Request):
    overview = {"candidate": CANDIDATE, "scenarios": public_scenarios()}
    recorded = getattr(request.app.state, "recorded_run_id", None)
    if recorded:
        overview["recordedRunId"] = recorded
    return reply(ScenariosResponse.model_validate(overview).dump())


@router.post("/runs", status_code=202, response_model=RunView)
async def start_run(request: Request):
    try:
        selection = await input_body(request, StartRequest)
        request_owner = owner(request)
        token = None
        if not request_owner:
            token = secrets.token_urlsafe(32)
            request_owner = owner_digest(token)
        engine = request.app.state.shift_engine
        run_id = engine.start(request_owner, key(request), selection.model_dump())
        response = reply(RunView.model_validate(engine.view(run_id, request_owner)).dump(), 202)
        if token is not None:
            response.set_cookie(
                SESSION_COOKIE, token, httponly=True, samesite="strict",
                secure=request.url.scheme == "https", max_age=86_400, path="/api",
            )
        return response
    except RunError as error:
        return error_reply(error)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, request: Request):
    try:
        view = request.app.state.shift_engine.view(run_id, owner(request))
        return reply(RunView.model_validate(view).dump())
    except RunError as error:
        return error_reply(error)


@router.get("/runs/{run_id}/events", response_model=EventsPage)
async def get_events(run_id: str, request: Request, after: str = "0"):
    try:
        if not after.isascii() or not after.isdecimal() or len(after) > 18:
            raise RunError(422, "invalid_cursor", "The event cursor must be a nonnegative integer.")
        cursor = int(after)
        store = request.app.state.shift_engine.store
        store.get(run_id)
        events = store.events(run_id, cursor)
        return reply(EventsPage(events=events, next_after=events[-1]["seq"] if events else cursor).dump())
    except RunError as error:
        return error_reply(error)


@router.post("/runs/{run_id}/retest", status_code=202, response_model=RunView)
async def retest(run_id: str, request: Request):
    try:
        selection = await input_body(request, RetestRequest)
        engine = request.app.state.shift_engine
        request_owner = owner(request)
        engine.retest(run_id, request_owner, key(request), selection.revision_id)
        return reply(RunView.model_validate(engine.view(run_id, request_owner)).dump(), 202)
    except RunError as error:
        return error_reply(error)


@router.post("/runs/{run_id}/review", response_model=RunView)
async def review(run_id: str, request: Request):
    try:
        await input_body(request, ReviewRequest)
        engine = request.app.state.shift_engine
        request_owner = owner(request)
        engine.store.review(run_id, request_owner)
        return reply(RunView.model_validate(engine.view(run_id, request_owner)).dump())
    except RunError as error:
        return error_reply(error)
