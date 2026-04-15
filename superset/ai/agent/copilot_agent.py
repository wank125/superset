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
"""CopilotAgent — Superset Copilot that answers questions about assets."""

from __future__ import annotations

from typing import Any

from superset.ai.agent.base import BaseAgent
from superset.ai.prompts.copilot import COPILOT_SYSTEM_PROMPT
from superset.ai.tools.get_chart_detail import GetChartDetailTool
from superset.ai.tools.get_dashboard_detail import GetDashboardDetailTool
from superset.ai.tools.get_dataset_detail import GetDatasetDetailTool
from superset.ai.tools.list_charts import ListChartsTool
from superset.ai.tools.list_dashboards import ListDashboardsTool
from superset.ai.tools.list_databases import ListDatabasesTool
from superset.ai.tools.query_history import QueryHistoryTool
from superset.ai.tools.report_status import ReportStatusTool
from superset.ai.tools.saved_query import SavedQueryTool
from superset.ai.tools.whoami import WhoAmITool
from superset.ai.tools.embed_dashboard import EmbedDashboardTool


class CopilotAgent(BaseAgent):
    """Superset Copilot — answers questions about the entire Superset instance.

    When ``database_id`` is provided, SQL exploration tools (get_schema,
    execute_sql, search_datasets) are also registered.  When it is ``None``,
    only the asset-query tools are available.
    """

    def __init__(
        self,
        provider: Any,
        context: Any,
        database_id: int | None = None,
        schema_name: str | None = None,
    ) -> None:
        tools: list[Any] = [
            ListDatabasesTool(),
            GetDatasetDetailTool(),
            ListChartsTool(),
            ListDashboardsTool(),
            WhoAmITool(),
            GetChartDetailTool(),
            GetDashboardDetailTool(),
            QueryHistoryTool(),
            SavedQueryTool(),
            ReportStatusTool(),
            EmbedDashboardTool(),
        ]

        if database_id is not None:
            # Lazy imports to avoid circular dependency at module load
            from superset.ai.tools.execute_sql import ExecuteSqlTool
            from superset.ai.tools.get_schema import GetSchemaTool
            from superset.ai.tools.search_datasets import SearchDatasetsTool

            tools.extend(
                [
                    GetSchemaTool(
                        database_id=database_id, default_schema=schema_name
                    ),
                    ExecuteSqlTool(database_id=database_id),
                    SearchDatasetsTool(
                        database_id=database_id, schema_name=schema_name
                    ),
                ]
            )

        super().__init__(provider, context, tools)

    def get_system_prompt(self) -> str:
        return COPILOT_SYSTEM_PROMPT
