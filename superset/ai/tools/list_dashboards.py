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
"""Tool to list dashboards available in Superset."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class ListDashboardsTool(BaseTool):
    """List dashboards in Superset with optional filters."""

    name = "list_dashboards"
    description = (
        "List dashboards in Superset. Optionally filter by name keyword. "
        "Returns dashboard ID, title, slug, published status, "
        "and last modified time."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Filter by dashboard title keyword",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.dashboard import DashboardDAO

        # DashboardDAO.find_all() applies DashboardAccessFilter (RBAC)
        dashboards = DashboardDAO.find_all()
        search = (arguments.get("search") or "").lower()

        result = []
        for dash in dashboards:
            if search and search not in (dash.dashboard_title or "").lower():
                continue
            result.append(
                {
                    "id": dash.id,
                    "slug": dash.slug,
                    "title": dash.dashboard_title,
                    "published": dash.published,
                    "changed_on": (
                        str(dash.changed_on) if dash.changed_on else None
                    ),
                }
            )

        # Sort by most recently modified
        result.sort(key=lambda x: x["changed_on"] or "", reverse=True)

        return json.dumps(
            {"dashboards": result[:30], "total": len(result)},
            ensure_ascii=False,
            default=str,
        )
