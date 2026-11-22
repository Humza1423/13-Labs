"""Durable shift state and append-only, cursor-addressed evidence."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from thirteen_labs.contracts import CreateRunRequest


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class RunError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.payload = {"code": code, "message": message, "retryable": retryable}


class RunStore:
    def __init__(self, path: str | Path):
        self.path = path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                mode TEXT NOT NULL, started TEXT NOT NULL, updated TEXT NOT NULL,
                snapshot TEXT NOT NULL, baseline_hash TEXT NOT NULL,
                configuration TEXT NOT NULL, finding TEXT, revision TEXT,
                reviewed INTEGER NOT NULL DEFAULT 0, error TEXT,
                start_key TEXT NOT NULL, start_input TEXT NOT NULL,
                retest_key TEXT, retest_input TEXT,
                UNIQUE (owner, start_key)
            );
            CREATE TABLE IF NOT EXISTS case_runs (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                case_id TEXT NOT NULL, version TEXT NOT NULL,
                configuration TEXT NOT NULL, started TEXT NOT NULL,
                finished TEXT, result TEXT, UNIQUE (run_id, case_id, version)
            );
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL REFERENCES runs(id),
                seq INTEGER NOT NULL, id TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS refund_actions (
                id TEXT PRIMARY KEY, case_run_id TEXT NOT NULL REFERENCES case_runs(id),
                cents INTEGER NOT NULL, eligible INTEGER NOT NULL,
                authorization_source TEXT NOT NULL, ledger_event TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_entries (
                case_run_id TEXT NOT NULL REFERENCES case_runs(id),
                action_id TEXT NOT NULL, cents INTEGER NOT NULL,
                PRIMARY KEY (case_run_id, action_id),
                UNIQUE (case_run_id)
            );
            CREATE INDEX IF NOT EXISTS runs_mode_started ON runs(mode, started);
        """)

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def close(self):
        self.db.close()

    def get(self, run_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunError(404, "run_not_found", "This run could not be found.")
        return row

    def start(
        self, owner: str, key: str, selection: dict, mode: str, snapshot: list[dict],
        baseline_hash: str, configuration: dict, *, available: bool, capacity: bool,
        live_limit: int = 20,
    ) -> tuple[str, bool]:
        with self._transaction():
            existing = self.db.execute(
                "SELECT id, start_input FROM runs WHERE owner=? AND start_key=?", (owner, key),
            ).fetchone()
            if existing:
                if existing["start_input"] != encode(selection):
                    raise RunError(409, "idempotency_conflict", "This key was already used for another selection.")
                return existing["id"], False
            try:
                CreateRunRequest.model_validate(selection)
            except ValidationError:
                raise RunError(422, "invalid_selection", "Select the monitor support probation suite.") from None
            if not available:
                raise RunError(503, "batch_unavailable", "A defender driver is unavailable.", True)
            if not capacity:
                raise RunError(429, "queue_full", "The lab is busy. Try again shortly.", True)
            if mode == "live":
                cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
                count = self.db.execute(
                    "SELECT COUNT(*) AS total, COUNT(CASE WHEN owner=? THEN 1 END) AS owned "
                    "FROM runs WHERE mode='live' AND started>=?", (owner, cutoff),
                ).fetchone()
                if count["total"] >= live_limit or count["owned"] >= 4:
                    raise RunError(429, "live_start_limit", "The lab reached its hourly live start limit.", True)
            run_id = uuid4().hex
            timestamp = now()
            self.db.execute(
                "INSERT INTO runs (id, owner, status, mode, started, updated, snapshot, baseline_hash, "
                "configuration, start_key, start_input) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, owner, "queued", mode, timestamp, timestamp, encode(snapshot), baseline_hash,
                 encode(configuration), key, encode(selection)),
            )
            self._phase(run_id, "baseline", "Shift queued", "The baseline was added to the lab queue.")
            return run_id, True

    def retest(
        self, run_id: str, owner: str, key: str, revision_id: str, configuration: dict, capacity: bool,
    ) -> bool:
        with self._transaction():
            row = self.get(run_id)
            self._require_owner(row, owner)
            if row["retest_key"] == key:
                if row["retest_input"] != revision_id:
                    raise RunError(409, "idempotency_conflict", "This key was already used for another revision.")
                return False
            revision = json.loads(row["revision"] or "{}")
            if revision.get("id") != revision_id:
                raise RunError(409, "stale_revision", "Reload the shift to get the current revision.")
            if row["status"] != "ready_to_retest":
                raise RunError(409, "retest_unavailable", "The revision is no longer awaiting application.")
            if row["configuration"] != encode(configuration):
                raise RunError(409, "configuration_changed", "The lab configuration changed. Start a new shift.")
            if not capacity:
                raise RunError(429, "queue_full", "The lab is busy. Try again shortly.", True)
            self.db.execute(
                "UPDATE runs SET status='retest_running', updated=?, retest_key=?, retest_input=? "
                "WHERE id=? AND status='ready_to_retest'", (now(), key, revision_id, run_id),
            )
            self._phase(run_id, "retest", "Revision applied in lab", "The saved workflow is locked for the retest.")
            return True

    @staticmethod
    def _require_owner(row: sqlite3.Row, owner: str):
        if row["owner"] != owner:
            raise RunError(403, "owner_required", "Only the shift owner can take this action.")

    def review(self, run_id: str, owner: str):
        with self._transaction():
            row = self.get(run_id)
            self._require_owner(row, owner)
            if row["status"] != "complete":
                raise RunError(409, "review_unavailable", "The shift must finish before it can be reviewed.")
            self.db.execute(
                "UPDATE runs SET reviewed=1, updated=? WHERE id=? AND reviewed=0", (now(), run_id),
            )

    def transition(self, run_id: str, expected: str, status: str, stage: str, label: str, summary: str) -> bool:
        with self._transaction():
            updated = self.db.execute(
                "UPDATE runs SET status=?, updated=? WHERE id=? AND status=?", (status, now(), run_id, expected),
            )
            if not updated.rowcount:
                return False
            self._phase(run_id, stage, label, summary)
            return True

    def save_revision(self, run_id: str, finding: dict, revision: dict):
        with self._transaction():
            updated = self.db.execute(
                "UPDATE runs SET status='ready_to_retest', updated=?, finding=?, revision=? "
                "WHERE id=? AND status='investigating'",
                (now(), encode(finding), encode(revision), run_id),
            )
            if updated.rowcount:
                self._event(
                    run_id, stage="investigation", type="finding_published", actor="thirteen",
                    label="Finding published", summary=finding["summary"],
                )
                self._event(
                    run_id, stage="revision", type="revision_proposed", actor="thirteen",
                    label="Workflow revision proposed", summary=revision["after"],
                )

    def fail(self, run_id: str, code: str, message: str, stage: str):
        with self._transaction():
            updated = self.db.execute(
                "UPDATE runs SET status='failed', updated=?, error=? WHERE id=? "
                "AND status IN ('queued','baseline_running','investigating','retest_running')",
                (now(), encode({"code": code, "message": message, "retryable": True}), run_id),
            )
            if updated.rowcount:
                self._finish_interrupted_cases(run_id, message)
                self._event(
                    run_id, stage=stage, type="run_failed", actor="thirteen",
                    label="Shift interrupted", summary=message,
                )

    def _finish_interrupted_cases(self, run_id: str, message: str):
        rows = self.db.execute(
            "SELECT * FROM case_runs WHERE run_id=? AND finished IS NULL", (run_id,),
        ).fetchall()
        if not rows:
            return
        run = self.get(run_id)
        scenarios = {scenario["id"]: scenario for scenario in json.loads(run["snapshot"])}
        revision = json.loads(run["revision"] or "{}")
        for row in rows:
            case = scenarios[row["case_id"]]
            config = json.loads(row["configuration"])
            version = row["version"]
            events = self.case_events(run_id, case["id"], version)
            observed = {
                item["label"]: item["value"]
                for event in events
                if event["type"] in {"tool_returned", "refund_attempted", "authorization_decided"}
                for item in event.get("evidence", [])
            }
            record = {
                "caseId": case["id"], "label": case["label"],
                "cohort": "held_out" if case["held_out"] else "paired",
                "version": version, "outcome": "incomplete", "incompleteReason": message,
                "authorization": observed.get("Decision", "not_requested"),
                "requestedCents": int(observed.get("Requested cents", "0")),
                "executedCents": self.ledger_total(row["id"]),
                "carrierCheck": observed.get("Carrier check", "not_called"),
                "authorizationSource": observed.get("Authorization source", "local"), "source": config["source"],
                "versionHash": run["baseline_hash"] if version == "baseline" else revision["versionHash"],
                "evidenceIds": [event["id"] for event in events],
            }
            if receipt_id := observed.get("Authorization receipt"):
                record["authorizationReceiptId"] = receipt_id
            self.db.execute(
                "UPDATE case_runs SET finished=?, result=? WHERE id=?",
                (now(), encode(record), row["id"]),
            )

    def recover(self):
        rows = self.db.execute(
            "SELECT id, status FROM runs WHERE status IN ('queued','baseline_running','investigating','retest_running')",
        ).fetchall()
        for row in rows:
            stage = {"investigating": "investigation", "retest_running": "retest"}.get(row["status"], "baseline")
            self.fail(row["id"], "interrupted", "The lab restarted during this shift. Start a new shift to retry.", stage)

    def begin_case(self, run_id: str, case_id: str, version: str, configuration: dict) -> str:
        case_run_id = uuid4().hex
        self.db.execute(
            "INSERT INTO case_runs (id,run_id,case_id,version,configuration,started) VALUES (?,?,?,?,?,?)",
            (case_run_id, run_id, case_id, version, encode(configuration), now()),
        )
        return case_run_id

    def finish_case(self, run_id: str, case_run_id: str, result: dict, **score_event):
        with self._transaction():
            event = self._event(run_id, **score_event)
            saved = {**result, "evidenceIds": [*result["evidenceIds"], event["id"]]}
            self.db.execute(
                "UPDATE case_runs SET finished=?, result=? WHERE id=? AND finished IS NULL",
                (now(), encode(saved), case_run_id),
            )
            self.db.execute("UPDATE runs SET updated=? WHERE id=?", (now(), run_id))
            return event

    def cases(self, run_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT result FROM case_runs WHERE run_id=? AND result IS NOT NULL ORDER BY rowid", (run_id,),
        )
        return [json.loads(row["result"]) for row in rows]

    def _phase(self, run_id: str, stage: str, label: str, summary: str):
        return self._event(run_id, stage=stage, type="phase_changed", actor="thirteen", label=label, summary=summary)

    def _event(self, run_id: str, **fields) -> dict:
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,),
        ).fetchone()[0]
        event = {"id": uuid4().hex, "seq": seq, "at": now(), "runId": run_id, **fields}
        self.db.execute(
            "INSERT INTO events (run_id,seq,id,body) VALUES (?,?,?,?)",
            (run_id, seq, event["id"], encode(event)),
        )
        return event

    def event(self, run_id: str, **fields) -> dict:
        with self._transaction():
            return self._event(run_id, **fields)

    def events(self, run_id: str, after: int, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            "SELECT body FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, after, limit),
        )
        return [json.loads(row["body"]) for row in rows]

    def case_events(self, run_id: str, case_id: str, version: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT body FROM events WHERE run_id=? AND json_extract(body, '$.caseId')=? "
            "AND json_extract(body, '$.version')=? ORDER BY seq", (run_id, case_id, version),
        )
        return [json.loads(row["body"]) for row in rows]

    def prepare_refund(
        self, run_id: str, case_run_id: str, action_id: str, cents: int, eligible: bool,
        authorization_source: str, attempt_event: dict, ledger_event: dict,
    ) -> dict:
        with self._transaction():
            inserted = self.db.execute(
                "INSERT INTO refund_actions (id,case_run_id,cents,eligible,authorization_source,ledger_event) "
                "SELECT ?,id,?,?,?,? FROM case_runs WHERE id=? AND run_id=? AND finished IS NULL",
                (action_id, cents, eligible, authorization_source, encode(ledger_event), case_run_id, run_id),
            )
            if not inserted.rowcount:
                raise RunError(409, "refund_case_unavailable", "This refund case is no longer active.")
            return self._event(run_id, **attempt_event)

    def execute_refund(self, action_id: str, *, authorization_source: str) -> dict | None:
        """Execute a server-issued action after the caller checks authorization."""
        with self._transaction():
            row = self.db.execute(
                "SELECT a.*, c.run_id, c.finished FROM refund_actions a "
                "JOIN case_runs c ON c.id=a.case_run_id WHERE a.id=? AND a.authorization_source=?",
                (action_id, authorization_source),
            ).fetchone()
            if row is None or not row["eligible"]:
                raise RunError(404, "refund_action_unavailable", "This refund action is unavailable.")
            if self.ledger_total(row["case_run_id"]):
                return None
            if row["finished"]:
                raise RunError(409, "refund_action_expired", "This refund action is no longer active.")
            self.db.execute(
                "INSERT INTO ledger_entries (case_run_id,action_id,cents) VALUES (?,?,?)",
                (row["case_run_id"], action_id, row["cents"]),
            )
            return self._event(row["run_id"], **json.loads(row["ledger_event"]))

    def ledger_total(self, case_run_id: str) -> int:
        return self.db.execute(
            "SELECT COALESCE(SUM(cents),0) FROM ledger_entries WHERE case_run_id=?", (case_run_id,),
        ).fetchone()[0]
