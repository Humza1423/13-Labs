"""Shared provider ports (C0).

The engine (E1) depends only on these interfaces. FastAPI app wiring picks the
concrete driver and adapters; provider constructors and credentials stay
outside the engine.

- DefenderDriver: implemented by V1 (ElevenLabs headless agent) and by the
  Anthropic Messages fallback; E1 may ship a scripted FixtureDriver for tests.
- TraceSink: implemented by T1 (TraceRoot). Must fail open.
- AuthorizationGate: implemented by P1 (Alter). Local default below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable

from .contracts import AuthDecision, RunEvent, ToolName, Version


@dataclass(frozen=True)
class DriverSpec:
    run_id: str
    case_id: str
    version: Version
    defender_prompt: str  # canonical baseline or revised instructions
    version_hash: str
    first_message: str  # visible customer opening line
    deadline_s: float = 35.0
    max_customer_turns: int = 4
    max_tool_calls: int = 8


@dataclass(frozen=True)
class DriverResult:
    conversation_id: str | None
    final_text: str | None
    completed: bool
    incomplete_reason: str | None = None
    source: Literal["elevenlabs", "model", "fixture"] = "model"
    provider_meta: dict[str, Any] = field(default_factory=dict)


# next_customer_turn(last_agent_text) -> next bounded customer utterance, or None to end the case.
NextCustomerTurn = Callable[[str], Awaitable[str | None]]
# call_tool(call_id, tool_name, args) -> typed public tool output (JSON-serializable dict).
CallTool = Callable[[str, ToolName, dict[str, Any]], Awaitable[dict[str, Any]]]
# emit(kind, payload) -> lets a driver report provider-side observations (messages, ids).
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


@runtime_checkable
class DefenderDriver(Protocol):
    name: str

    async def run_case(
        self,
        spec: DriverSpec,
        next_customer_turn: NextCustomerTurn,
        call_tool: CallTool,
        emit: Emit,
    ) -> DriverResult: ...


@runtime_checkable
class TraceSink(Protocol):
    def observe(self, event: RunEvent) -> None:
        """Best effort. Must never raise into the business state machine."""


@dataclass(frozen=True)
class AuthorizationResult:
    decision: AuthDecision
    source: Literal["local", "alter"]
    receipt_id: str | None = None
    detail: str | None = None


@runtime_checkable
class AuthorizationGate(Protocol):
    async def authorize(self, action_id: str) -> AuthorizationResult: ...


class NullTraceSink:
    """Local default: durable local events are the source of truth."""

    def observe(self, event: RunEvent) -> None:  # noqa: D401
        return None


class LocalAuthorizationGate:
    """Local default. The engine enforces case scope and verified eligibility
    before calling the gate; this gate only records a labeled local decision."""

    async def authorize(self, action_id: str) -> AuthorizationResult:
        return AuthorizationResult(decision="allowed", source="local", receipt_id=None)
