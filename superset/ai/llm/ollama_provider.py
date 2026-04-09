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
"""Ollama local LLM provider."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from superset.ai.llm.base import BaseLLMProvider
from superset.ai.llm.types import LLMMessage, LLMResponse, LLMStreamChunk, ToolCall
from superset.utils.retries import retry_call

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Provider for Ollama local model API."""

    provider_name = "ollama"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._base_url = config.get("base_url", "http://localhost:11434")
        self._model = config.get("model", "llama3")
        self._temperature = config.get("temperature", 0.0)

    @staticmethod
    def _format_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            result.append(entry)
        return result

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            fn = tool.get("function", tool)
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return result

    @staticmethod
    def _parse_tool_calls(data: list[dict[str, Any]]) -> list[ToolCall]:
        calls = []
        for tc in data:
            fn = tc.get("function", {})
            calls.append(
                ToolCall(
                    id=fn.get("name", ""),
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", {}),
                )
            )
        return calls

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages(messages),
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        if tools:
            body["tools"] = self._convert_tools(tools)

        def _call() -> dict[str, Any]:
            resp = httpx.post(
                f"{self._base_url}/api/chat",
                json=body,
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(_call, exception=httpx.HTTPError)
        message = data.get("message", {})
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = self._parse_tool_calls(message["tool_calls"])

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason="stop" if data.get("done") else "unknown",
        )

    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages(messages),
            "stream": True,
            "options": {"temperature": self._temperature},
        }
        if tools:
            body["tools"] = self._convert_tools(tools)

        with httpx.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json=body,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = chunk.get("message", {})
                tool_calls = None
                if message.get("tool_calls"):
                    tool_calls = self._parse_tool_calls(message["tool_calls"])
                yield LLMStreamChunk(
                    content=message.get("content"),
                    tool_calls=tool_calls,
                    finish_reason="stop" if chunk.get("done") else None,
                )
