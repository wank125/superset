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
"""Tool to search SQL query execution history."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class QueryHistoryTool(BaseTool):
    """Search SQL query execution history with filters."""

    name = "query_history"
    description = (
        "Search SQL query execution history. Filter by status "
        "(success/failed/running/timed_out), time range, or minimum elapsed "
        "seconds. Useful for finding slow queries, errors, or recent activity."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "failed", "running", "timed_out"],
                "description": "Filter by query status",
            },
            "min_elapsed_seconds": {
                "type": "number",
                "description": "Minimum execution time in seconds",
            },
            "days_ago": {
                "type": "integer",
                "description": "Look back N days (default: 7)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 20, max: 50)",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from flask import g
        from datetime import datetime, timedelta, timezone

        from superset import db
        from superset.models.sql_lab import Query

        user = getattr(g, "user", None)
        if not user:
            return json.dumps({"error": "Not authenticated"})

        days_ago = arguments.get("days_ago", 7)
        limit = min(arguments.get("limit", 20), 50)

        since = datetime.now(timezone.utc) - timedelta(days=days_ago)
        since_epoch = since.timestamp()

        q = db.session.query(Query).filter(
            Query.user_id == user.id,
            Query.start_time >= since_epoch,
        )

        status = arguments.get("status")
        if status:
            q = q.filter(Query.status == status)

        queries = q.order_by(Query.start_time.desc()).limit(limit).all()

        min_elapsed = arguments.get("min_elapsed_seconds")
        result_list = []
        for query in queries:
            # start_time and end_time are Numeric(20,6) — epoch seconds
            elapsed = None
            if query.end_time and query.start_time:
                elapsed = round(float(query.end_time) - float(query.start_time), 2)

            # Apply elapsed filter in Python
            if min_elapsed and (elapsed is None or elapsed < min_elapsed):
                continue

            result_list.append(
                {
                    "id": query.id,
                    "sql_preview": (query.sql or "")[:200],
                    "status": query.status,
                    "database": (
                        query.database.database_name if query.database else None
                    ),
                    "elapsed_seconds": elapsed,
                    "rows_returned": query.rows,
                    "start_time": str(query.start_time),
                    "error_message": query.error_message,
                }
            )

        return json.dumps(
            {"queries": result_list, "total_found": len(result_list)},
            ensure_ascii=False,
            default=str,
        )
