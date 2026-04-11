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
"""Base agent with ReAct reasoning loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from superset.ai.agent.context import ConversationContext
from superset.ai.agent.events import AgentEvent
from superset.ai.config import get_max_turns
from superset.ai.llm.base import BaseLLMProvider
from superset.ai.llm.types import LLMMessage, ToolCall
from superset.ai.tools.base import BaseTool


class BaseAgent(ABC):
    """Abstract base agent implementing a ReAct reasoning loop.

    The loop proceeds as:
    1. Build messages (system prompt + history + user message)
    2. Call LLM (streaming)
    3. If the LLM requests tool calls, execute them and feed results back
    4. Repeat until the LLM returns a final answer or max turns reached
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        context: ConversationContext,
        tools: list[BaseTool],
    ) -> None:
        self._provider = provider
        self._context = context
        self._tools = {t.name: t for t in tools}
        self._max_turns = get_max_turns()

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""

    def _get_tool_defs(self) -> list[dict[str, Any]]:
        """Build OpenAI-style tool definitions for all registered tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in self._tools.values()
        ]

    # Safeguard: max characters of text_chunk content before forcing stop
    _MAX_STREAM_CHARS = 10000
    # Safeguard: if a short text segment repeats this many times, stop
    _MAX_REPETITIONS = 8

    def _detect_repetition(self, accumulated: str) -> bool:
        """Check whether accumulated text is stuck in a loop."""
        if len(accumulated) < 200:
            return False
        # Check a short tail segment against the whole text
        tail = accumulated[-30:]
        return accumulated.count(tail) >= self._MAX_REPETITIONS

    def run(self, user_message: str) -> Iterator[AgentEvent]:
        """Execute the ReAct loop and yield events."""
        messages = [
            LLMMessage(role="system", content=self.get_system_prompt())
        ]
        # Append conversation history
        for entry in self._context.get_history():
            messages.append(
                LLMMessage(role=entry["role"], content=entry["content"])
            )
        messages.append(LLMMessage(role="user", content=user_message))

        # Save user message to context
        self._context.add_message("user", user_message)

        assistant_content_parts: list[str] = []
        tool_defs = self._get_tool_defs() if self._tools else None

        for _turn in range(self._max_turns):
            tool_calls_acc: list[dict[str, Any]] = []
            # Reset per-turn accumulators so repetition detection and char
            # limits apply to a single LLM response, not the whole conversation.
            turn_content_parts: list[str] = []
            stream_chars = 0

            try:
                for chunk in self._provider.chat_stream(messages, tools=tool_defs):
                    if chunk.content:
                        turn_content_parts.append(chunk.content)
                        assistant_content_parts.append(chunk.content)
                        stream_chars += len(chunk.content)
                        yield AgentEvent(
                            type="text_chunk",
                            data={"content": chunk.content},
                        )
                        # Guard against infinite text generation (per-turn)
                        if stream_chars > self._MAX_STREAM_CHARS:
                            yield AgentEvent(
                                type="error",
                                data={"message": "Response too long, stopped early."},
                            )
                            full_response = "".join(assistant_content_parts)
                            self._context.add_message(
                                "assistant", full_response[:self._MAX_STREAM_CHARS]
                            )
                            yield AgentEvent(type="done", data={})
                            return
                        if self._detect_repetition(
                            "".join(turn_content_parts)
                        ):
                            yield AgentEvent(
                                type="error",
                                data={"message": "Detected repetitive output, stopped."},
                            )
                            full_response = "".join(assistant_content_parts)
                            self._context.add_message("assistant", full_response)
                            yield AgentEvent(type="done", data={})
                            return
                    if chunk.tool_calls:
                        tool_calls_acc.extend(
                            [
                                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                for tc in chunk.tool_calls
                            ]
                        )
                    if chunk.finish_reason in ("stop", "end_turn", "tool_calls"):
                        break
            except Exception as exc:
                yield AgentEvent(
                    type="error",
                    data={"message": f"LLM call failed: {exc}"},
                )
                return

            if not tool_calls_acc:
                # LLM returned a final answer
                break

            # Append the assistant message with tool_calls before tool results
            # (required by OpenAI/Anthropic chat APIs)
            assistant_tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in tool_calls_acc
            ]
            messages.append(
                LLMMessage(
                    role="assistant",
                    content="".join(turn_content_parts) or None,
                    tool_calls=assistant_tool_calls,
                )
            )

            # Execute each tool call
            for tc in tool_calls_acc:
                yield AgentEvent(
                    type="tool_call",
                    data={"tool": tc["name"], "args": tc["arguments"]},
                )
                try:
                    result = self._tools[tc["name"]].run(tc["arguments"])
                except Exception as exc:
                    result = f"Tool error: {exc}"

                messages.append(
                    LLMMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc["id"],
                    )
                )
                yield AgentEvent(
                    type="tool_result",
                    data={"tool": tc["name"], "result": result},
                )

        # Save assistant response to context
        full_response = "".join(assistant_content_parts)
        self._context.add_message("assistant", full_response)

        yield AgentEvent(type="done", data={})
