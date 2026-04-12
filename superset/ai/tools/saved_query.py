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
"""Tool to search saved SQL queries."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class SavedQueryTool(BaseTool):
    """Search and retrieve saved SQL queries."""

    name = "saved_query"
    description = (
        "Search saved SQL queries. Optionally filter by name keyword. "
        "Returns the SQL text, database, and metadata for each saved query."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Filter by saved query label keyword",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 20, max: 50)",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from flask import g

        from superset import db
        from superset.models.sql_lab import SavedQuery

        user = getattr(g, "user", None)
        if not user:
            return json.dumps({"error": "Not authenticated"})

        limit = min(arguments.get("limit", 20), 50)
        search = (arguments.get("search") or "").lower()

        q = db.session.query(SavedQuery).filter(
            SavedQuery.user_id == user.id,
        )

        if search:
            q = q.filter(SavedQuery.label.ilike(f"%{search}%"))

        saved_queries = q.order_by(SavedQuery.changed_on.desc()).limit(limit).all()

        result_list = [
            {
                "id": sq.id,
                "label": sq.label,
                "description": sq.description,
                "schema": sq.schema,
                "database": sq.database.database_name if sq.database else None,
                "sql_preview": (sq.sql or "")[:300],
                "rows": sq.rows,
                "last_run": str(sq.last_run) if sq.last_run else None,
                "changed_on": str(sq.changed_on) if sq.changed_on else None,
            }
            for sq in saved_queries
        ]

        return json.dumps(
            {"saved_queries": result_list, "total": len(result_list)},
            ensure_ascii=False,
            default=str,
        )
