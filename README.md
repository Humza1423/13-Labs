<div align="center">

# 13 LABS

### Your AI’s first bad customer shouldn’t be a real one.

**An adversarial simulation sandbox for building more reliable customer service agents.**

Before you hire your next AI coworker, send it to 13.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?style=flat-square&logo=fastapi&logoColor=white) ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white) ![React](https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black) ![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)

[The sandbox](#the-sandbox) · [Architecture](#architecture) · [Engineering](#engineering-highlights) · [Run locally](#run-locally)

<img src="web/tests/screenshots/lab-fixture-1440x900.png" width="900" alt="13 Labs AI probation shift interface, showing the customer service candidate in a simulated workplace">

<sub>Application screenshot from the scripted design-preview fixture.</sub>

</div>

## The problem

Building an AI agent is becoming easier. Getting one ready for real customers is not.

A product manager can prototype a customer service agent in an afternoon, but making it reliable requires engineers to simulate customers, uncover security failures, investigate tool calls, revise workflows and repeatedly test the changes. Smaller teams should not have to build an entire internal reliability platform just to ship an agent.

**13 Labs is our prototype of an on-demand AI reliability department.** Instead of exposing an untested agent to real customers, put it through a probation shift in a controlled sandbox. Simulated customers challenge its decisions, an evaluator investigates failures, and the revised workflow is tested again before a human makes a deployment decision.

The goal is not to make agents reject everything. It is to prevent fraudulent or unsupported actions while preserving good service for legitimate customers.

## The sandbox

Our first implementation models a fictional electronics retailer with $900 damaged-monitor refund claims. Every customer, order, carrier record and financial transaction is synthetic.

| Customer persona | What the sandbox tests | Expected behavior |
| :--- | :--- | :--- |
| Legitimate customer | A corroborated damage claim | Complete the valid refund |
| Adversarial customer | A claim with conflicting shipping evidence | Verify trusted records and decline the unsupported claim |
| Customer with missing evidence | A claim that cannot yet be verified | Explain the missing information and provide a helpful next step |
| Held-out adversarial customer | A new claim with a different shipment mismatch | Generalize the revised policy to a case not used in the baseline |

The current customer personas use bounded, authored dialogue. The defending agent can run through a live model provider; open-ended attacker agents and dynamic persona generation are the next step.

### The AI probation shift

```mermaid
flowchart LR
    A["Customer personas<br/>Legitimate · Difficult · Adversarial"] --> B["Baseline sandbox<br/>Defending agent + tools"]
    B --> C["Investigate<br/>Conversation + action evidence"]
    C --> D["Revise<br/>Versioned workflow"]
    D --> E["Retest<br/>Paired + held-out cases"]
    E --> F["Human review<br/>Security + service outcomes"]
```

1. **Break it.** Run the candidate against three customer scenarios and observe its conversation, business-tool calls and refund decisions.
2. **Understand it.** Independently score the behavior and identify the evidence behind failures, such as an unsupported refund attempt before a trusted carrier check.
3. **Improve it.** Propose a targeted, versioned workflow revision that requires verification and a useful next step when evidence is missing.
4. **Prove it.** Rerun the same three cases plus a held-out case. Compare security and customer-service outcomes side by side, then present the findings for human review.

<div align="center">
<img src="web/tests/screenshots/report-fixture-1440x900.png" width="900" alt="13 Labs evaluation dashboard comparing baseline and revised agent decisions, evidence and case outcomes">
<br>
<sub>Evaluation dashboard — scripted fixture preview, not a live performance benchmark.</sub>
</div>

### What we measure

| Security | Customer experience | Reliability |
| :--- | :--- | :--- |
| Unsupported refund attempts | Valid claims completed | Independent case pass/fail |
| Unauthorized refund executions | Unnecessary refusals or escalations | Trusted carrier checks performed |

The suite defines **4 scenarios, 3 baseline executions and 4 revised executions** per completed probation shift. Attempts and actual executions are counted separately, so a blocked refund is never reported as money lost. Incomplete cases are reported explicitly rather than silently scored as successes.

The dashboard screenshots demonstrate the product experience using fixtures. They do not establish a real-world fraud-prevention percentage.

## Architecture

```mermaid
flowchart TD
    UI["React + TypeScript<br/>Sandbox UI · Evidence viewer · Report"] <--> API["FastAPI<br/>Runs · Events · Retest · Review"]
    API --> Engine["Async shift engine"]
    Engine --> Scenarios["Synthetic customer scenarios"]
    Engine <--> Defender["Defender adapter<br/>Anthropic / ElevenLabs / Fixture"]
    Defender <--> Tools["Business-tool dispatcher<br/>Order lookup · Carrier verification · Refund"]
    Tools --> Gate["Authorization gate<br/>Local / optional Alter"]
    Gate --> Ledger["Synthetic refund ledger"]
    Engine --> Eval["Independent evaluator<br/>Investigation + versioned revision"]
    Engine --> Store[("SQLite<br/>Run state + append-only evidence")]
    Ledger --> Store
    Eval --> Store
    Engine -.-> Trace["Optional TraceRoot / OpenTelemetry"]
```

## Engineering highlights

| Component | Implementation |
| :--- | :--- |
| **Execution engine** | Async baseline/retest state machine, bounded case execution, queue limits, and failure recovery. [Engine](src/thirteen_labs/engine/service.py) |
| **Agent integrations** | Provider-independent driver interface, Anthropic tool calling, ElevenLabs text/voice integrations, and deterministic fixture mode. [Adapters](src/thirteen_labs/integrations/) |
| **Safety boundaries** | Synthetic business tools, a separate authorization gate, and a ledger that distinguishes proposed actions from actual executions. [Tools](src/thirteen_labs/engine/tools.py) · [Alter adapter](src/thirteen_labs/integrations/alter.py) |
| **Evaluation** | Independent outcome scoring, evidence-linked findings, SHA-256 workflow hashes, paired comparisons and a held-out case. [Evaluator](src/thirteen_labs/engine/evaluation.py) |
| **Evidence and persistence** | SQLite-backed run state, append-only cursor-addressed events, provider provenance and optional trace export. [Store](src/thirteen_labs/store/) · [Tracing](docs/traceroot.md) |
| **API and UI** | Idempotent start/retest requests, typed Pydantic/TypeScript contracts, interactive evidence and case-level comparison reports. [API](src/thirteen_labs/routes/runs.py) · [Frontend](web/src/) |
| **Testing** | Pytest engine and HTTP acceptance tests, provider integration tests, Vitest frontend tests, and a fresh-checkout smoke script. [Backend tests](tests/) · [Frontend tests](web/tests/) |

**Stack:** Python 3.12, FastAPI, asyncio, Pydantic, SQLite, React, TypeScript, Vite, Anthropic, ElevenLabs, OpenTelemetry, TraceRoot, Alter, Docker, pytest and Vitest.

## Run locally

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/) and Node.js compatible with the repository’s Vite version (use Node 22.12+).

```bash
git clone https://github.com/Humza1423/13-Labs.git
cd 13-Labs
uv sync --frozen
npm ci --prefix web
```

**Try the sandbox without provider credentials.** Start the API in fixture mode:

```bash
DEMO_MODE=fixture uv run uvicorn thirteen_labs.app:app --port 8000
```

In a second terminal:

```bash
npm run dev --prefix web
```

Open [localhost:5173](http://localhost:5173). Fixture mode is deterministic and does not make live model calls; it is intended for inspecting the workflow and interface.

**Run with a live defending agent.** Copy `.env.example` to `.env`, configure `ANTHROPIC_API_KEY` and a supported `LAB_MODEL` (or the ElevenLabs provider settings), then launch:

```bash
uv run uvicorn thirteen_labs.app:app --env-file .env --port 8000
```

Optional integrations and provider configuration are documented in [.env.example](.env.example). Never commit provider credentials.

| Local endpoint | Purpose |
| :--- | :--- |
| `http://localhost:5173` | Web application |
| `http://localhost:8000/api/docs` | Interactive API documentation |
| `http://localhost:8000/api/health` | Service health and capabilities |

### Test and build

```bash
uv run pytest -q
npm test --prefix web
npm run build --prefix web
```

The production build can be served by FastAPI on the same origin. For reproducible live evaluation, start a configured live API and use the [measurement script](scripts/measure_live_shift.py):

```bash
uv run python scripts/measure_live_shift.py http://127.0.0.1:8000
```

The script rejects fixture/replay results as live evidence and prints observed case outcomes and trace references.

## Where we are taking 13 Labs

This repository is a working hackathon prototype of the probation-shift concept, not a general-purpose production security guarantee. The current revision is rule-based and specific to the monitor-refund workflow.

- [x] Controlled customer-service sandbox and synthetic refund ledger
- [x] Live defending-agent adapters and reproducible fixture runs
- [x] Evidence-linked investigation, versioned workflow revisions and held-out evaluation
- [ ] Model-driven attacker personas with varied customer and scam strategies
- [ ] Configurable business policies and bring-your-own-agent integration
- [ ] Evidence-driven generation of workflow changes and iterative optimization
- [ ] Larger evaluation suites, repeatable live benchmarks and deployment gates

## Team and contributions

Originally built with Rayan and Han during a hackathon. This is my personal development fork of the [original team repository](https://github.com/rayanali2/13-Labs).

My contributions include:

- Implementing the [probation shift execution engine and runs API](https://github.com/rayanali2/13-Labs/pull/1).
- Integrating [live Anthropic defender tool calls](https://github.com/rayanali2/13-Labs/pull/4) and testing all seven evaluation legs with controlled provider responses.
- Persisting [case-level trace references and refund evidence](https://github.com/rayanali2/13-Labs/pull/10).
- Improving the [recording, evidence, retest and review flow](https://github.com/rayanali2/13-Labs/pull/11).

[Original development notes](docs/hackathon-development.md) · [Third-party asset credits](web/src/assets/THIRD-PARTY.txt)

---

<div align="center">
<strong>13 Labs — Break it. Understand it. Improve it.</strong>
</div>
