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
"""Tool to get detailed information about a Superset chart."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class GetChartDetailTool(BaseTool):
    """Get full details of a Superset chart: configuration, datasource, and params."""

    name = "get_chart_detail"
    description = (
        "Get detailed configuration of a Superset chart by ID. "
        "Returns viz_type, datasource, query parameters, and description. "
        "Useful for understanding how a chart is configured."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["chart_id"],
        "properties": {
            "chart_id": {
                "type": "integer",
                "description": "The chart (Slice) ID",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.chart import ChartDAO
        from superset.extensions import security_manager

        chart_id = arguments.get("chart_id")
        if chart_id is None:
            return json.dumps({"error": "chart_id is required"})

        chart = ChartDAO.find_by_id(chart_id)
        if not chart:
            return json.dumps({"error": f"Chart {chart_id} not found"})

        # Permission check
        try:
            if not security_manager.can_access_chart(chart):
                return json.dumps({"error": f"No access to chart '{chart.slice_name}'"})
        except Exception:
            return json.dumps(
                {"error": f"Unable to verify access to chart '{chart.slice_name}'"}
            )

        result: dict[str, Any] = {
            "id": chart.id,
            "name": chart.slice_name,
            "viz_type": chart.viz_type,
            "description": chart.description,
            "datasource_id": chart.datasource_id,
            "datasource_type": chart.datasource_type,
            "cache_timeout": chart.cache_timeout,
            "changed_on": str(chart.changed_on) if chart.changed_on else None,
            "changed_by_fk": chart.changed_by_fk,
            "params": chart.params,
        }

        # Parse params JSON if present
        if chart.params:
            try:
                result["parsed_params"] = json.loads(chart.params)
            except (json.JSONDecodeError, TypeError):
                pass

        return json.dumps(result, ensure_ascii=False, default=str)
