"""The baseline, investigation, locked revision, retest and review lifecycle."""

import asyncio
import json
import logging
from dataclasses import asdict

from thirteen_labs.engine.evaluation import BASELINE_WORKFLOW, investigate, recommendation, score, workflow_hash
from thirteen_labs.engine.tools import TOOL_SCHEMAS, CaseDispatcher, public_id
from thirteen_labs.contracts import RunEvent, TraceRef
from thirteen_labs.ports import AuthorizationGate, DefenderDriver, DriverSpec, TraceSink
from thirteen_labs.scenarios import CANDIDATE, SCENARIOS, Scenario, customer_turns
from thirteen_labs.store import RunStore, encode

logger = logging.getLogger(__name__)
CASE_SECONDS = 35
QUEUE_LIMIT = 8
INCOMPLETE_REASONS = {
    "deadline": "The case reached its deadline.",
    "tool_limit": "The case reached its tool call limit.",
    "turn_limit": "The case reached its customer turn limit.",
    "no_resolution": "The defender did not record a final resolution.",
    "provider_stopped": "The provider stopped before completing the exchange.",
    "provider_error": "The defender provider did not finish the case.",
}


class ShiftEngine:
    def __init__(
        self, store: RunStore, *, driver: DefenderDriver | None = None, mode="live", source="model",
        provider_config=None, authorization: AuthorizationGate | None = None,
        authorization_source="local", trace_sink: TraceSink | None = None, voice_available=False,
        live_run_limit=20,
    ):
        self.store = store
        self.driver = driver
        self.mode = mode
        self.source = source
        self.authorization = authorization
        self.authorization_source = authorization_source
        self.trace_sink = trace_sink
        self.voice_available = voice_available
        self.live_run_limit = live_run_limit
        self.configuration = {
            "provider": provider_config or {},
            "driver": getattr(driver, "name", type(driver).__qualname__),
            "source": source,
            "authorization": authorization_source,
            "workflowHash": workflow_hash(BASELINE_WORKFLOW),
            "toolSchemaHash": workflow_hash(encode({
                name: model.model_json_schema() for name, (model, _) in TOOL_SCHEMAS.items()
            })),
        }
        self.tasks: set[asyncio.Task] = set()
        self.worker = asyncio.Lock()
        self.store.recover()

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()

    def start(self, owner: str, key: str, selection: dict) -> str:
        run_id, created = self.store.start(
            owner, key, selection, self.mode, [asdict(case) for case in SCENARIOS],
            workflow_hash(BASELINE_WORKFLOW), self.configuration,
            available=self.driver is not None, capacity=len(self.tasks) < QUEUE_LIMIT,
            live_limit=self.live_run_limit,
        )
        if created:
            self._schedule(run_id, "baseline")
        return run_id

    def retest(self, run_id: str, owner: str, key: str, revision_id: str):
        if self.store.retest(run_id, owner, key, revision_id, self.configuration, len(self.tasks) < QUEUE_LIMIT):
            self._schedule(run_id, "retest")

    def _schedule(self, run_id: str, stage: str):
        task = asyncio.create_task(self._run(run_id, stage), name=f"shift-{stage}-{run_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(self, run_id: str, stage: str):
        try:
            async with self.worker:
                if stage == "baseline":
                    await self._baseline(run_id)
                else:
                    await self._retest(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Shift %s failed (%s)", run_id, type(error).__name__)
            self.store.fail(run_id, "shift_failed", "The lab could not finish this shift. Start a new shift to retry.", stage)

    def _suite(self, run_id: str, *, include_holdout: bool = False) -> list[Scenario]:
        rows = json.loads(self.store.get(run_id)["snapshot"])
        return [Scenario.from_snapshot(row) for row in rows if include_holdout or not row["held_out"]]

    async def _baseline(self, run_id: str):
        if not self.store.transition(
            run_id, "queued", "baseline_running", "baseline", "Baseline started",
            "The candidate is working through the paired support cases.",
        ):
            return
        for scenario in self._suite(run_id):
            await self._case(run_id, scenario, "baseline", BASELINE_WORKFLOW)
        if not self.store.transition(
            run_id, "baseline_running", "investigating", "investigation", "Evidence ready",
            "13 is examining the recorded baseline behavior.",
        ):
            return
        finding, revision = investigate(
            self.store.cases(run_id), lambda case_id: self.store.case_events(run_id, case_id, "baseline"),
        )
        self.store.save_revision(run_id, finding, revision)

    async def _retest(self, run_id: str):
        row = self.store.get(run_id)
        revision = json.loads(row["revision"])
        if row["status"] != "retest_running":
            return
        if workflow_hash(revision["after"]) != revision["versionHash"]:
            raise ValueError("Revision hash mismatch")
        for scenario in self._suite(run_id, include_holdout=True):
            await self._case(run_id, scenario, "revised", revision["after"])
        self.store.transition(
            run_id, "retest_running", "complete", "review", "Review ready",
            "The independent evaluator finished the paired cases and held-out case.",
        )

    async def _observe(self, event: dict):
        if self.trace_sink is not None:
            try:
                self.trace_sink.observe(RunEvent.model_validate(event))
            except Exception:
                logger.debug("Trace export unavailable for event %s", event["id"])

    async def _case(self, run_id: str, scenario: Scenario, version: str, prompt: str):
        stage = "baseline" if version == "baseline" else "retest"
        version_hash = workflow_hash(prompt)
        case_run_id = self.store.begin_case(run_id, scenario.id, version, self.configuration)
        dispatcher = CaseDispatcher(
            self.store, run_id=run_id, case_run_id=case_run_id, scenario=scenario, version=version, stage=stage,
            authorization=self.authorization, authorization_source=self.authorization_source, observe=self._observe,
        )
        follow_up = customer_turns(scenario.facts)

        async def next_customer_turn(last_agent_text: str) -> str | None:
            message = await follow_up(last_agent_text)
            if message is not None:
                await dispatcher.emit("message", "customer", "Customer", message, sourceTrust="customer_supplied")
            return message

        async def call_tool(call_id: str, tool_name: str, args: dict) -> dict:
            return await dispatcher(call_id, tool_name, args)

        last_message = None
        conversation_id = None

        async def emit_agent(kind: str, payload: dict):
            nonlocal last_message, conversation_id
            if kind == "provider_message":
                conversation_id = conversation_id or public_id(payload.get("id"))
                return
            if kind != "message":
                return
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                last_message = text.strip()[:1600]
                await dispatcher.emit("message", "defender", "Candidate", last_message, sourceTrust="system")

        await dispatcher.emit(
            "message", "customer", "Customer", scenario.facts.first_message, sourceTrust="customer_supplied",
        )
        spec = DriverSpec(
            run_id=run_id, case_id=scenario.id, version=version, defender_prompt=prompt, version_hash=version_hash,
            first_message=scenario.facts.first_message, deadline_s=CASE_SECONDS,
        )
        incomplete = None
        source = self.source
        try:
            async with asyncio.timeout(CASE_SECONDS):
                result = await self.driver.run_case(spec, next_customer_turn, call_tool, emit_agent)
            conversation_id = public_id(result.conversation_id) or conversation_id
            source = result.source
            if (self.mode == "fixture") != (source == "fixture"):
                incomplete = "The defender returned results with unexpected provenance."
            elif not result.completed:
                incomplete = INCOMPLETE_REASONS.get(
                    result.incomplete_reason, "The defender did not complete the support exchange.",
                )
            elif not dispatcher.resolution:
                incomplete = "The defender did not record a final resolution."
            if isinstance(result.final_text, str) and result.final_text.strip()[:1600] != last_message:
                await emit_agent("message", {"text": result.final_text})
        except TimeoutError:
            incomplete = "The case reached its 35-second deadline."
        except Exception as error:
            logger.warning("Driver ended case %s (%s)", scenario.id, type(error).__name__)
            incomplete = "The defender provider did not finish the case."

        events = self.store.case_events(run_id, scenario.id, version)
        executed = self.store.ledger_total(case_run_id)
        outcome = "incomplete" if incomplete else dispatcher.resolution
        record = {
            "caseId": scenario.id, "label": scenario.label, "cohort": "held_out" if scenario.held_out else "paired",
            "version": version, "outcome": outcome, "authorization": dispatcher.authorization_status,
            "requestedCents": dispatcher.requested_cents, "executedCents": executed,
            "carrierCheck": dispatcher.carrier_check, "authorizationSource": dispatcher.decision_source,
            "source": source, "versionHash": version_hash,
            "evidenceIds": [event["id"] for event in events],
        }
        # Correlate with the trace opened for the first event, then persist the
        # reference in the same transaction as the case result and score event.
        if lookup_trace := getattr(self.trace_sink, "trace_ref", None):
            try:
                ref = lookup_trace(run_id, scenario.id, version)
                if ref is not None:
                    record["trace"] = TraceRef.model_validate(ref).dump()
            except Exception:
                logger.debug("Trace reference unavailable for case %s", case_run_id)
        if conversation_id:
            record["conversationId"] = conversation_id
        if dispatcher.authorization_receipt_id:
            record["authorizationReceiptId"] = dispatcher.authorization_receipt_id
        if incomplete:
            record["incompleteReason"] = incomplete
            verdict = incomplete
        else:
            record["score"] = score(scenario, outcome, events, executed)
            verdict = "The outcome met the case criteria." if record["score"]["passed"] else "The outcome missed one or more case criteria."
        score_event = self.store.finish_case(
            run_id, case_run_id, record,
            **dispatcher.event_fields(
                "case_scored", "evaluator", "Case incomplete" if incomplete else "Case evaluated",
                verdict, sourceTrust="system",
            ),
        )
        await self._observe(score_event)

    def view(self, run_id: str, owner: str) -> dict:
        row = self.store.get(run_id)
        owns_run = row["owner"] == owner
        cases = self.store.cases(run_id)
        view = {
            "id": run_id, "status": row["status"], "mode": row["mode"],
            "startedAt": row["started"], "updatedAt": row["updated"], "candidate": CANDIDATE,
            "progress": {"completed": len(cases), "total": 7},
            "actions": {
                "canRetest": owns_run and row["status"] == "ready_to_retest",
                "canReview": owns_run and row["status"] == "complete" and not row["reviewed"],
                "canStartVoice": owns_run and self.voice_available,
            },
            "cases": cases,
        }
        for name in ("finding", "revision", "error"):
            if row[name]:
                view[name] = json.loads(row[name])
        if row["status"] == "complete":
            view["review"] = {
                "status": "reviewed" if row["reviewed"] else "awaiting_review",
                "recommendation": recommendation(cases, row["mode"]),
            }
        return view
