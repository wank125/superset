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
"""Tool to generate embedded dashboard links in Superset."""

from __future__ import annotations

import logging
from typing import Any

from superset import db, is_feature_enabled
from superset.ai.tools.base import BaseTool
from superset.daos.dashboard import EmbeddedDashboardDAO
from superset.models.dashboard import Dashboard
from superset.utils import json

logger = logging.getLogger(__name__)


class EmbedDashboardTool(BaseTool):
    """Generate an embeddable link for a Superset dashboard."""

    name = "embed_dashboard"
    description = (
        "Generate an embedded dashboard link that can be used to embed "
        "the dashboard in an external website or iframe. "
        "Requires dashboard_id. Optionally specify allowed_domains."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["dashboard_id"],
        "properties": {
            "dashboard_id": {
                "type": "integer",
                "description": "The dashboard ID to generate an embedded link for",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of allowed domains for embedding. "
                    "Empty list means any domain can embed."
                ),
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        dashboard_id = arguments.get("dashboard_id")
        allowed_domains = arguments.get("allowed_domains", [])

        if not dashboard_id:
            return "Error: dashboard_id is required"

        # Check feature flag
        if not is_feature_enabled("EMBEDDED_SUPERSET"):
            return (
                "Error: Embedded dashboards feature is not enabled. "
                "Set EMBEDDED_SUPERSET=True in config to enable."
            )

        # Verify permission — must match the native set_embedded API gate
        try:
            from superset.extensions import security_manager

            if not security_manager.can_access(
                "can_set_embedded", "DashboardRestApi"
            ):
                return "Error: You do not have permission to set embedded dashboards."
        except Exception:
            return "Error: Unable to verify dashboard permissions."

        # Find dashboard
        dashboard = db.session.query(Dashboard).get(dashboard_id)
        if not dashboard:
            return f"Error: Dashboard ID {dashboard_id} not found"

        # Upsert embedded config
        try:
            embedded = EmbeddedDashboardDAO.upsert(
                dashboard=dashboard,
                allowed_domains=allowed_domains,
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return f"Error creating embedded config: {exc}"

        embed_url = f"/embedded/{embedded.uuid}"
        return json.dumps(
            {
                "dashboard_id": dashboard.id,
                "dashboard_title": dashboard.dashboard_title,
                "embedded_uuid": str(embedded.uuid),
                "embed_url": embed_url,
                "allowed_domains": allowed_domains,
                "message": (
                    f"Embedded link generated for '{dashboard.dashboard_title}'. "
                    f"Embed URL: {embed_url}"
                ),
            },
        )
