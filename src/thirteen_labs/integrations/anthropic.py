"""Anthropic Messages adapter for one isolated, bounded support case."""

import asyncio
import json

from anthropic import APIError, AsyncAnthropic

from thirteen_labs.engine.tools import TOOL_SCHEMAS
from thirteen_labs.ports import CallTool, DriverResult, DriverSpec, Emit, NextCustomerTurn


class AnthropicDefenderDriver:
    name = "anthropic-messages"

    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self.model = model

    async def run_case(
        self, spec: DriverSpec, next_customer_turn: NextCustomerTurn, call_tool: CallTool, emit: Emit,
    ) -> DriverResult:
        tools = [
            {"name": name, "description": description, "input_schema": schema.model_json_schema()}
            for name, (schema, description) in TOOL_SCHEMAS.items()
        ]
        messages = [{"role": "user", "content": spec.first_message}]
        customer_turns = 1
        tool_calls = 0
        tool_limit = min(spec.max_tool_calls, 8)
        conversation_id = None
        final_text = None
        resolved = False
        overflow = False

        def result(reason: str | None = None) -> DriverResult:
            return DriverResult(conversation_id, final_text, reason is None, reason, source="model")

        try:
            async with asyncio.timeout(min(spec.deadline_s, 35)), AsyncAnthropic(
                api_key=self._api_key, max_retries=0,
            ) as client:
                while True:
                    reply = await client.messages.create(
                        model=self.model, system=spec.defender_prompt, messages=messages,
                        max_tokens=640, tools=tools,
                        tool_choice={"type": "none" if tool_calls >= tool_limit else "auto"},
                    )
                    # Messages is stateless. The first provider message ID identifies this exchange.
                    if conversation_id is None:
                        conversation_id = reply.id
                        await emit("provider_message", {"id": reply.id})
                    if reply.stop_reason not in {"tool_use", "end_turn", "stop_sequence"}:
                        return result("provider_stopped")
                    if tool_calls >= tool_limit and any(block.type == "tool_use" for block in reply.content):
                        return result("tool_limit")
                    messages.append({"role": "assistant", "content": reply.content})
                    results = []
                    for block in reply.content:
                        if block.type == "text" and block.text.strip():
                            final_text = block.text
                            await emit("message", {"text": block.text})
                        elif block.type == "tool_use":
                            if tool_calls >= tool_limit:
                                output = {"error": "tool_limit_reached"}
                                overflow = True
                            else:
                                tool_calls += 1
                                output = await call_tool(block.id, block.name, block.input)
                                if block.name == "record_resolution" and "outcome" in output:
                                    resolved = True
                            results.append({
                                "type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(output), "is_error": "error" in output,
                            })
                    if results:
                        messages.append({"role": "user", "content": results})
                        continue
                    if overflow:
                        return result("tool_limit")
                    if resolved:
                        return result()
                    if customer_turns >= min(spec.max_customer_turns, 4):
                        return result("turn_limit")
                    if tool_calls >= tool_limit:
                        return result("tool_limit")
                    next_turn = await next_customer_turn(final_text or "")
                    if not next_turn:
                        return result("no_resolution")
                    customer_turns += 1
                    messages.append({"role": "user", "content": next_turn})
        except TimeoutError:
            return result("deadline")
        except APIError:
            return result("provider_error")
