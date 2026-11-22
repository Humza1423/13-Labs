"""Run one live monitor shift against an authorized 13 Labs origin and print observed evidence."""

import argparse
import json
import time
from uuid import uuid4

import httpx


def post(client, path, body):
    response = client.post(path, json=body, headers={"Idempotency-Key": uuid4().hex})
    response.raise_for_status()
    return response.json()


def wait_for(client, path, status):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        run = response.json()
        if run["status"] == status:
            return run
        if run["status"] == "failed":
            raise RuntimeError(f"{run['id']} failed: {run.get('error')}")
        time.sleep(.75)
    raise TimeoutError(f"{path} did not reach {status}")


def measure(client):
    run = post(client, "/api/runs", {"candidateId": "monitor-support", "suiteId": "probation-v1"})
    path = f"/api/runs/{run['id']}"
    print(f"Run {run['id']} ({run['mode']})", flush=True)
    if run["mode"] != "live":
        raise RuntimeError("This environment selected a fixture or replay. Live measurement is unavailable.")
    baseline = wait_for(client, path, "ready_to_retest")
    # Fetch all evidence pages, independently of the number of emitted messages.
    after = 0
    disputed = []
    while True:
        response = client.get(path + "/events", params={"after": after})
        response.raise_for_status()
        page = response.json()
        disputed.extend(event for event in page["events"] if event.get("caseId") == "monitor-conflict"
                        and event.get("version") == "baseline")
        if not page["events"]:
            break
        after = page["nextAfter"]
    post(client, path + "/retest", {"revisionId": baseline["revision"]["id"]})
    final = wait_for(client, path, "complete")
    print(json.dumps({
        "runId": run["id"],
        "baselineRefundEventIds": [e["id"] for e in disputed if e["type"] == "refund_attempted"],
        "baselineEvidence": [{"id": e["id"], "type": e["type"], "tool": e.get("tool")} for e in disputed],
        "review": final["review"],
        "cases": [{key: case.get(key) for key in (
            "caseId", "version", "source", "conversationId", "outcome", "carrierCheck", "requestedCents",
            "executedCents", "incompleteReason", "score",
        )} for case in final["cases"]],
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="13 Labs API origin, e.g. http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.origin, timeout=15) as client:
        measure(client)
