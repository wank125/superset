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
"""Dashboard agent — creates Superset dashboards from natural language."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from superset.ai.agent.base import BaseAgent
from superset.ai.agent.context import ConversationContext
from superset.ai.agent.events import AgentEvent
from superset.ai.agent.langchain.guard import ToolOrderGuard
from superset.ai.chart_types.registry import get_chart_registry
from superset.ai.llm.base import BaseLLMProvider
from superset.ai.llm.types import LLMMessage, ToolCall
from superset.ai.prompts.dashboard_creation import DASHBOARD_CREATION_SYSTEM_PROMPT
from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.tools.base import BaseTool
from superset.ai.tools.create_chart import CreateChartTool
from superset.ai.tools.create_dashboard import CreateDashboardTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.ai.tools.get_schema import GetSchemaTool
from superset.ai.tools.search_datasets import SearchDatasetsTool

logger = logging.getLogger(__name__)


class DashboardAgent(BaseAgent):
    """Agent that creates Superset dashboards from natural language requests.

    Tools: get_schema + execute_sql + analyze_data + search_datasets +
    create_chart + create_dashboard

    Enforces a sequential tool-calling order via ToolOrderGuard:
    search_datasets → analyze_data → create_chart → create_dashboard.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        context: ConversationContext,
        database_id: int,
        schema_name: str | None = None,
    ) -> None:
        tools: list[BaseTool] = [
            GetSchemaTool(database_id=database_id, default_schema=schema_name),
            ExecuteSqlTool(database_id=database_id),
            AnalyzeDataTool(database_id=database_id),
            SearchDatasetsTool(
                database_id=database_id, schema_name=schema_name
            ),
            CreateChartTool(),
            CreateDashboardTool(),
        ]
        super().__init__(provider, context, tools)
        self._database_id = database_id
        self._schema_name = schema_name
        self._order_guard = ToolOrderGuard(
            phases=[
                "search_datasets",
                "analyze_data",
                "create_chart",
                "create_dashboard",
            ],
        )

    def get_system_prompt(self) -> str:
        registry = get_chart_registry()
        chart_table = registry.format_for_prompt()

        prompt = DASHBOARD_CREATION_SYSTEM_PROMPT.format(
            chart_type_table=chart_table,
        )
        if self._schema_name:
            prompt += f"\n\nThe user is working in schema: {self._schema_name}"
        return prompt

    def run(self, user_message: str) -> Iterator[AgentEvent]:  # noqa: C901
        """Execute the ReAct loop with sequential tool-order enforcement."""
        messages = [
            LLMMessage(role="system", content=self.get_system_prompt())
        ]
        for entry in self._context.get_history():
            messages.append(
                LLMMessage(role=entry["role"], content=entry["content"])
            )
        messages.append(LLMMessage(role="user", content=user_message))

        self._context.add_message("user", user_message)
        self._order_guard.reset()

        assistant_content_parts: list[str] = []
        tool_defs = self._get_tool_defs() if self._tools else None

        for _turn in range(self._max_turns):
            tool_calls_acc: list[dict] = []
            turn_content_parts: list[str] = []
            stream_chars = 0

            try:
                for chunk in self._provider.chat_stream(
                    messages, tools=tool_defs
                ):
                    if chunk.content:
                        turn_content_parts.append(chunk.content)
                        assistant_content_parts.append(chunk.content)
                        stream_chars += len(chunk.content)
                        yield AgentEvent(
                            type="text_chunk",
                            data={"content": chunk.content},
                        )
                        if stream_chars > self._MAX_STREAM_CHARS:
                            yield AgentEvent(
                                type="error",
                                data={
                                    "message": (
                                        "Response too long, stopped early."
                                    )
                                },
                            )
                            self._context.add_message(
                                "assistant",
                                "".join(assistant_content_parts)[
                                    :self._MAX_STREAM_CHARS
                                ],
                            )
                            yield AgentEvent(type="done", data={})
                            return
                        if self._detect_repetition(
                            "".join(turn_content_parts)
                        ):
                            yield AgentEvent(
                                type="error",
                                data={
                                    "message": (
                                        "Detected repetitive output, stopped."
                                    )
                                },
                            )
                            self._context.add_message(
                                "assistant",
                                "".join(assistant_content_parts),
                            )
                            yield AgentEvent(type="done", data={})
                            return
                    if chunk.tool_calls:
                        tool_calls_acc.extend(
                            [
                                {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                }
                                for tc in chunk.tool_calls
                            ]
                        )
                    if chunk.finish_reason in (
                        "stop",
                        "end_turn",
                        "tool_calls",
                    ):
                        break
            except Exception as exc:
                yield AgentEvent(
                    type="error",
                    data={"message": f"LLM call failed: {exc}"},
                )
                return

            if not tool_calls_acc:
                break

            # Enforce tool call order via guard
            allowed = self._order_guard.allowed_tools
            filtered_calls: list[dict] = []
            for tc in tool_calls_acc:
                if tc["name"] in self._tools and not self._order_guard.check(
                    tc["name"]
                ):
                    logger.warning(
                        "Tool '%s' called out of order (phase=%d), blocking",
                        tc["name"],
                        self._order_guard.phase_idx,
                    )
                    messages.append(
                        LLMMessage(
                            role="assistant",
                            content="".join(turn_content_parts) or None,
                            tool_calls=[
                                ToolCall(
                                    id=tc["id"],
                                    name=tc["name"],
                                    arguments=tc["arguments"],
                                )
                            ],
                        )
                    )
                    correction = (
                        f"You must follow the workflow in order. "
                        f"Call one of: {', '.join(sorted(allowed))} next."
                    )
                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=correction,
                            tool_call_id=tc["id"],
                        )
                    )
                    yield AgentEvent(
                        type="error",
                        data={
                            "message": (
                                f"Tool '{tc['name']}' called before required "
                                f"steps completed. Please follow the workflow."
                            )
                        },
                    )
                else:
                    filtered_calls.append(tc)

            if not filtered_calls:
                continue

            # Append assistant message with filtered tool calls
            assistant_tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in filtered_calls
            ]
            messages.append(
                LLMMessage(
                    role="assistant",
                    content="".join(turn_content_parts) or None,
                    tool_calls=assistant_tool_calls,
                )
            )

            # Execute each tool call
            for tc in filtered_calls:
                self._order_guard.advance(tc["name"])

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

        full_response = "".join(assistant_content_parts)
        self._context.add_message("assistant", full_response)
        yield AgentEvent(type="done", data={})
