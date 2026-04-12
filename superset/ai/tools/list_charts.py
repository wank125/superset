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
"""Tool to list charts available in Superset."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class ListChartsTool(BaseTool):
    """List charts in Superset with optional filters."""

    name = "list_charts"
    description = (
        "List charts in Superset. Optionally filter by name keyword "
        "or chart type (viz_type). Returns chart ID, name, type, "
        "and last modified time."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Filter by chart name keyword",
            },
            "viz_type": {
                "type": "string",
                "description": "Filter by chart visualization type "
                "(e.g. 'table', 'pie', 'echarts_timeseries_line')",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.chart import ChartDAO

        # ChartDAO.find_all() applies ChartFilter (row-level security)
        charts = ChartDAO.find_all()
        search = (arguments.get("search") or "").lower()
        viz_type = arguments.get("viz_type")

        result = []
        for chart in charts:
            if search and search not in (chart.slice_name or "").lower():
                continue
            if viz_type and chart.viz_type != viz_type:
                continue
            result.append(
                {
                    "id": chart.id,
                    "name": chart.slice_name,
                    "viz_type": chart.viz_type,
                    "description": chart.description,
                    "changed_on": str(chart.changed_on) if chart.changed_on else None,
                }
            )

        # Sort by most recently modified
        result.sort(key=lambda x: x["changed_on"] or "", reverse=True)

        return json.dumps(
            {"charts": result[:30], "total": len(result)},
            ensure_ascii=False,
            default=str,
        )
