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
"""Tool to get detailed information about a Superset dashboard."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class GetDashboardDetailTool(BaseTool):
    """Get full details of a Superset dashboard: charts, datasets, layout."""

    name = "get_dashboard_detail"
    description = (
        "Get detailed information about a Superset dashboard by ID or slug. "
        "Returns charts on the dashboard, datasets used, owner info, "
        "and filter configuration."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["dashboard_id"],
        "properties": {
            "dashboard_id": {
                "type": "integer",
                "description": "The dashboard ID",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.dashboard import DashboardDAO
        from superset.extensions import security_manager

        dashboard_id = arguments.get("dashboard_id")
        if dashboard_id is None:
            return json.dumps({"error": "dashboard_id is required"})

        dash = DashboardDAO.find_by_id(dashboard_id)
        if not dash:
            return json.dumps({"error": f"Dashboard {dashboard_id} not found"})

        # Permission check
        try:
            dash.raise_for_access()
        except Exception as exc:
            return json.dumps({"error": f"No access to dashboard: {exc}"})

        # Get charts on this dashboard
        charts_info = []
        for chart in dash.slices:
            charts_info.append(
                {
                    "id": chart.id,
                    "name": chart.slice_name,
                    "viz_type": chart.viz_type,
                    "datasource_id": chart.datasource_id,
                }
            )

        result: dict[str, Any] = {
            "id": dash.id,
            "slug": dash.slug,
            "title": dash.dashboard_title,
            "description": dash.description,
            "published": dash.published,
            "changed_on": str(dash.changed_on) if dash.changed_on else None,
            "chart_count": len(charts_info),
            "charts": charts_info,
            "owners": [
                {"id": o.id, "username": o.username}
                for o in (dash.owners or [])
            ],
        }

        return json.dumps(result, ensure_ascii=False, default=str)
