"""App-managed configuration. Names match .env.example; values stay server-side."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    state_db: str = field(default_factory=lambda: _env("STATE_DB", "./data/13-labs.db"))
    public_origin: str = field(default_factory=lambda: _env("PUBLIC_ORIGIN", "http://localhost:8000"))
    demo_mode: str = field(default_factory=lambda: _env("DEMO_MODE", "live"))
    demo_run_limit: int = field(default_factory=lambda: int(_env("DEMO_RUN_LIMIT", "20") or "20"))
    commit_sha: str = field(default_factory=lambda: _env("COMMIT_SHA", "dev"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_lab_agent_id: str = field(default_factory=lambda: _env("ELEVENLABS_LAB_AGENT_ID"))
    elevenlabs_voice_agent_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_AGENT_ID"))
    elevenlabs_webhook_secret: str = field(default_factory=lambda: _env("ELEVENLABS_WEBHOOK_SECRET"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    lab_model: str = field(default_factory=lambda: _env("LAB_MODEL", "claude-haiku-5-5"))
    traceroot_api_key: str = field(default_factory=lambda: _env("TRACEROOT_API_KEY"))
    traceroot_dashboard_url: str = field(default_factory=lambda: _env("TRACEROOT_DASHBOARD_URL"))
    otel_exporter_otlp_endpoint: str = field(default_factory=lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT"))
    alter_api_key: str = field(default_factory=lambda: _env("ALTER_API_KEY"))
    alter_grant_id: str = field(default_factory=lambda: _env("ALTER_GRANT_ID"))
    ledger_upstream_secret: str = field(default_factory=lambda: _env("LEDGER_UPSTREAM_SECRET"))

    @property
    def capabilities(self) -> dict[str, bool]:
        """Public booleans only. Provider receipts are verified separately."""
        return {
            "batch": bool(self.anthropic_api_key or self.elevenlabs_lab_agent_id),
            "voice": bool(self.elevenlabs_api_key and self.elevenlabs_voice_agent_id),
            "traces": bool(self.traceroot_api_key or self.otel_exporter_otlp_endpoint),
            "alter": bool(self.alter_api_key and self.alter_grant_id),
            "personas": False,
        }


def get_settings() -> Settings:
    return Settings()
