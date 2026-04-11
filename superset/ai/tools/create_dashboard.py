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
"""Tool to create dashboards in Superset via CreateDashboardCommand."""

from __future__ import annotations

import json
import re
from typing import Any

from superset import db
from superset.ai.tools.base import BaseTool
from superset.commands.dashboard.create import CreateDashboardCommand
from superset.commands.dashboard.export import (
    append_charts,
    get_default_position,
)
from superset.daos.dashboard import DashboardDAO
from superset.models.slice import Slice


class CreateDashboardTool(BaseTool):
    """Create a Superset dashboard containing previously created charts."""

    name = "create_dashboard"
    description = (
        "Create a dashboard in Superset that contains the specified charts. "
        "Requires dashboard_title and chart_ids (list of chart IDs from "
        "create_chart). Use create_chart first to create individual charts, "
        "then call this to assemble them into a dashboard."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["dashboard_title", "chart_ids"],
        "properties": {
            "dashboard_title": {
                "type": "string",
                "description": "Dashboard title (1-500 characters)",
            },
            "chart_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "List of chart IDs to include in the dashboard",
            },
            "slug": {
                "type": "string",
                "description": "Optional URL slug (auto-generated if omitted)",
            },
            "description": {
                "type": "string",
                "description": "Optional dashboard description",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        title = arguments.get("dashboard_title", "").strip()
        chart_ids = arguments.get("chart_ids", [])
        slug = arguments.get("slug", "")
        description = arguments.get("description", "")

        if not title:
            return "Error: dashboard_title is required"
        if not chart_ids or not isinstance(chart_ids, list):
            return "Error: chart_ids must be a non-empty list of chart IDs"

        # Verify Dashboard write permission
        try:
            from superset.extensions import security_manager

            if not security_manager.can_access("can_write", "Dashboard"):
                return "Error: You do not have permission to create dashboards."
        except Exception:
            return "Error: Unable to verify dashboard creation permissions."

        # Look up all chart slices and verify access
        from superset.extensions import security_manager

        slices: list[Slice] = []
        inaccessible: list[int] = []
        for sid in chart_ids:
            sl = db.session.query(Slice).get(sid)
            if not sl:
                return f"Error: Chart ID {sid} not found"
            # Verify the user can read each chart's datasource
            try:
                from superset.connectors.sqla.models import SqlaTable

                table = (
                    db.session.query(SqlaTable)
                    .filter_by(id=sl.datasource_id)
                    .first()
                )
                if table and not security_manager.can_access_datasource(table):
                    inaccessible.append(sid)
            except Exception:
                pass  # If check fails, allow (best-effort)
            slices.append(sl)

        if inaccessible:
            return f"Error: Cannot access charts: {sorted(inaccessible)}"

        found_ids = {s.id for s in slices}

        # Generate a slug if not provided
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]

        # Build position_json using existing helpers
        position = get_default_position(title)
        append_charts(position, set(slices))

        # Create the dashboard
        dashboard_data: dict[str, Any] = {
            "dashboard_title": title[:500],
            "slug": slug,
            "position_json": json.dumps(position, sort_keys=True),
            "published": False,
        }
        if description:
            dashboard_data["description"] = description

        try:
            command = CreateDashboardCommand(dashboard_data)
            dashboard = command.run()
        except Exception as exc:
            return f"Error creating dashboard: {exc}"

        # Link charts to dashboard using DashboardDAO which extracts
        # chartIds from position_json and properly establishes the
        # many-to-many relationship.
        DashboardDAO.set_dash_metadata(dashboard, {"positions": position})
        db.session.commit()

        dashboard_url = f"/superset/dashboard/{dashboard.id}/"
        return json.dumps(
            {
                "dashboard_id": dashboard.id,
                "dashboard_title": dashboard.dashboard_title,
                "dashboard_url": dashboard_url,
                "chart_count": len(slices),
                "chart_ids": sorted(found_ids),
                "message": (
                    f"Dashboard '{title}' created with {len(slices)} charts! "
                    f"View at: {dashboard_url}"
                ),
            },
            ensure_ascii=False,
        )
