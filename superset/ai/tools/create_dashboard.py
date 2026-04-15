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

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from superset import db
from superset.ai.tools.base import BaseTool
from superset.commands.dashboard.create import CreateDashboardCommand
from superset.commands.dashboard.export import (
    append_charts_v2,
    get_default_position,
)
from superset.connectors.sqla.models import SqlaTable
from superset.daos.dashboard import DashboardDAO
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.utils import json

logger = logging.getLogger(__name__)

# Idempotency window: skip dashboard creation if one with the same title
# was created within this time span.
_IDEMPOTENCY_WINDOW_MINUTES = 10


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
            "chart_widths": {
                "type": "object",
                "description": "Map of chart_id to grid width (1-12). Omit to use default width.",
                "additionalProperties": {"type": "integer"},
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

    def run(self, arguments: dict[str, Any]) -> str:  # noqa: C901
        title = arguments.get("dashboard_title", "").strip()
        chart_ids = arguments.get("chart_ids", [])
        chart_widths = arguments.get("chart_widths") or {}
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
            except Exception as ex:
                return f"Error: Unable to verify access for chart ID {sid}: {ex}"
            slices.append(sl)

        if inaccessible:
            return f"Error: Cannot access charts: {sorted(inaccessible)}"

        found_ids = {s.id for s in slices}

        # Idempotency: skip only after validating every requested chart.
        # This avoids leaking an existing dashboard before access checks.
        sorted_ids = sorted(found_ids)
        existing = self._find_duplicate(title, sorted_ids)
        if existing and self._can_reuse_dashboard(existing):
            dashboard_url = f"/superset/dashboard/{existing.id}/"
            logger.info(
                "Skipping duplicate dashboard creation: '%s' (id=%d)",
                title,
                existing.id,
            )
            return json.dumps(
                {
                    "dashboard_id": existing.id,
                    "dashboard_title": existing.dashboard_title,
                    "dashboard_url": dashboard_url,
                    "chart_count": len(existing.slices),
                    "chart_ids": sorted(s.id for s in existing.slices),
                    "message": (
                        f"Dashboard '{title}' already exists "
                        f"(id={existing.id}). Reusing. View at: "
                        f"{dashboard_url}"
                    ),
                    },
                )

        # Generate a slug if not provided
        if not slug:
            base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            base_slug = base_slug[:45] or "ai-dashboard"
            slug_hash = hashlib.sha256(
                json.dumps(sorted_ids).encode()
            ).hexdigest()[:8]
            slug = f"{base_slug}-{slug_hash}"

        # Build position_json using dynamic layout engine
        position = get_default_position(title)
        charts_with_widths = [
            (sl, chart_widths.get(sl.id, chart_widths.get(str(sl.id), 4)))
            for sl in slices
        ]
        append_charts_v2(position, charts_with_widths)

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
        )

    @staticmethod
    def _find_duplicate(
        title: str, sorted_chart_ids: list[int]
    ) -> Any:
        """Find a dashboard with same title and chart set within window."""

        id_hash = hashlib.sha256(
            json.dumps(sorted_chart_ids).encode()
        ).hexdigest()[:16]
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=_IDEMPOTENCY_WINDOW_MINUTES
        )
        candidates = (
            db.session.query(Dashboard)
            .filter(
                Dashboard.dashboard_title == title,
                Dashboard.changed_on >= cutoff,
            )
            .all()
        )
        for d in candidates:
            existing_ids = sorted(s.id for s in d.slices)
            existing_hash = hashlib.sha256(
                json.dumps(existing_ids).encode()
            ).hexdigest()[:16]
            if existing_hash == id_hash:
                return d
        return None

    @staticmethod
    def _can_reuse_dashboard(dashboard: Any) -> bool:
        """Verify the current user can access the dashboard + its charts."""
        try:
            from superset.extensions import security_manager

            if not security_manager.can_access("can_read", "Dashboard"):
                return False
            for sl in dashboard.slices:
                table = (
                    db.session.query(SqlaTable)
                    .filter_by(id=sl.datasource_id)
                    .first()
                )
                if table and not security_manager.can_access_datasource(table):
                    return False
            return True
        except Exception:
            return False
