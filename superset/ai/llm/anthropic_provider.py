# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Anthropic Claude LLM provider."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

import httpx

from superset.ai.llm.base import BaseLLMProvider
from superset.ai.llm.types import LLMMessage, LLMResponse, LLMStreamChunk, ToolCall
from superset.utils.retries import retry_call

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic Claude API."""

    provider_name = "anthropic"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._api_key = os.environ.get(
            config.get("api_key_env", "ANTHROPIC_API_KEY"), ""
        )
        self._model = config.get("model", "claude-sonnet-4-20250514")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 4096)
        self._base_url = config.get("base_url", "https://api.anthropic.com")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    @staticmethod
    def _split_messages(
        messages: list[LLMMessage],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Split into system prompt + conversation messages for Anthropic format."""
        system_prompt = None
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
                continue
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                entry["content"] = [
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                entry = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            api_messages.append(entry)
        return system_prompt, api_messages

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tool defs to Anthropic format."""
        result = []
        for tool in tools:
            fn = tool.get("function", tool)
            result.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return result

    @staticmethod
    def _parse_tool_calls(content_blocks: list[dict[str, Any]]) -> list[ToolCall]:
        calls = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )
        return calls

    def _build_body(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        system_prompt, api_messages = self._split_messages(messages)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": self._max_tokens,
            "stream": stream,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = self._convert_tools(tools)
        return body

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body = self._build_body(messages, tools, stream=False)

        def _call() -> dict[str, Any]:
            resp = httpx.post(
                f"{self._base_url}/v1/messages",
                headers=self._headers(),
                json=body,
                timeout=45.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(_call, exception=httpx.HTTPError, max_tries=2, interval=2)
        content_blocks = data.get("content", [])
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
        tool_calls = self._parse_tool_calls(content_blocks)
        stop_reason = data.get("stop_reason", "end_turn")

        return LLMResponse(
            content="\n".join(text_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=stop_reason,
        )

    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        body = self._build_body(messages, tools, stream=True)

        with httpx.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json=body,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            # Accumulate tool input across deltas
            tc_accum: dict[int, dict[str, Any]] = {}
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_start":
                    block = event.get("content_block", {})
                    idx = event.get("index", 0)
                    if block.get("type") == "tool_use":
                        tc_accum[idx] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input_json": "",
                        }

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    idx = event.get("index", 0)
                    if delta.get("type") == "text_delta":
                        yield LLMStreamChunk(content=delta.get("text"))
                    elif delta.get("type") == "input_json_delta" and idx in tc_accum:
                        tc_accum[idx]["input_json"] += delta.get(
                            "partial_json", ""
                        )

                elif event_type == "content_block_stop":
                    idx = event.get("index", 0)
                    if idx in tc_accum:
                        tc = tc_accum.pop(idx)
                        try:
                            args = json.loads(tc["input_json"]) if tc["input_json"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield LLMStreamChunk(
                            tool_calls=[
                                ToolCall(
                                    id=tc["id"],
                                    name=tc["name"],
                                    arguments=args,
                                )
                            ]
                        )

                elif event_type == "message_stop":
                    yield LLMStreamChunk(finish_reason="end_turn")
                    return
