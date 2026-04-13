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
"""Chart parameter normalizer — semantic params → Superset form_data.

Six rules applied in order:
  R1  metric ↔ metrics singular/plural
  R2  groupby string → list
  R3  x_axis list → string
  R4  metric/metrics expression → Superset metric object
  R5  time_field → granularity_sqla + time_range
  R6  datasource + viz_type injection
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from superset.ai.graph.state import ChartPlan, SchemaSummary

# Regex for simple aggregate expressions like "SUM(col)" or "COUNT(*)"
_AGG_EXPR_RE = re.compile(
    r"^(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(\*|[A-Za-z_][\w]*)\s*\)$",
    re.IGNORECASE,
)


def compile_superset_form_data(  # noqa: C901
    chart_plan: ChartPlan,
    schema_summary: SchemaSummary,
    saved_metrics_lookup: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert semantic chart_plan params into a Superset-compatible form_data dict.

    Raises ValueError if required parameters are missing or invalid.
    """
    saved_metrics_lookup = saved_metrics_lookup or {}
    semantic = chart_plan.get("semantic_params", {})
    viz_type = chart_plan.get("viz_type", "table")
    datasource_id = schema_summary["datasource_id"]
    column_lookup = _build_column_lookup(schema_summary)

    form_data: dict[str, Any] = {
        "viz_type": viz_type,
        "datasource": f"{datasource_id}__table",
    }

    # R1: Determine metric/metrics based on viz_type
    uses_singular = _uses_metric_singular(viz_type)

    if uses_singular:
        metric_val = semantic.get("metric") or semantic.get("metrics")
        if metric_val is None:
            raise ValueError(
                f"'{viz_type}' requires 'metric' (singular) but none provided"
            )
        if isinstance(metric_val, list):
            metric_val = metric_val[0] if metric_val else ""
        form_data["metric"] = _build_metric_object(
            metric_val, column_lookup, saved_metrics_lookup
        )
    else:
        metrics_val = semantic.get("metrics") or semantic.get("metric")
        if metrics_val is None:
            # Some chart types (e.g. table) allow empty metrics
            metrics_val = []
        if isinstance(metrics_val, str):
            metrics_val = [metrics_val]
        elif isinstance(metrics_val, dict):
            metrics_val = [metrics_val]
        form_data["metrics"] = [
            _build_metric_object(m, column_lookup, saved_metrics_lookup)
            for m in metrics_val
        ]

    # R2: groupby → always list
    groupby = semantic.get("groupby")
    if groupby is not None:
        if isinstance(groupby, str):
            groupby = [groupby]
        form_data["groupby"] = groupby

    # R2b: for table-type charts, merge x_axis into groupby so the SQL
    # includes the dimension column in GROUP BY (table uses groupby, not x_axis)
    if viz_type == "table":
        x_dim = semantic.get("x_field") or semantic.get("x_axis")
        if x_dim:
            if isinstance(x_dim, list):
                x_dim = x_dim[0] if x_dim else None
            if x_dim:
                existing = form_data.get("groupby", [])
                if isinstance(existing, list) and x_dim not in existing:
                    form_data["groupby"] = existing + [x_dim]
                elif not existing:
                    form_data["groupby"] = [x_dim]

    # R3: x_axis → always string
    x_field = semantic.get("x_field") or semantic.get("x_axis")
    if not x_field and "echarts" in viz_type:
        time_field = semantic.get("time_field")
        if time_field:
            x_field = time_field
        else:
            groupby = form_data.get("groupby", [])
            if isinstance(groupby, list) and groupby:
                x_field = groupby[0]
    if x_field is not None:
        if isinstance(x_field, list):
            x_field = x_field[0] if x_field else ""
        form_data["x_axis"] = x_field

    # R4 is handled inline above via _build_metric_object

    # R5: time_field → granularity_sqla + time_range
    time_field = semantic.get("time_field")
    # big_number / big_number_total require granularity_sqla;
    # auto-fill from schema datetime cols when LLM omits time_field
    if not time_field and viz_type in {"big_number", "big_number_total"}:
        datetime_cols = schema_summary.get("datetime_cols", [])
        if datetime_cols:
            time_field = datetime_cols[0]
    if time_field:
        form_data["granularity_sqla"] = time_field
        form_data["time_range"] = "No filter"

    # x_axis and groupby must not overlap (except table, where x_axis is groupby)
    x_axis = form_data.get("x_axis")
    groupby = form_data.get("groupby", [])
    if (
        x_axis
        and viz_type != "table"
        and isinstance(groupby, list)
        and x_axis in groupby
    ):
        form_data["groupby"] = [g for g in groupby if g != x_axis]

    # R6: datasource and viz_type already set at top

    return form_data


def _uses_metric_singular(viz_type: str) -> bool:
    """Return True if the viz_type uses 'metric' (singular) instead of 'metrics'."""
    from superset.ai.chart_types.registry import get_chart_registry

    registry = get_chart_registry()
    desc = registry.get(viz_type)
    if desc:
        return desc.uses_metric_singular
    # Fallback: known singular types
    return viz_type in {"pie", "big_number_total", "big_number"}


def _build_column_lookup(
    schema_summary: SchemaSummary,
) -> dict[str, dict[str, Any]]:
    """Build column metadata lookup from schema_summary."""
    from superset import db
    from superset.connectors.sqla.models import SqlaTable

    datasource_id = schema_summary["datasource_id"]
    table = db.session.query(SqlaTable).get(datasource_id)
    if not table:
        return {}

    lookup: dict[str, dict[str, Any]] = {}
    for col in table.columns:
        lookup[col.column_name] = {
            "type": str(col.type),
            "groupable": col.groupby,
            "filterable": col.filterable,
            "is_dttm": col.is_dttm,
        }
    return lookup


def _build_metric_object(  # noqa: C901
    metric_val: Any,
    column_lookup: dict[str, dict[str, Any]],
    saved_metrics: dict[str, str],
) -> Any:
    """Convert a metric value to a proper Superset metric object.

    Accepts:
      - A string referencing a saved metric name
      - A string aggregate expression (e.g. "SUM(col)")
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
