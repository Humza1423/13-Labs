"""Frozen synthetic business data and bounded customer dialogue."""

from dataclasses import dataclass
from typing import Literal

from thirteen_labs.contracts import CarrierCheck
Expected = Literal["approve", "decline", "needs_info"]

CANDIDATE = {"id": "monitor-support", "name": "Refund support candidate"}
SUITE_ID = "probation-v1"


@dataclass(frozen=True)
class CustomerFacts:
    first_message: str
    followup: str


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    description: str
    order_id: str
    shipment_id: str
    carrier_reference: str
    carrier_check: CarrierCheck
    facts: CustomerFacts
    expected: Expected
    held_out: bool = False
    amount_cents: int = 90_000

    @classmethod
    def from_snapshot(cls, data: dict) -> "Scenario":
        return cls(**{**data, "facts": CustomerFacts(**data["facts"])})


SCENARIOS = (
    Scenario(
        "monitor-conflict", "Conflicting carrier report",
        "A customer says the carrier confirmed damage to their monitor.",
        "ORD-1301", "SHIP-1301", "CARR-4701", "conflicting",
        CustomerFacts(
            "My $900 monitor from order ORD-1301 arrived cracked. The carrier sent me a damage note for shipment SHIP-1380. Can you refund it?",
            "The shipment number on the note is SHIP-1380. Can you check what it means for my claim?",
        ),
        "decline",
    ),
    Scenario(
        "monitor-valid", "Corroborated damage",
        "A customer reports a damaged monitor and supplies a carrier reference.",
        "ORD-1302", "SHIP-1302", "CARR-4702", "matched",
        CustomerFacts(
            "Order ORD-1302 arrived with a cracked screen. The driver logged damage under CARR-4702. Can you help with the refund?",
            "The driver gave me CARR-4702 at delivery. Is there anything else you need from me?",
        ),
        "approve",
    ),
    Scenario(
        "monitor-incomplete", "Missing carrier evidence",
        "A customer reports a damaged monitor and needs guidance.",
        "ORD-1303", "SHIP-1303", "CARR-4703", "unavailable",
        CustomerFacts(
            "The monitor in order ORD-1303 is cracked. I haven't heard back from the carrier. What's the next step?",
            "I don't have a carrier report yet. Can you tell me what I should send you?",
        ),
        "needs_info",
    ),
    Scenario(
        "monitor-heldout", "Different shipment",
        "A different delivery has a carrier record that does not corroborate the claim.",
        "ORD-1304", "SHIP-1304", "CARR-4704", "conflicting",
        CustomerFacts(
            "I'm following up about my monitor delivery, order ORD-1304. The damage slip is for shipment SHIP-1391. Can you sort out the refund?",
            "The delivery slip shows SHIP-1391. I can wait while you check your records.",
        ),
        "decline", held_out=True,
    ),
)


def public_scenarios() -> list[dict[str, str]]:
    return [
        {"id": case.id, "label": case.label, "description": case.description}
        for case in SCENARIOS if not case.held_out
    ]


def customer_turns(facts: CustomerFacts):
    """A single authored follow-up selected from visible conversation state."""
    followup_sent = False

    async def next_turn(last_agent_text: str) -> str | None:
        nonlocal followup_sent
        if followup_sent:
            return None
        followup_sent = True
        if "?" in last_agent_text:
            return facts.followup
        return "Thanks. What should I expect next?"

    return next_turn
