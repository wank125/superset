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
"""Base agent with ReAct reasoning loop (legacy).

.. deprecated::
    The ReAct agents (NL2SQLAgent, ChartAgent, etc.) are retained for
    the ``LegacyAgentRunner`` path.  Chart/dashboard creation now uses
    the StateGraph pipeline (``superset.ai.graph``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from superset.ai.agent.confirmation import (
    confirmation_required_message,
    is_creation_confirmed,
    is_side_effect_tool,
)
from superset.ai.agent.context import ConversationContext
from superset.ai.agent.events import AgentEvent
from superset.ai.config import get_max_turns
from superset.ai.errors import format_user_facing_error
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

    Subclasses can customise the loop via hook methods:
    - ``_on_run_start()`` — called once before the loop
    - ``_pre_tool_execution()`` — filter/augment tool calls before execution
    - ``_on_tool_executed()`` — called after each successful tool call
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

    # ── Hook methods (override in subclasses) ────────────────────────

    def _on_run_start(self) -> None:
        """Called once at the start of ``run()``. Override to initialise state."""

    def _pre_tool_execution(
        self,
        tool_calls: list[dict[str, Any]],
        messages: list[LLMMessage],
        turn_content: str,
    ) -> tuple[list[dict[str, Any]], list[LLMMessage]]:
        """Filter tool calls before execution.

        Returns ``(filtered_calls, extra_messages)`` where *extra_messages*
        are appended to *messages* alongside the filtered assistant message.
        """
        return tool_calls, []

    def _on_tool_executed(self, tool_name: str) -> None:
        """Called after a tool call is executed successfully."""

    # ── Main ReAct loop ──────────────────────────────────────────────

    def run(self, user_message: str) -> Iterator[AgentEvent]:
        """Execute the ReAct loop and yield events."""
        self._on_run_start()

        messages = [
            LLMMessage(role="system", content=self.get_system_prompt())
        ]
        # Append conversation history, converting tool_summary to system messages
        for entry in self._context.get_history():
            role = entry.get("role", "")
            if role == "tool_summary":
                messages.append(
                    LLMMessage(
                        role="system",
                        content=(
                            f"[Previous tool result — {entry.get('tool', 'unknown')}]\n"
                            f"{entry['content']}"
                        ),
                    )
                )
            elif role in ("user", "assistant"):
                messages.append(
                    LLMMessage(role=role, content=entry["content"])
                )
            # router_meta and other metadata entries are silently skipped
        messages.append(LLMMessage(role="user", content=user_message))

        # Save user message to context
        self._context.add_message("user", user_message)

        assistant_content_parts: list[str] = []
        tool_summaries: list[tuple[str, str]] = []  # (tool_name, content)
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
                    data={"message": format_user_facing_error(exc)},
                )
                return

            if not tool_calls_acc:
                # LLM returned a final answer
                break

            blocked_calls = [
                tc
                for tc in tool_calls_acc
                if is_side_effect_tool(tc["name"])
                and not is_creation_confirmed(user_message)
            ]
            if blocked_calls:
                blocked_tool = blocked_calls[0]["name"]
                confirmation_message = confirmation_required_message(
                    blocked_tool
                )
                assistant_content_parts.append(confirmation_message)
                yield AgentEvent(
                    type="text_chunk",
                    data={"content": confirmation_message},
                )
                full_response = "".join(assistant_content_parts)
                self._context.add_message("assistant", full_response)
                yield AgentEvent(type="done", data={})
                return

            # Hook: let subclasses filter / intercept tool calls
            turn_text = "".join(turn_content_parts)
            filtered_calls, extra_messages = self._pre_tool_execution(
                tool_calls_acc, messages, turn_text,
            )
            tool_calls_acc = filtered_calls

            if not tool_calls_acc:
                # All calls were filtered out — continue to next turn
                messages.extend(extra_messages)
                continue

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
                    content=turn_text or None,
                    tool_calls=assistant_tool_calls,
                )
            )

            # Execute each tool call
            for tc in tool_calls_acc:
                self._on_tool_executed(tc["name"])

                yield AgentEvent(
                    type="tool_call",
                    data={"tool": tc["name"], "args": tc["arguments"]},
                )
                try:
                    result = self._tools[tc["name"]].run(tc["arguments"])
                except Exception as exc:
                    result = f"Tool error: {exc}"

                # Record key tool results for multi-turn context
                if tc["name"] == "execute_sql":
                    sql = (
                        tc["arguments"].get("sql", "")
                        if isinstance(tc["arguments"], dict)
                        else str(tc["arguments"])
                    )
                    preview = str(result)[:300]
                    tool_summaries.append((
                        "execute_sql",
                        f"SQL: {sql}\nResult preview: {preview}",
                    ))

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

            # Append blocked-tool messages after all tool results so the
            # assistant→tool→assistant→tool sequence required by chat APIs
            # is not violated.
            messages.extend(extra_messages)

        # Save assistant response to context
        full_response = "".join(assistant_content_parts)
        self._context.add_message("assistant", full_response)

        # Persist tool summaries for next-turn context
        for tool_name, content in tool_summaries:
            self._context.add_tool_summary(tool_name, content)

        yield AgentEvent(type="done", data={})
