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
from typing import Any

from superset.ai.agent.base import BaseAgent
from superset.ai.agent.context import ConversationContext
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

    # ── Hook overrides ──────────────────────────────────────────────

    def _on_run_start(self) -> None:
        self._order_guard.reset()

    def _pre_tool_execution(
        self,
        tool_calls: list[dict],
        messages: list[LLMMessage],
        turn_content: str,
    ) -> tuple[list[dict], list[LLMMessage]]:
        """Enforce tool-call ordering via ToolOrderGuard.

        Out-of-order calls are blocked and replaced with correction messages
        so the LLM retries with an allowed tool.
        """
        allowed = self._order_guard.allowed_tools
        filtered: list[dict] = []
        extra_messages: list[LLMMessage] = []

        for tc in tool_calls:
            if tc["name"] in self._tools and not self._order_guard.check(
                tc["name"]
            ):
                logger.warning(
                    "Tool '%s' called out of order (phase=%d), blocking",
                    tc["name"],
                    self._order_guard.phase_idx,
                )
                correction = (
                    f"You must follow the workflow in order. "
                    f"Call one of: {', '.join(sorted(allowed))} next."
                )
                extra_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=turn_content or None,
                        tool_calls=[
                            ToolCall(
                                id=tc["id"],
                                name=tc["name"],
                                arguments=tc["arguments"],
                            )
                        ],
                    )
                )
                extra_messages.append(
                    LLMMessage(
                        role="tool",
                        content=correction,
                        tool_call_id=tc["id"],
                    )
                )
            else:
                filtered.append(tc)

        return filtered, extra_messages

    def _on_tool_executed(self, tool_name: str) -> None:
        self._order_guard.advance(tool_name)
