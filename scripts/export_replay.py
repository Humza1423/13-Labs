#!/usr/bin/env python3
"""Export a completed measured run and import a read-only copy into the lab."""

import argparse
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from uuid import NAMESPACE_URL, uuid4, uuid5

from thirteen_labs.contracts import EventsPage, PreviewFixture, RunView
from thirteen_labs.store import RunStore, encode

READ_ONLY = {"canRetest": False, "canReview": False, "canStartVoice": False}


def fetch(url):
    with urlopen(url, timeout=15) as response:
        return json.load(response)


def validate(document):
    if document.get("schemaVersion") != 1:
        raise ValueError("Unsupported replay snapshot version.")
    snapshot = PreviewFixture.model_validate({"run": document["run"], "events": document["events"]})
    run = snapshot.run
    if run.id != document["originalRunId"] or run.updated_at != document["completedAt"]:
        raise ValueError("The snapshot metadata does not match the exported run.")
    if run.status != "complete" or run.mode != "live" or not run.cases:
        raise ValueError("Replay requires a completed live run.")
    sources = {case.source for case in run.cases}
    if "fixture" in sources or sources != set(document["providers"]):
        raise ValueError("The snapshot does not preserve measured provider provenance.")
    if not any(case.conversation_id and case.outcome != "incomplete" for case in run.cases):
        raise ValueError("Replay requires a completed provider conversation.")
    seq = [event.seq for event in snapshot.events]
    event_ids = {event.id for event in snapshot.events}
    if not seq or seq != list(range(1, len(seq) + 1)) or len(event_ids) != len(seq):
        raise ValueError("The event stream must contain the complete, ordered sequence.")
    if any(event.run_id != run.id for event in snapshot.events):
        raise ValueError("The event stream contains another run's evidence.")
    references = [item.evidence_ids for item in run.cases]
    references += [item.event_ids for item in (run.finding, run.revision) if item]
    if any(not set(ids).issubset(event_ids) for ids in references):
        raise ValueError("The snapshot refers to missing evidence.")
    last = snapshot.events[-1]
    if last.type != "phase_changed" or last.stage != "review":
        raise ValueError("The snapshot is missing the final review transition.")
    return snapshot


def export_run(base_url, run_id):
    endpoint = f"{base_url.rstrip('/')}/api/runs/{quote(run_id, safe='')}"
    run = RunView.model_validate(fetch(endpoint))
    if run.id != run_id or run.status != "complete" or run.mode != "live":
        raise ValueError("Select a completed live run from the deployed lab.")
    events = []
    cursor = 0
    while True:
        page = EventsPage.model_validate(fetch(f"{endpoint}/events?after={cursor}"))
        if not page.events:
            break
        if page.events[0].seq <= cursor or page.events[-1].seq != page.next_after:
            raise ValueError("The deployed lab returned an invalid event cursor.")
        events.extend(event.dump() for event in page.events)
        cursor = page.next_after
    document = {
        "schemaVersion": 1,
        "originalRunId": run.id,
        "completedAt": run.updated_at,
        "providers": sorted({case.source for case in run.cases}),
        "run": {**run.dump(), "actions": READ_ONLY},
        "events": events,
    }
    validate(document)
    return document


def retain_replay(document, database):
    snapshot = validate(document)
    replay_id = uuid5(NAMESPACE_URL, f"13-labs/replay/{encode(document)}").hex
    event_ids = {event.id: uuid5(NAMESPACE_URL, f"{replay_id}/{event.id}").hex for event in snapshot.events}
    run = snapshot.run
    narrative = []
    for item in (run.finding, run.revision):
        narrative.append(encode({**item.dump(), "eventIds": [event_ids[key] for key in item.event_ids]}) if item else None)

    store = RunStore(database)
    try:
        with store._transaction():
            exists = store.db.execute("SELECT mode FROM runs WHERE id=?", (replay_id,)).fetchone()
            if exists:
                if exists["mode"] != "replay":
                    raise ValueError("The replay identifier is already in use.")
                return replay_id
            store.db.execute(
                "INSERT INTO runs (id,owner,status,mode,started,updated,snapshot,baseline_hash,configuration,"
                "finding,revision,reviewed,start_key,start_input) VALUES (?,?,'complete','replay',?,?,?,?,?,?,?,?,?,?)",
                (replay_id, "replay", run.started_at, run.updated_at, "[]", "",
                 encode({"originalRunId": run.id, "providers": document["providers"]}), *narrative,
                 int(run.review is not None and run.review.status == "reviewed"), replay_id, "{}"),
            )
            for case in run.cases:
                result = {**case.dump(), "evidenceIds": [event_ids[key] for key in case.evidence_ids]}
                store.db.execute(
                    "INSERT INTO case_runs (id,run_id,case_id,version,configuration,started,finished,result) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uuid4().hex, replay_id, case.case_id, case.version, "{}", run.started_at, run.updated_at, encode(result)),
                )
            for event in snapshot.events:
                published = {**event.dump(), "id": event_ids[event.id], "runId": replay_id}
                store.db.execute(
                    "INSERT INTO events (run_id,seq,id,body) VALUES (?,?,?,?)",
                    (replay_id, event.seq, published["id"], encode(published)),
                )
    finally:
        store.close()
    return replay_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("base_url")
    export.add_argument("run_id")
    export.add_argument("file", type=Path)
    restore = commands.add_parser("import")
    restore.add_argument("file", type=Path)
    restore.add_argument("--db", required=True)
    args = parser.parse_args()
    if args.command == "export":
        document = export_run(args.base_url, args.run_id)
        args.file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    else:
        print(retain_replay(json.loads(args.file.read_text(encoding="utf-8")), args.db))


if __name__ == "__main__":
    main()
