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
"""Child graph nodes — single chart generation pipeline.

Nodes:
  C1 plan_query            [LLM]
  C2 validate_sql          [Code]
  C3 execute_query         [Code]
  C4 analyze_result        [Code]
  C5 select_chart          [LLM]
  C6 normalize_chart_params [Code]
  C7 repair_chart_params   [LLM]
  C8 create_chart          [Code]
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from langgraph.types import Command

from superset.ai.graph.llm_helpers import llm_call_json
from superset.ai.graph.state import ResultSummary, SchemaSummary, SingleChartState
from superset.utils import json

logger = logging.getLogger(__name__)

_MAX_SQL_ATTEMPTS = 3
_MAX_REPAIR_ATTEMPTS = 3
_AGG_EXPR_RE = re.compile(
    r"^(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(\*|[A-Za-z_][\w]*)\s*\)$",
    re.IGNORECASE,
)

# ── Prompts ─────────────────────────────────────────────────────────

PLAN_QUERY_PROMPT = """\
Generate a SQL query plan. Return ONLY valid JSON.

Chart goal: {analysis_intent} — "{slice_name}"
{sql_hint}
Table: {table_name}
Columns:
  time: {datetime_cols}
  dimensions: {dimension_cols}
  metrics: {metric_cols}
  saved_metrics: {saved_metrics}
{error_hint}

Output:
{{
  "metric_expr": "<aggregate SQL expression such as SUM(col)>",
  "dimensions": ["<groupby cols>"],
  "time_field": "<datetime col or null>",
  "time_grain": "<month|day|year|null>",
  "filters": [],
  "order_by": "<col ASC|DESC or null>",
  "limit": 200
}}

Rules:
- Only use column names from the lists above
- Do not use saved metric names in metric_expr; convert them to SQL expressions
- For trend charts: include time_field
- For composition: include 1 low-cardinality dimension
- LIMIT max 500
"""

SELECT_CHART_PROMPT = """\
Choose the best chart type. Return ONLY valid JSON.

Chart goal: {analysis_intent} — "{slice_name}"
User preferred: {preferred_viz}

Data suitability:
{suitability_flags}
Columns: time={datetime_col}, numeric={numeric_cols}, low-cardinality={low_card_cols}

Chart types reference:
echarts_timeseries_line  → good_for_trend=true, needs time_field + metric
echarts_timeseries_bar   → good_for_comparison/trend, needs x_field + metrics
echarts_area             → good_for_trend, needs time_field + metric
pie                      → good_for_composition, needs metric(singular) + groupby
big_number_total         → good_for_kpi, needs metric(singular) only
table                    → always works, needs metrics + groupby
bar_chart (horizontal)   → good_for_comparison, needs x_field + metrics

Output:
{{
  "viz_type": "<chosen>",
  "slice_name": "<chart title>",
  "semantic_params": {{
    "time_field": "<or null>",
    "metric": "<SUM(col) — for singular, e.g. pie/kpi>",
    "metrics": ["<SUM(col)>", ...],
    "groupby": ["<string col>"],
    "x_field": "<col for bar x-axis or null>"
  }},
  "rationale": "<one sentence>"
}}

Rules:
- If User preferred is set and compatible with the data, use that viz_type
"""

REPAIR_PROMPT = """\
Fix the chart plan to resolve a parameter error.
Return ONLY the corrected chart_plan JSON (same structure).

Error: {error}
Current plan: {plan}
Schema:
  datetime cols: {datetime_cols}
  numeric cols: {numeric_cols}
  string cols: {string_cols}
"""


# ── Node C1: plan_query [LLM] ──────────────────────────────────────


def plan_query(
    state: SingleChartState,
) -> Command[Literal["validate_sql"]]:
    summary: SchemaSummary = state["schema_summary"]
    intent = state["chart_intent"]
    error_hint = ""
    last_err = state.get("last_error")
    if last_err and last_err.get("node") in ("validate_sql", "execute_query"):
        error_hint = (
            f"\nPrevious attempt failed: {last_err['message']}\nFix the plan."
        )

    sql_hint = intent.get("sql_hint", "")
    prompt = PLAN_QUERY_PROMPT.format(
        analysis_intent=intent["analysis_intent"],
        slice_name=intent["slice_name"],
        sql_hint=f"Hint: {sql_hint}" if sql_hint else "",
        table_name=summary["table_name"],
        datetime_cols=summary["datetime_cols"],
        dimension_cols=summary["dimension_cols"],
        metric_cols=summary["metric_cols"],
        saved_metrics=summary["saved_metrics"],
        error_hint=error_hint,
    )
    sql_plan = llm_call_json(prompt)
    return Command(update={"sql_plan": sql_plan}, goto="validate_sql")


# ── Node C2: validate_sql [Code] ───────────────────────────────────


def validate_sql(
    state: SingleChartState,
) -> Command[Literal["execute_query", "plan_query"]]:
    plan = state["sql_plan"]
    summary: SchemaSummary = state["schema_summary"]
    table = summary["table_name"]
    plan = _normalize_sql_plan(plan or {}, summary)

    # Compile SQL from plan
    try:
        sql = _compile_sql(plan, table)
    except ValueError as exc:
        if state.get("sql_attempts", 0) >= _MAX_SQL_ATTEMPTS:
            return Command(
                update={
                    "last_error": {
                        "node": "validate_sql",
                        "type": "compile_failed",
                        "message": str(exc),
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
        return Command(
            update={
                "last_error": {
                    "node": "validate_sql",
                    "type": "compile_error",
                    "message": str(exc),
                    "recoverable": True,
                },
                "sql_attempts": state.get("sql_attempts", 0) + 1,
            },
            goto="plan_query",
        )

    # Static validation
    known_cols = set(
        summary["datetime_cols"]
        + summary["dimension_cols"]
        + summary["metric_cols"]
    )
    issues = _validate_sql_static(sql, known_cols)

    if issues:
        if state.get("sql_attempts", 0) >= _MAX_SQL_ATTEMPTS:
            return Command(
                update={
                    "last_error": {
                        "node": "validate_sql",
                        "type": "validation_failed",
                        "message": "; ".join(issues),
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
        return Command(
            update={
                "last_error": {
                    "node": "validate_sql",
                    "type": "field_not_found",
                    "message": "; ".join(issues),
                    "recoverable": True,
                },
                "sql_attempts": state.get("sql_attempts", 0) + 1,
            },
            goto="plan_query",
        )

    return Command(
        update={
            "sql": sql,
            "sql_plan": plan,
            "sql_valid": True,
            "last_error": None,
        },
        goto="execute_query",
    )


def _normalize_sql_plan(
    plan: dict[str, Any],
    summary: SchemaSummary,
) -> dict[str, Any]:
    """Make LLM SQL plans executable before compiling SQL."""
    normalized = dict(plan)
    metric_cols = summary["metric_cols"]
    valid_cols = set(
        summary["datetime_cols"]
        + summary["dimension_cols"]
        + metric_cols
    )

    normalized["metric_expr"] = _normalize_metric_expr(
        normalized.get("metric_expr"),
        metric_cols,
        summary.get("saved_metric_expressions", {}),
    )

    dimensions = normalized.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    normalized["dimensions"] = [dim for dim in dimensions if dim in valid_cols]

    if normalized.get("time_field") not in summary["datetime_cols"]:
        normalized["time_field"] = None

    order_by = normalized.get("order_by")
    if order_by:
        order_col = str(order_by).split()[0]
        if order_col not in valid_cols:
            normalized["order_by"] = None

    return normalized


def _normalize_metric_expr(
    metric_expr: Any,
    metric_cols: list[str],
    saved_metric_expressions: dict[str, str],
) -> str:
    """Return an executable aggregate expression for the SQL query."""
    fallback = f"SUM({metric_cols[0]})" if metric_cols else "COUNT(*)"

    if isinstance(metric_expr, list):
        metric_expr = metric_expr[0] if metric_expr else None
    if not metric_expr:
        return fallback

    metric = str(metric_expr).strip()
    if metric in saved_metric_expressions:
        expression = saved_metric_expressions[metric].strip()
        return expression or fallback

    if metric in metric_cols:
        return f"SUM({metric})"

    match = _AGG_EXPR_RE.match(metric)
    if match:
        col_name = match.group(2)
        if col_name == "*" or col_name in metric_cols:
            return metric

    return fallback


def _compile_sql(plan: dict[str, Any], table: str) -> str:
    """Convert sql_plan dict to executable SQL."""
    metric = plan.get("metric_expr", "COUNT(*)")
    if isinstance(metric, list):
        metric = ", ".join(metric)
    dimensions = plan.get("dimensions", [])
    time_field = plan.get("time_field")
    limit = min(int(plan.get("limit", 200)), 500)

    select_cols: list[str] = []
    group_cols: list[str] = []

    if time_field:
        select_cols.append(time_field)
        group_cols.append(time_field)

    for dim in dimensions:
        if dim and dim not in group_cols:
            select_cols.append(dim)
            group_cols.append(dim)

    select_cols.append(metric)

    sql = f"SELECT {', '.join(select_cols)} FROM {table}"  # noqa: S608
    if group_cols:
        sql += f" GROUP BY {', '.join(group_cols)}"

    order_by = plan.get("order_by")
    if order_by:
        sql += f" ORDER BY {order_by}"

    sql += f" LIMIT {limit}"
    return sql


def _validate_sql_static(sql: str, known_cols: set[str]) -> list[str]:
    """Static checks: no DDL/DML, LIMIT exists."""
    issues: list[str] = []
    sql_upper = sql.upper().strip()

    for forbidden in (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    ):
        if sql_upper.startswith(forbidden):
            issues.append(f"Forbidden statement type: {forbidden}")

    if "LIMIT" not in sql_upper:
        issues.append("Missing LIMIT clause")

    return issues


# ── Node C3: execute_query [Code] ──────────────────────────────────


def execute_query(
    state: SingleChartState,
) -> Command[Literal["analyze_result", "plan_query"]]:
    from superset.ai.tools.execute_sql import ExecuteSqlTool

    tool = ExecuteSqlTool(database_id=state["database_id"])
    result_str = tool.run({"sql": state["sql"]})

    if result_str.startswith("Error"):
        if state.get("sql_attempts", 0) >= _MAX_SQL_ATTEMPTS:
            return Command(
                update={
                    "last_error": {
                        "node": "execute_query",
                        "type": "sql_execution_error",
                        "message": result_str,
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
        return Command(
            update={
                "last_error": {
                    "node": "execute_query",
                    "type": "sql_execution_error",
                    "message": result_str,
                    "recoverable": True,
                },
                "sql_attempts": state.get("sql_attempts", 0) + 1,
            },
            goto="plan_query",
        )

    return Command(
        update={"query_result_raw": result_str, "last_error": None},
        goto="analyze_result",
    )


# ── Node C4: analyze_result [Code] ─────────────────────────────────


def analyze_result(
    state: SingleChartState,
) -> Command[Literal["select_chart"]]:
    from superset.ai.tools.analyze_data import AnalyzeDataTool

    raw_str = state["query_result_raw"] or ""
    columns, rows = AnalyzeDataTool._parse_text_table(raw_str)
    col_analysis = AnalyzeDataTool._analyze_columns(columns, rows)

    row_count = len(rows)
    datetime_col = next(
        (
            c["name"]
            for c in col_analysis
            if any(
                kw in c["name"].lower()
                for kw in ("date", "ds", "time", "year", "month", "day", "dttm")
            )
        ),
        None,
    )
    numeric_cols = [c["name"] for c in col_analysis if c["type"] == "numeric"]
    string_cols = [c["name"] for c in col_analysis if c["type"] == "string"]
    low_card_cols = [
        c["name"]
        for c in col_analysis
        if c["type"] == "string" and c.get("distinct_count", 999) < 20
    ]

    dt_cardinality = 0
    if datetime_col:
        dt_info = next(
            (c for c in col_analysis if c["name"] == datetime_col), {}
        )
        dt_cardinality = dt_info.get("distinct_count", 0)

    flags: dict[str, bool] = {
        "good_for_trend": bool(
            datetime_col and numeric_cols and dt_cardinality > 3
        ),
        "good_for_composition": bool(low_card_cols and numeric_cols),
        "good_for_kpi": row_count == 1 and len(numeric_cols) == 1,
        "good_for_distribution": (
            row_count > 10 and len(numeric_cols) == 1 and not string_cols
        ),
        "good_for_comparison": bool(low_card_cols and numeric_cols),
        "good_for_table": True,
    }

    summary: ResultSummary = {
        "row_count": row_count,
        "columns": col_analysis,
        "has_datetime": datetime_col is not None,
        "datetime_col": datetime_col,
        "datetime_cardinality": dt_cardinality,
        "numeric_cols": numeric_cols,
        "string_cols": string_cols,
        "low_cardinality_cols": low_card_cols,
        "suitability_flags": flags,
    }
    return Command(update={"query_result_summary": summary}, goto="select_chart")


# ── Node C5: select_chart [LLM] ────────────────────────────────────


def select_chart(
    state: SingleChartState,
) -> Command[Literal["normalize_chart_params"]]:
    intent = state["chart_intent"]
    result_summary = state["query_result_summary"]
    flags = result_summary["suitability_flags"]

    flag_str = "\n".join(
        f"  {k}=true" for k, v in flags.items() if v
    ) or "  (no strong signal, use table)"

    prompt = SELECT_CHART_PROMPT.format(
        analysis_intent=intent["analysis_intent"],
        slice_name=intent["slice_name"],
        preferred_viz=intent.get("preferred_viz", "auto"),
        suitability_flags=flag_str,
        datetime_col=result_summary.get("datetime_col"),
        numeric_cols=result_summary["numeric_cols"][:4],
        low_card_cols=result_summary["low_cardinality_cols"][:4],
    )
    chart_plan = llm_call_json(prompt)
    preferred_viz = intent.get("preferred_viz")
    if preferred_viz:
        chart_plan["viz_type"] = preferred_viz
    return Command(
        update={"chart_plan": chart_plan},
        goto="normalize_chart_params",
    )


# ── Node C6: normalize_chart_params [Code] ─────────────────────────


def normalize_chart_params(
    state: SingleChartState,
) -> Command[Literal["create_chart", "repair_chart_params"]]:
    if state.get("repair_attempts", 0) >= _MAX_REPAIR_ATTEMPTS:
        return Command(
            update={
                "last_error": {
                    "node": "normalize_chart_params",
                    "type": "max_repairs",
                    "message": "超过最大修复次数，跳过此图表",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    summary: SchemaSummary = state["schema_summary"]
    saved_metrics = summary.get("saved_metric_expressions") or {
        m: m for m in summary.get("saved_metrics", [])
    }
    chart_plan = state["chart_plan"]

    if not chart_plan:
        return Command(
            update={
                "last_error": {
                    "node": "normalize_chart_params",
                    "type": "missing_plan",
                    "message": "chart_plan is None",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    try:
        from superset.ai.graph.normalizer import compile_superset_form_data

        form_data = compile_superset_form_data(
            chart_plan=chart_plan,
            schema_summary=summary,
            saved_metrics_lookup=saved_metrics,
        )
        return Command(
            update={"chart_form_data": form_data, "last_error": None},
            goto="create_chart",
        )
    except ValueError as exc:
        return Command(
            update={
                "last_error": {
                    "node": "normalize_chart_params",
                    "type": "compile_error",
                    "message": str(exc),
                    "recoverable": True,
                },
                "repair_attempts": state.get("repair_attempts", 0) + 1,
            },
            goto="repair_chart_params",
        )


# ── Node C7: repair_chart_params [LLM] ─────────────────────────────


def repair_chart_params(
    state: SingleChartState,
) -> Command[Literal["normalize_chart_params"]]:
    summary: SchemaSummary = state["schema_summary"]
    last_err = state.get("last_error", {})
    chart_plan = state.get("chart_plan", {})

    prompt = REPAIR_PROMPT.format(
        error=last_err.get("message", "unknown"),
        plan=json.dumps(chart_plan),
        datetime_cols=summary["datetime_cols"],
        numeric_cols=summary["metric_cols"],
        string_cols=summary["dimension_cols"],
    )
    fixed = llm_call_json(prompt)
    return Command(update={"chart_plan": fixed}, goto="normalize_chart_params")


# ── Node C8: create_chart [Code] ───────────────────────────────────


def create_chart(
    state: SingleChartState,
) -> Command[Literal["__end__"]]:
    from superset.ai.tools.create_chart import CreateChartTool

    chart_plan = state["chart_plan"]
    if not chart_plan:
        return Command(
            update={
                "last_error": {
                    "message": "No chart plan available",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    slice_name = chart_plan.get("slice_name", "AI Chart")
    viz_type = chart_plan.get("viz_type", "table")
    datasource_id = state["schema_summary"]["datasource_id"]

    # Idempotency: skip if same chart was created within 10 minutes
    existing = _find_recent_chart(slice_name, viz_type, datasource_id)
    if existing:
        return Command(
            update={
                "created_chart": {
                    "chart_id": existing.id,
                    "slice_name": existing.slice_name,
                    "viz_type": viz_type,
                    "explore_url": f"/explore/?slice_id={existing.id}",
                    "message": f"Reusing existing chart id={existing.id}",
                },
            },
            goto="__end__",
        )

    tool = CreateChartTool()
    result_str = tool.run({
        "slice_name": slice_name,
        "viz_type": viz_type,
        "datasource_id": datasource_id,
        "params": state["chart_form_data"],
    })

    if result_str.startswith("Error"):
        if state.get("repair_attempts", 0) < 2:
            return Command(
                update={
                    "last_error": {
                        "node": "create_chart",
                        "type": "create_fail",
                        "message": result_str,
                        "recoverable": True,
                    },
                    "repair_attempts": state.get("repair_attempts", 0) + 1,
                },
                goto="repair_chart_params",
            )
        return Command(
            update={
                "last_error": {
                    "message": result_str,
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    return Command(
        update={"created_chart": json.loads(result_str)},
        goto="__end__",
    )


def _find_recent_chart(
    slice_name: str, viz_type: str, datasource_id: int
) -> Any:
    """Find a recently-created chart with matching name/type/datasource."""
    from superset import db
    from superset.models.slice import Slice

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    return (
        db.session.query(Slice)
        .filter(
            Slice.slice_name == slice_name,
            Slice.viz_type == viz_type,
            Slice.datasource_id == datasource_id,
            Slice.changed_on >= cutoff,
        )
        .first()
    )
