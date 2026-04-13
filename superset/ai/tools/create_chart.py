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

import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from superset import db
from superset.ai.chart_types.registry import get_chart_registry
from superset.ai.tools.base import BaseTool
from superset.commands.chart.create import CreateChartCommand
from superset.connectors.sqla.models import SqlaTable
from superset.utils import json

logger = logging.getLogger(__name__)

# Idempotency window: skip chart creation if an identical chart was
# created within this time span (prevents LLM duplicate calls).
_IDEMPOTENCY_WINDOW_MINUTES = 10

# Supported visualization types — driven by the chart type registry
SUPPORTED_VIZ_TYPES = get_chart_registry().get_supported_types()

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

    metric_val = metric_val.strip()

    # Check if it references a saved metric
    if metric_val in saved_metrics:
        return metric_val

    if metric_val.lower() == "sum":
        if "sum__num" in saved_metrics:
            return "sum__num"
        if "num" in column_lookup:
            metric_val = "SUM(num)"

    # Try to parse "AGG(column)" format
    match = _AGG_EXPR_RE.match(metric_val)
    if not match:
        if _looks_like_sql_expression(metric_val):
            return {
                "aggregate": None,
                "column": None,
                "expressionType": "SQL",
                "hasCustomLabel": False,
                "isNew": True,
                "label": metric_val,
                "optionName": f"metric_{uuid.uuid4().hex[:12]}",
                "sqlExpression": metric_val,
            }
        raise ValueError(
            f"Unknown metric '{metric_val}'. Use a saved metric or an "
            "aggregate expression over an existing column."
        )

    aggregate = match.group(1).upper()
    col_name = match.group(2)

    # COUNT(*) uses SQL expression type — Superset SIMPLE metric requires
    # column to be a non-null dict. Using SQL type avoids this constraint.
    if col_name == "*":
        return {
            "aggregate": aggregate,
            "column": {"column_name": "__count_star_placeholder__"},
            "expressionType": "SQL",
            "hasCustomLabel": False,
            "isNew": True,
            "label": f"{aggregate}(*)",
            "optionName": f"metric_{uuid.uuid4().hex[:12]}",
            "sqlExpression": f"{aggregate}(*)",
        }

    col_info = column_lookup.get(col_name)
    if not col_info:
        available = ", ".join(sorted(column_lookup)) or "none"
        raise ValueError(
            f"Unknown metric column '{col_name}' in '{metric_val}'. "
            f"Available columns: {available}."
        )

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


def _looks_like_sql_expression(metric_val: str) -> bool:
    """Return True for custom SQL metric expressions."""
    lower = metric_val.lower()
    return (
        "(" in metric_val
        or " case " in f" {lower} "
        or lower.startswith("case ")
    )


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

    def run(self, arguments: dict[str, Any]) -> str:  # noqa: C901
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
        params_fixed = self._normalize_params(viz_type, params_dict)
        try:
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
        except ValueError as exc:
            return f"Error: Parameter validation failed: {exc}"

        # Validate: x_axis and groupby must not overlap (causes "Duplicate labels")
        # Exception: table charts use groupby for SQL GROUP BY and x_axis for
        # display; they intentionally overlap.
        x_axis = params_fixed.get("x_axis")
        groupby = params_fixed.get("groupby", [])
        if (
            x_axis
            and viz_type != "table"
            and isinstance(groupby, list)
            and x_axis in groupby
        ):
            params_fixed["groupby"] = [g for g in groupby if g != x_axis]

        # Registry-driven parameter validation
        registry = get_chart_registry()
        validation_issues = registry.validate_form_data(viz_type, params_fixed)
        if validation_issues:
            return (
                f"Error: Parameter validation failed for '{viz_type}': "
                + "; ".join(validation_issues)
                + ". Please fix and retry."
            )

        # Build form_data
        form_data = {
            "viz_type": viz_type,
            "datasource": f"{datasource_id}__table",
            **params_fixed,
        }

        # Idempotency: skip if a truly equivalent chart was created recently.
        # Compare the final normalized form_data, not the raw LLM params.
        params_hash = self._compute_params_hash(viz_type, form_data)
        existing = self._find_duplicate(
            slice_name, viz_type, datasource_id, params_hash
        )
        if existing and self._can_reuse_chart(existing):
            explore_url = f"/explore/?slice_id={existing.id}"
            logger.info(
                "Skipping duplicate chart creation: '%s' (id=%d)",
                slice_name,
                existing.id,
            )
            return json.dumps(
                {
                    "chart_id": existing.id,
                    "slice_name": existing.slice_name,
                    "viz_type": viz_type,
                    "explore_url": explore_url,
                    "message": (
                        f"Chart '{slice_name}' already exists "
                        f"(id={existing.id}). Reusing. View at: "
                        f"{explore_url}"
                    ),
                    },
                )

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
        )

    @staticmethod
    def _compute_params_hash(viz_type: str, params: dict[str, Any]) -> str:
        """Return a stable hash of the chart params for dedup.

        Normalizes metric/groupby ordering and ignores keys that don't
        affect visual equivalence (e.g. row_limit).
        """

        # Keys that affect visual output
        content_keys = {
            "metrics", "metric", "groupby", "x_axis", "y_axis",
            "granularity_sqla", "time_range", "columns",
            "source", "target", "series", "entity", "x", "y",
            "size", "metricsA", "metricsB", "column",
            # Filter fields — directly affect result set
            "adhoc_filters", "extra_filters", "where", "having",
            "filters", "row_limit",
        }
        normalized: dict[str, Any] = {}
        for k in sorted(content_keys):
            if k in params:
                val = params[k]
                # Sort lists for stable comparison
                if isinstance(val, list):
                    val = sorted(val, key=str)
                normalized[k] = val
        payload = json.dumps(
            {"viz_type": viz_type, **normalized},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @staticmethod
    def _find_duplicate(
        slice_name: str,
        viz_type: str,
        datasource_id: int,
        params_hash: str,
    ) -> Any:
        """Check for a recently-created chart with matching business key."""
        from superset.models.slice import Slice

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=_IDEMPOTENCY_WINDOW_MINUTES
        )
        candidates = (
            db.session.query(Slice)
            .filter(
                Slice.slice_name == slice_name,
                Slice.viz_type == viz_type,
                Slice.datasource_id == datasource_id,
                Slice.changed_on >= cutoff,
            )
            .all()
        )
        for s in candidates:
            try:
                stored = json.loads(s.params) if s.params else {}
            except (json.JSONDecodeError, TypeError):
                continue
            stored_hash = CreateChartTool._compute_params_hash(
                viz_type, stored
            )
            if stored_hash == params_hash:
                return s
        return None

    @staticmethod
    def _can_reuse_chart(chart: Any) -> bool:
        """Verify the current user can access the chart's datasource."""
        try:
            from superset.extensions import security_manager

            table = (
                db.session.query(SqlaTable)
                .filter_by(id=chart.datasource_id)
                .first()
            )
            if table and not security_manager.can_access_datasource(table):
                return False
            if not security_manager.can_access("can_read", "Chart"):
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_params(viz_type: str, params: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
        """Normalize LLM-generated params to match expected types.

        Handles common LLM mistakes:
        - metrics as string instead of list
        - metric as list instead of string
        - groupby as string instead of list
        - x_axis as list instead of string
        """
        fixed = dict(params)
        registry = get_chart_registry()
        desc = registry.get(viz_type)

        # Determine if this viz_type uses metric (singular) or metrics (plural)
        uses_singular = desc.uses_metric_singular if desc else False

        if uses_singular:
            # Ensure 'metric' is a single string
            if "metric" in fixed:
                val = fixed["metric"]
                if isinstance(val, list):
                    fixed["metric"] = val[0] if val else ""
                elif isinstance(val, dict):
                    fixed["metric"] = val  # pass through
            # If 'metrics' given but type expects 'metric', convert
            if "metrics" not in fixed and "metric" not in fixed:
                pass  # neither provided, will be caught by validation
            elif "metrics" in fixed and "metric" not in fixed:
                val = fixed.pop("metrics")
                if isinstance(val, list) and val:
                    fixed["metric"] = val[0]
                elif isinstance(val, str):
                    fixed["metric"] = val
        else:
            # Ensure 'metrics' is a list
            if "metrics" in fixed:
                val = fixed["metrics"]
                if isinstance(val, str):
                    fixed["metrics"] = [val]
                elif isinstance(val, dict):
                    fixed["metrics"] = [val]
            # If 'metric' given but type expects 'metrics', convert
            if "metric" in fixed and "metrics" not in fixed:
                val = fixed.pop("metric")
                if isinstance(val, str):
                    fixed["metrics"] = [val]
                elif isinstance(val, list):
                    fixed["metrics"] = val

        # Ensure groupby is always a list
        if "groupby" in fixed:
            val = fixed["groupby"]
            if isinstance(val, str):
                fixed["groupby"] = [val]

        # Ensure x_axis is always a string
        if "x_axis" in fixed:
            val = fixed["x_axis"]
            if isinstance(val, list):
                fixed["x_axis"] = val[0] if val else ""

        return fixed

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
