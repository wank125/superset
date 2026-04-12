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
"""Tool to check alert and report execution status."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class ReportStatusTool(BaseTool):
    """Check status of alerts and scheduled reports."""

    name = "report_status"
    description = (
        "Check status of alerts and scheduled reports. "
        "Returns execution history, last success/failure time, and schedule. "
        "Optionally filter by name, type (Alert/Report), or only show failures."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Filter by report name keyword",
            },
            "type": {
                "type": "string",
                "enum": ["Alert", "Report"],
                "description": "Filter by type: Alert or Report",
            },
            "only_failed": {
                "type": "boolean",
                "description": "Only show reports with errors",
            },
            "include_logs": {
                "type": "boolean",
                "description": "Include recent execution logs",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.report import ReportScheduleDAO
        from superset.extensions import security_manager

        # Permission check — early exit if no access
        if not security_manager.can_access("can_read", "ReportSchedule"):
            return json.dumps(
                {"error": "No permission to read report schedules"}
            )

        reports = ReportScheduleDAO.find_all()
        search = (arguments.get("search") or "").lower()
        report_type = arguments.get("type")
        only_failed = arguments.get("only_failed", False)
        include_logs = arguments.get("include_logs", False)

        result = []
        for r in reports:
            if search and search not in r.name.lower():
                continue
            if report_type and r.type != report_type:
                continue

            # Get last execution log
            last_log = r.logs[0] if r.logs else None
            last_state = last_log.state if last_log else None

            if only_failed and last_state and last_state.lower() != "error":
                continue

            entry: dict[str, Any] = {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "active": r.active,
                "crontab": r.crontab,
                "timezone": r.timezone,
                "last_state": last_state,
                "last_run": str(last_log.start_dttm) if last_log else None,
                "description": r.description,
                "chart_id": r.chart_id,
                "dashboard_id": r.dashboard_id,
            }

            if include_logs:
                entry["recent_logs"] = [
                    {
                        "state": log.state,
                        "scheduled": str(log.scheduled_dttm),
                        "start": str(log.start_dttm) if log.start_dttm else None,
                        "end": str(log.end_dttm) if log.end_dttm else None,
                        "error": log.error_message,
                    }
                    for log in r.logs[:5]
                ]

            result.append(entry)

        return json.dumps(
            {"reports": result[:20], "total": len(result)},
            ensure_ascii=False,
            default=str,
        )
