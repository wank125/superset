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
"""Tool to create charts in Superset via CreateChartCommand."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from superset import db
from superset.ai.tools.base import BaseTool
from superset.commands.chart.create import CreateChartCommand
from superset.connectors.sqla.models import SqlaTable

# Whitelist of supported visualization types
SUPPORTED_VIZ_TYPES = frozenset({
    "echarts_timeseries_bar",
    "echarts_timeseries_line",
    "echarts_timeseries_smooth",
    "echarts_area",
    "pie",
    "table",
    "big_number_total",
    "big_number",
})

# Regex to parse simple aggregate expressions like "SUM(col)" or "COUNT(*)"
_AGG_EXPR_RE = re.compile(
    r"^(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(\*|[A-Za-z_][\w]*)\s*\)$",
    re.IGNORECASE,
)


def _build_metric_object(  # noqa: C901
    metric_val: Any,
    column_lookup: dict[str, dict[str, Any]],
    saved_metrics: dict[str, str],
) -> Any:
    """Convert a metric value to a proper Superset metric object.

    Accepts:
      - A string referencing a saved metric name (e.g. "count")
      - A string aggregate expression (e.g. "SUM(num)")
      - A dict that is already a full metric object (pass through)
    """
    if isinstance(metric_val, dict):
        return metric_val

    if not isinstance(metric_val, str):
        return metric_val

    # Check if it references a saved metric
    if metric_val in saved_metrics:
        return metric_val

    # Try to parse "AGG(column)" format
    match = _AGG_EXPR_RE.match(metric_val.strip())
    if not match:
        # Return as-is (might be a saved metric name or custom SQL)
        return metric_val

    aggregate = match.group(1).upper()
    col_name = match.group(2)
    col_info = column_lookup.get(col_name, {})

    # COUNT(*) uses null column reference
    if col_name == "*":
        return {
            "aggregate": aggregate,
            "column": None,
            "expressionType": "SIMPLE",
            "hasCustomLabel": False,
            "isNew": True,
            "label": f"{aggregate}(*)",
            "optionName": f"metric_{uuid.uuid4().hex[:12]}",
            "sqlExpression": None,
        }

    return {
        "aggregate": aggregate,
        "column": {
            "column_name": col_name,
            "type": col_info.get("type", ""),
            "groupby": col_info.get("groupable", True),
            "filterable": col_info.get("filterable", True),
            "is_dttm": col_info.get("is_dttm", False),
        },
        "expressionType": "SIMPLE",
        "hasCustomLabel": False,
        "isNew": True,
        "label": f"{aggregate}({col_name})",
        "optionName": f"metric_{uuid.uuid4().hex[:12]}",
        "sqlExpression": None,
    }


class CreateChartTool(BaseTool):
    """Create a Superset chart using the CreateChartCommand."""

    name = "create_chart"
    description = (
        "Create a chart (visualization) in Superset. "
        "Requires datasource_id, viz_type, and params (form_data). "
        "Use search_datasets first to get the datasource_id."
    )

    parameters_schema = {
        "type": "object",
        "required": ["slice_name", "viz_type", "datasource_id", "params"],
        "properties": {
            "slice_name": {
                "type": "string",
                "description": "Chart title (1-250 characters)",
            },
            "viz_type": {
                "type": "string",
                "description": (
                    "Visualization type. Supported: echarts_timeseries_bar, "
                    "echarts_timeseries_line, echarts_timeseries_smooth, "
                    "echarts_area, pie, table, big_number_total, big_number"
                ),
            },
            "datasource_id": {
                "type": "integer",
                "description": "Dataset ID obtained from search_datasets",
            },
            "params": {
                "type": "object",
                "description": (
                    "Chart form_data containing metrics, groupby, "
                    "x_axis, granularity_sqla, time_range, etc."
                ),
            },
            "description": {
                "type": "string",
                "description": "Optional chart description",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        slice_name = arguments.get("slice_name", "")
        viz_type = arguments.get("viz_type", "")
        datasource_id = arguments.get("datasource_id")
        params_dict = arguments.get("params", {})
        description = arguments.get("description", "")

        if not slice_name:
            return "Error: slice_name is required"
        if not viz_type:
            return "Error: viz_type is required"
        if not datasource_id:
            return "Error: datasource_id is required"

        if viz_type not in SUPPORTED_VIZ_TYPES:
            supported = ", ".join(sorted(SUPPORTED_VIZ_TYPES))
            return (
                f"Error: Unsupported viz_type '{viz_type}'. "
                f"Supported types: {supported}"
            )

        # Verify the user has Chart write permission
        try:
            from superset.extensions import security_manager

            if not security_manager.can_access("can_write", "Chart"):
                return "Error: You do not have permission to create charts."
        except Exception:
            return "Error: Unable to verify chart creation permissions."

        # Look up datasource columns for metric auto-conversion
        column_lookup, saved_metrics = self._get_datasource_meta(datasource_id)

        # If metadata lookup failed (permissions or not found), deny creation
        if not column_lookup and not saved_metrics:
            return (
                f"Error: Cannot access datasource {datasource_id}. "
                "Either it does not exist or you lack permission."
            )

        # Auto-convert metrics to proper Superset metric objects
        params_fixed = dict(params_dict)
        for key in ("metrics", "metric"):
            if key in params_fixed:
                val = params_fixed[key]
                if key == "metrics" and isinstance(val, list):
                    params_fixed[key] = [
                        _build_metric_object(m, column_lookup, saved_metrics)
                        for m in val
                    ]
                elif isinstance(val, (str, dict)):
                    params_fixed[key] = _build_metric_object(
                        val, column_lookup, saved_metrics
                    )

        # Validate: x_axis and groupby must not overlap (causes "Duplicate labels")
        x_axis = params_fixed.get("x_axis")
        groupby = params_fixed.get("groupby", [])
        if x_axis and isinstance(groupby, list) and x_axis in groupby:
            params_fixed["groupby"] = [g for g in groupby if g != x_axis]

        # Build form_data
        form_data = {
            "viz_type": viz_type,
            "datasource": f"{datasource_id}__table",
            **params_fixed,
        }

        # Construct the data dict for CreateChartCommand
        chart_data: dict[str, Any] = {
            "slice_name": slice_name[:250],
            "viz_type": viz_type,
            "params": json.dumps(form_data, sort_keys=True),
            "datasource_id": datasource_id,
            "datasource_type": "table",
        }
        if description:
            chart_data["description"] = description

        try:
            command = CreateChartCommand(chart_data)
            chart = command.run()
        except Exception as exc:
            return f"Error creating chart: {exc}"

        explore_url = f"/explore/?slice_id={chart.id}"
        return json.dumps(
            {
                "chart_id": chart.id,
                "slice_name": chart.slice_name,
                "viz_type": viz_type,
                "explore_url": explore_url,
                "message": (
                    f"Chart '{slice_name}' created successfully! "
                    f"View at: {explore_url}"
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _get_datasource_meta(
        datasource_id: int,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """Fetch column and saved-metric metadata from the SqlaTable."""
        table = db.session.query(SqlaTable).get(datasource_id)
        if not table:
            return {}, {}

        # Permission check: verify the current user can access this datasource
        try:
            from superset.extensions import security_manager

            if not security_manager.can_access_datasource(table):
                return {}, {}
        except Exception:
            return {}, {}

        col_lookup: dict[str, dict[str, Any]] = {}
        for col in table.columns:
            col_lookup[col.column_name] = {
                "type": str(col.type),
                "groupable": col.groupby,
                "filterable": col.filterable,
                "is_dttm": col.is_dttm,
            }

        saved: dict[str, str] = {m.metric_name: m.expression for m in table.metrics}
        return col_lookup, saved
