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
"""OpenAI-compatible LLM provider."""

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


class OpenAIProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs (OpenAI, Azure, local proxies)."""

    provider_name = "openai"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._api_key = os.environ.get(config.get("api_key_env", "OPENAI_API_KEY"), "")
        self._model = config.get("model", "gpt-4o")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 4096)
        self._base_url = config.get("base_url") or "https://api.openai.com/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _to_openai_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            result.append(entry)
        return result

    @staticmethod
    def _parse_tool_calls(data: list[dict[str, Any]]) -> list[ToolCall]:
        calls = []
        for tc in data:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )
        return calls

    def _build_body(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(messages),
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        return body

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body = self._build_body(messages, tools, stream=False)

        def _call() -> dict[str, Any]:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=body,
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(_call, exception=httpx.HTTPError)
        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = self._parse_tool_calls(message["tool_calls"])

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
        )

    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        body = self._build_body(messages, tools, stream=True)

        with httpx.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=120.0,
        ) as resp:
            resp.raise_for_status()
            # Accumulate tool call argument fragments across deltas
            tc_accum: dict[int, dict[str, Any]] = {}
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload.strip() == "[DONE]":
                    # Emit final accumulated tool calls
                    if tc_accum:
                        yield LLMStreamChunk(
                            tool_calls=self._parse_accumulated_tool_calls(tc_accum)
                        )
                    yield LLMStreamChunk(finish_reason="stop")
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                finish_reason = choices[0].get("finish_reason")

                # Accumulate tool call fragments
                if delta.get("tool_calls"):
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        if idx not in tc_accum:
                            tc_accum[idx] = {
                                "id": tc_delta.get("id", ""),
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc_delta.get("id"):
                            tc_accum[idx]["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            tc_accum[idx]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            tc_accum[idx]["function"]["arguments"] += fn["arguments"]

                # Emit accumulated tool calls when finish_reason signals completion.
                # Local/GLM models may return finish_reason="tool_calls", "stop", or
                # even None/"" — flush tc_accum whenever finish_reason is non-null
                # (including "tool_calls") to handle all variants.
                tool_calls = None
                if finish_reason and tc_accum:
                    tool_calls = self._parse_accumulated_tool_calls(tc_accum)
                    tc_accum = {}

                yield LLMStreamChunk(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                )

    @staticmethod
    def _parse_accumulated_tool_calls(
        tc_accum: dict[int, dict[str, Any]],
    ) -> list[ToolCall]:
        """Parse accumulated tool call fragments into ToolCall objects."""
        calls = []
        for idx in sorted(tc_accum.keys()):
            tc = tc_accum[idx]
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )
        return calls
