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
"""Chart creation agent — creates Superset charts from natural language."""

from __future__ import annotations

from superset.ai.agent.base import BaseAgent
from superset.ai.agent.context import ConversationContext
from superset.ai.chart_types.registry import get_chart_registry
from superset.ai.llm.base import BaseLLMProvider
from superset.ai.prompts.chart_creation import CHART_CREATION_SYSTEM_PROMPT
from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.tools.create_chart import CreateChartTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.ai.tools.get_schema import GetSchemaTool
from superset.ai.tools.search_datasets import SearchDatasetsTool


class ChartAgent(BaseAgent):
    """Agent that creates Superset charts from natural language requests.

    Tools: get_schema + execute_sql + analyze_data + search_datasets + create_chart
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        context: ConversationContext,
        database_id: int,
        schema_name: str | None = None,
    ) -> None:
        tools = [
            GetSchemaTool(database_id=database_id, default_schema=schema_name),
            ExecuteSqlTool(database_id=database_id),
            AnalyzeDataTool(database_id=database_id),
            SearchDatasetsTool(
                database_id=database_id, schema_name=schema_name
            ),
            CreateChartTool(),
        ]
        super().__init__(provider, context, tools)
        self._database_id = database_id
        self._schema_name = schema_name

    def get_system_prompt(self) -> str:
        registry = get_chart_registry()
        chart_table = registry.format_for_prompt()

        prompt = CHART_CREATION_SYSTEM_PROMPT.format(
            chart_type_table=chart_table,
        )
        if self._schema_name:
            prompt += f"\n\nThe user is working in schema: {self._schema_name}"
        return prompt
