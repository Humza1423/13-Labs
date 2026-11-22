"""Private ElevenLabs sessions and the live text drivers for the refund lab."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import httpx
from anthropic import APIError, AsyncAnthropic
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from thirteen_labs.config import Settings
from thirteen_labs.engine.tools import TOOL_SCHEMAS, public_id
from thirteen_labs.ports import CallTool, DefenderDriver, DriverResult, DriverSpec, Emit, NextCustomerTurn

SIGNED_URL_ENDPOINT = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"


class ElevenLabsUnavailable(Exception):
    """ElevenLabs did not issue a usable private conversation URL."""


async def signed_conversation_url(api_key: str, agent_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                SIGNED_URL_ENDPOINT,
                headers={"xi-api-key": api_key},
                params={"agent_id": agent_id, "include_conversation_id": "true"},
            )
            response.raise_for_status()
            url = response.json()["signed_url"]
        if not isinstance(url, str) or urlsplit(url).scheme != "wss" or not urlsplit(url).hostname:
            raise ValueError("Expected a secure WebSocket URL")
        return url
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ElevenLabsUnavailable("Could not start a private ElevenLabs conversation.") from error


def voice_enabled(settings: Settings) -> bool:
    return bool(
        settings.demo_mode == "live"
        and settings.elevenlabs_api_key
        and settings.elevenlabs_voice_agent_id
        and settings.elevenlabs_webhook_secret
        and settings.elevenlabs_voice_agent_id != settings.elevenlabs_lab_agent_id
    )


class ElevenLabsDriver:
    name = "elevenlabs"

    def __init__(self, api_key: str, agent_id: str):
        self.api_key = api_key
        self.agent_id = agent_id

    async def run_case(
        self, spec: DriverSpec, next_customer_turn: NextCustomerTurn, call_tool: CallTool, emit: Emit,
    ) -> DriverResult:
        conversation_id = None
        last_text = None
        resolved = False
        customer_turns = 0
        calls: dict[str, tuple[str, dict, dict]] = {}

        def result(completed: bool, reason: str | None = None) -> DriverResult:
            return DriverResult(conversation_id, last_text, completed, reason, source="elevenlabs")

        try:
            async with asyncio.timeout(spec.deadline_s):
                url = await signed_conversation_url(self.api_key, self.agent_id)
                async with connect(url, subprotocols=["convai"], open_timeout=8, close_timeout=1) as socket:
                    await socket.send(json.dumps({
                        "type": "conversation_initiation_client_data",
                        "conversation_config_override": {
                            "agent": {"prompt": {"prompt": spec.defender_prompt}},
                        },
                    }))
                    async for raw in socket:
                        event = json.loads(raw)
                        match event.get("type"):
                            case "ping":
                                await socket.send(json.dumps({
                                    "type": "pong", "event_id": event["ping_event"]["event_id"],
                                }))
                            case "conversation_initiation_metadata" if not conversation_id:
                                conversation_id = public_id(event["conversation_initiation_metadata_event"]["conversation_id"])
                                if not conversation_id:
                                    return result(False, "ElevenLabs did not return a conversation ID.")
                                await emit("provider_conversation", {"conversationId": conversation_id})
                                await socket.send(json.dumps({"type": "user_message", "text": spec.first_message}))
                                customer_turns = 1
                            case "client_tool_call" if conversation_id and customer_turns:
                                call = event["client_tool_call"]
                                call_id = public_id(call.get("tool_call_id"))
                                name = call.get("tool_name")
                                arguments = call.get("parameters", {})
                                if not call_id or not isinstance(name, str) or not isinstance(arguments, dict):
                                    return result(False, "ElevenLabs returned an invalid tool request.")
                                prior = calls.get(call_id)
                                if prior:
                                    output = prior[2] if prior[:2] == (name, arguments) else {"error": "conflicting_tool_retry"}
                                elif len(calls) >= spec.max_tool_calls:
                                    return result(False, "The provider reached the case tool limit.")
                                else:
                                    output = await call_tool(call_id, name, arguments) if name in TOOL_SCHEMAS else {"error": "unknown_tool"}
                                    calls[call_id] = (name, arguments, output)
                                await socket.send(json.dumps({
                                    "type": "client_tool_result", "tool_call_id": call_id,
                                    "result": json.dumps(output), "is_error": "error" in output,
                                }))
                                if name == "record_resolution" and "outcome" in output:
                                    resolved = True
                            case "agent_response" if conversation_id and customer_turns:
                                text = event["agent_response_event"].get("agent_response")
                                if not isinstance(text, str) or not text.strip():
                                    continue
                                last_text = text.strip()[:1600]
                                await emit("message", {"text": last_text})
                                if resolved:
                                    return result(True)
                                if customer_turns >= spec.max_customer_turns:
                                    return result(False, "The case reached its customer-turn limit.")
                                text = await next_customer_turn(last_text)
                                if text is None:
                                    return result(False, "The customer exchange ended before a resolution was recorded.")
                                await socket.send(json.dumps({"type": "user_message", "text": text}))
                                customer_turns += 1
                            case "error":
                                return result(False, "ElevenLabs reported a conversation error.")
        except TimeoutError:
            return result(False, "The ElevenLabs conversation reached its deadline.")
        except (ElevenLabsUnavailable, WebSocketException, OSError, KeyError, TypeError, ValueError):
            return result(False, "The ElevenLabs conversation could not complete.")
        return result(False, "ElevenLabs disconnected before a final response.")


class AnthropicDriver:
    """Use one configured model for all legs when the lab agent is unavailable."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def run_case(
        self, spec: DriverSpec, next_customer_turn: NextCustomerTurn, call_tool: CallTool, emit: Emit,
    ) -> DriverResult:
        tools = [
            {"name": name, "description": help_text, "input_schema": model.model_json_schema()}
            for name, (model, help_text) in TOOL_SCHEMAS.items()
        ]
        messages = [{"role": "user", "content": spec.first_message}]
        response_id = None
        last_text = None
        resolved = False
        tool_calls = 0

        def result(completed: bool, reason: str | None = None) -> DriverResult:
            return DriverResult(response_id, last_text, completed, reason, source="model")

        try:
            async with asyncio.timeout(spec.deadline_s), AsyncAnthropic(api_key=self.api_key, max_retries=0) as client:
                for turn in range(spec.max_customer_turns):
                    while True:
                        response = await client.messages.create(
                            model=self.model, system=spec.defender_prompt, max_tokens=420,
                            messages=messages, tools=tools,
                        )
                        response_id = response_id or response.id
                        content = []
                        tool_results = []
                        response_text = []
                        for block in response.content:
                            if block.type == "text":
                                content.append({"type": "text", "text": block.text})
                                response_text.append(block.text)
                            elif block.type == "tool_use":
                                if tool_calls >= spec.max_tool_calls:
                                    return result(False, "The model reached the case tool limit.")
                                tool_calls += 1
                                output = await call_tool(block.id, block.name, block.input)
                                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
                                tool_results.append({
                                    "type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(output), "is_error": "error" in output,
                                })
                                if block.name == "record_resolution" and "outcome" in output:
                                    resolved = True
                        messages.append({"role": "assistant", "content": content})
                        if response_text:
                            last_text = "\n".join(response_text).strip()[:1600]
                            await emit("message", {"text": last_text})
                        if tool_results:
                            messages.append({"role": "user", "content": tool_results})
                            continue
                        if resolved and response_text:
                            return result(True)
                        if not response_text or turn + 1 == spec.max_customer_turns:
                            return result(False, "The model did not give a resolved final reply.")
                        next_text = await next_customer_turn(last_text)
                        if next_text is None:
                            return result(False, "The customer exchange ended before a resolution was recorded.")
                        messages.append({"role": "user", "content": next_text})
                        break
        except TimeoutError:
            return result(False, "The model conversation reached its deadline.")
        except APIError:
            return result(False, "The model provider could not finish the conversation.")
        return result(False, "The model did not give a resolved final reply.")


def configured_driver(settings: Settings) -> DefenderDriver | None:
    if (
        settings.elevenlabs_api_key
        and settings.elevenlabs_lab_agent_id
        and settings.elevenlabs_lab_agent_id != settings.elevenlabs_voice_agent_id
    ):
        return ElevenLabsDriver(settings.elevenlabs_api_key, settings.elevenlabs_lab_agent_id)
    if settings.anthropic_api_key:
        return AnthropicDriver(settings.anthropic_api_key, settings.lab_model)
    return None
