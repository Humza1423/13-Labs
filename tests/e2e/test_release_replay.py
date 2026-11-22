from urllib.parse import urlsplit

import asyncio
import pytest
from fastapi.testclient import TestClient

from scripts import export_replay
from thirteen_labs.app import create_app
from thirteen_labs.config import Settings
from thirteen_labs.engine.service import ShiftEngine
from thirteen_labs.ports import DriverResult
from thirteen_labs.store import RunStore


class TestProvider:
    __test__ = False
    name = "test-only"

    async def run_case(self, spec, _next_turn, call_tool, emit):
        await call_tool("test-resolution", "record_resolution", {"outcome": "escalated"})
        reply = "I’ll send this lab claim for review."
        await emit("message", {"text": reply})
        return DriverResult(f"conv_test_{spec.case_id}_{spec.version}", reply, True, source="elevenlabs")


def test_export_and_replay_survive_reopening_the_same_database(tmp_path, monkeypatch):
    database = str(tmp_path / "release.db")
    settings = Settings(state_db=database, demo_mode="live")

    async def finish_test_run():
        engine = ShiftEngine(RunStore(database), driver=TestProvider())
        try:
            run_id = engine.start("test-owner", "start", {"candidateId": "monitor-support", "suiteId": "probation-v1"})
            await asyncio.gather(*engine.tasks)
            baseline = engine.view(run_id, "test-owner")
            engine.retest(run_id, "test-owner", "retest", baseline["revision"]["id"])
            await asyncio.gather(*engine.tasks)
            return run_id
        finally:
            await engine.close()

    run_id = asyncio.run(finish_test_run())
    with TestClient(create_app(settings=settings)) as client:
        def fetch(url):
            target = urlsplit(url)
            response = client.get(target.path + (f"?{target.query}" if target.query else ""))
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(export_replay, "fetch", fetch)
        document = export_replay.export_run("https://lab.example", run_id)
        assert document["originalRunId"] == run_id
        assert document["providers"] == ["elevenlabs"]
        assert document["run"]["progress"]["completed"] == 7
        assert document["events"][0]["runId"] == run_id

    replay_id = export_replay.retain_replay(document, database)
    assert export_replay.retain_replay(document, database) == replay_id
    assert replay_id != run_id
    monkeypatch.setenv("RECORDED_RUN_ID", replay_id)

    with TestClient(create_app(settings=settings)) as restarted:
        assert restarted.get("/api/scenarios").json()["recordedRunId"] == replay_id
        assert restarted.get(f"/api/runs/{run_id}").json()["mode"] == "live"
        replay = restarted.get(f"/api/runs/{replay_id}").json()
        assert replay["mode"] == "replay" and replay["status"] == "complete"
        assert not any(replay["actions"].values())
        events = restarted.get(f"/api/runs/{replay_id}/events").json()["events"]
        ids = {event["id"] for event in events}
        assert all(event["runId"] == replay_id for event in events)
        assert set(replay["revision"]["eventIds"]) <= ids
        assert all(set(case["evidenceIds"]) <= ids for case in replay["cases"])
        assert restarted.post(f"/api/runs/{replay_id}/review", json={"decision": "reviewed"}).status_code == 403

    last_case_event = max(event["seq"] for event in document["events"] if event["type"] == "case_scored")
    stale = {**document, "events": document["events"][:last_case_event - 1]}
    with pytest.raises(ValueError, match="missing evidence"):
        export_replay.retain_replay(stale, database)
