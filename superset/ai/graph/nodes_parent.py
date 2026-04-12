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
"""Parent graph nodes — dashboard-level orchestration.

Nodes:
  P1 parse_request      [LLM]
  P2 search_dataset     [Code]
  P3 select_dataset     [Code]
  P4 read_schema        [Code]
  P5 plan_dashboard     [LLM]
  P6 create_dashboard   [Code]
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from langgraph.types import Command

from superset.ai.graph.llm_helpers import llm_call_json, llm_call_json_list
from superset.ai.graph.state import DashboardState, SchemaSummary
from superset.utils import json

logger = logging.getLogger(__name__)

# ── Prompts ─────────────────────────────────────────────────────────

PARSE_PROMPT = """\
Extract structured intent from a chart/dashboard request.
Return ONLY valid JSON.
{context}

{{
  "task": "build_chart" or "build_dashboard",
  "target_table": "<table keyword or null>",
  "analysis_intent": "<trend|comparison|composition|distribution|kpi>",
  "preferred_viz": "<viz_type or null>",
  "chart_count": <int, 1 for single chart>,
  "time_hint": "<monthly|daily|yearly|null>",
  "user_language": "zh" or "en"
}}

Request: {request}
"""

PLAN_DASHBOARD_PROMPT = """\
Plan chart intents for a dashboard. Return ONLY valid JSON array.

User request: {request}
Analysis goal: {analysis_intent}
Preferred visualization: {preferred_viz}
Available columns:
  time: {datetime_cols}
  dimensions: {dimension_cols}
  metrics: {metric_cols}
Requested chart count: {chart_count}

Output (array of {chart_count} items):
[
  {{
    "chart_index": 0,
    "analysis_intent": "<trend|comparison|composition|distribution|kpi>",
    "preferred_viz": "<preferred_viz or null>",
    "slice_name": "<chart title>",
    "sql_hint": "<optional hint for SQL generation>"
  }}
]

Rules:
- If chart_count=1, output exactly 1 item
- Different charts should show different aspects of the data
- slice_name should be in {user_language}
"""


# ── Helpers ─────────────────────────────────────────────────────────


def _is_string_type(t: str) -> bool:
    return any(
        k in t.upper()
        for k in ("VARCHAR", "TEXT", "STRING", "CHAR", "NVARCHAR")
    )


def _is_numeric_type(t: str) -> bool:
    return any(
        k in t.upper()
        for k in (
            "FLOAT", "DOUBLE", "INT", "DECIMAL", "NUMERIC", "BIGINT", "REAL",
        )
    )


def _normalize_preferred_viz(value: Any) -> str | None:
    """Normalize model/user chart aliases to supported Superset viz_type values."""
    if not value:
        return None

    raw = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "bar": "echarts_timeseries_bar",
        "bar_chart": "echarts_timeseries_bar",
        "column": "echarts_timeseries_bar",
        "column_chart": "echarts_timeseries_bar",
        "line": "echarts_timeseries_line",
        "line_chart": "echarts_timeseries_line",
        "折线图": "echarts_timeseries_line",
        "柱状图": "echarts_timeseries_bar",
        "饼图": "pie",
        "pie_chart": "pie",
        "table_chart": "table",
    }
    normalized = aliases.get(raw, raw)

    from superset.ai.chart_types.registry import get_chart_registry

    return normalized if get_chart_registry().get(normalized) else None


# ── Node P1: parse_request [LLM] ───────────────────────────────────


def parse_request(
    state: DashboardState,
) -> Command[Literal["search_dataset", "__end__"]]:
    # Build context from conversation history for follow-up understanding
    history_lines = ""
    for entry in (state.get("conversation_history") or [])[:-1]:
        role = entry.get("role", "")
        content = entry.get("content", "")[:200]
        if role and content:
            history_lines += f"\n{role}: {content}"
    context_block = f"\nConversation history:{history_lines}" if history_lines else ""

    prompt = PARSE_PROMPT.format(
        request=state["request"][:500],
        context=context_block,
    )
    try:
        goal = llm_call_json(prompt)
    except ValueError as exc:
        logger.warning("parse_request LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "type": "llm_format_error",
                    "message": f"Failed to parse request: {exc}",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
    goal["preferred_viz"] = _normalize_preferred_viz(goal.get("preferred_viz"))

    # Force single-chart mode when agent_mode == "chart"
    if state.get("agent_mode") == "chart":
        goal["chart_count"] = 1
        goal["task"] = "build_chart"

    return Command(update={"goal": goal}, goto="search_dataset")


# ── Node P2: search_dataset [Code] ─────────────────────────────────


def search_dataset(
    state: DashboardState,
) -> Command[Literal["select_dataset", "__end__"]]:
    from superset.ai.tools.search_datasets import SearchDatasetsTool

    tool = SearchDatasetsTool(
        database_id=state["database_id"],
        schema_name=state.get("schema_name"),
    )
    target = state.get("goal", {}).get("target_table", "")
    if not target:
        # No table name extracted — try to search by a keyword from the request
        target = state["request"][:50]
    result_str = tool.run({"table_name": target})

    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        return Command(
            update={
                "last_error": {
                    "type": "tool_error",
                    "message": f"Unexpected tool response: {result_str[:200]}",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    status = result.get("status", "")

    # Error case
    if status == "error":
        return Command(
            update={
                "last_error": {
                    "type": "tool_error",
                    "message": result.get("message", "Unknown error"),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Found: single dataset match
    if status == "found":
        return Command(
            update={"dataset_candidates": [result]},
            goto="select_dataset",
        )

    # Not found: use available_datasets list as candidates
    candidates = result.get("available_datasets", [])
    if not candidates:
        return Command(
            update={
                "last_error": {
                    "type": "no_dataset",
                    "message": result.get(
                        "message", "未找到可用数据集，请指定表名"
                    ),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
    return Command(
        update={"dataset_candidates": candidates},
        goto="select_dataset",
    )


# ── Node P3: select_dataset [Code] ──────────────────────────────────


def select_dataset(  # noqa: C901
    state: DashboardState,
) -> Command[Literal["read_schema", "search_dataset"]]:
    candidates = state.get("dataset_candidates", [])
    target = (state.get("goal", {}) or {}).get("target_table") or ""
    target = target.lower()

    # No candidates → return error
    if not candidates:
        return Command(
            update={
                "last_error": {
                    "type": "no_dataset",
                    "message": "未找到可用数据集，请指定表名",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Single candidate → auto-select
    if len(candidates) == 1:
        candidate = candidates[0]
        selected = (
            candidate
            if isinstance(candidate, dict)
            else {"table_name": candidate}
        )
        # Re-search if candidate only has table_name
        if (
            "datasource_id" not in selected
            and selected.get("table_name")
        ):
            goal = {
                **state.get("goal", {}),
                "target_table": selected["table_name"],
            }
            return Command(
                update={"goal": goal},
                goto="search_dataset",
            )
        return Command(
            update={"selected_dataset": selected},
            goto="read_schema",
        )

    # Multiple candidates → score-based selection (auto-pick best)
    scored: list[tuple[int, Any]] = []
    for c in candidates:
        name = c.get("table_name", c) if isinstance(c, dict) else c
        name_lower = str(name).lower()
        if name_lower == target:
            score = 100
        elif name_lower.startswith(target):
            score = 50
        elif target in name_lower:
            score = 20
        else:
            score = 0
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    # Require a minimum score to avoid selecting an unrelated dataset
    if best_score == 0:
        return Command(
            update={
                "last_error": {
                    "type": "no_dataset",
                    "message": (
                        f"未找到与「{target}」匹配的数据集，"
                        f"请指定准确的表名"
                    ),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Auto-select the best match (interrupt requires checkpointer
    # which isn't available in the stream-only run_graph path)
    selected = (
        best
        if isinstance(best, dict)
        else {"table_name": best}
    )
    if (
        "datasource_id" not in selected
        and selected.get("table_name")
    ):
        goal = {
            **state.get("goal", {}),
            "target_table": selected["table_name"],
        }
        return Command(
            update={"goal": goal},
            goto="search_dataset",
        )
    return Command(
        update={"selected_dataset": selected},
        goto="read_schema",
    )


# ── Node P4: read_schema [Code] ────────────────────────────────────


def read_schema(
    state: DashboardState,
) -> Command[Literal["plan_dashboard"]]:
    dataset = state["selected_dataset"]
    raw = dataset  # SearchDatasetsTool already returns complete info
    columns = raw.get("columns", [])

    datetime_cols = [c["name"] for c in columns if c.get("is_dttm")]
    dimension_cols = [
        c["name"]
        for c in columns
        if not c.get("is_dttm")
        and c.get("groupable")
        and _is_string_type(c.get("type", ""))
    ]
    metric_cols = [
        c["name"]
        for c in columns
        if _is_numeric_type(c.get("type", ""))
    ]
    saved_metrics = [m["name"] for m in raw.get("metrics", [])]
    saved_metric_expressions = {
        m["name"]: m.get("expression", "")
        for m in raw.get("metrics", [])
        if m.get("name") and m.get("expression")
    }
    main_dttm = raw.get("main_datetime_column")

    summary: SchemaSummary = {
        "datasource_id": raw["datasource_id"],
        "table_name": raw["table_name"],
        "datetime_cols": datetime_cols[:5],
        "dimension_cols": dimension_cols[:10],
        "metric_cols": metric_cols[:10],
        "saved_metrics": saved_metrics[:10],
        "saved_metric_expressions": saved_metric_expressions,
        "main_dttm_col": main_dttm,
    }
    return Command(
        update={"schema_raw": raw, "schema_summary": summary},
        goto="plan_dashboard",
    )


# ── Node P5: plan_dashboard [LLM] ──────────────────────────────────


def plan_dashboard(
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph"]]:
    goal = state.get("goal", {})
    summary = state.get("schema_summary")
    if not summary:
        return Command(
            update={
                "last_error": {
                    "type": "no_schema",
                    "message": "Schema summary not available",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    prompt = PLAN_DASHBOARD_PROMPT.format(
        request=state["request"][:200],
        analysis_intent=goal.get("analysis_intent", "trend"),
        preferred_viz=goal.get("preferred_viz"),
        datetime_cols=summary["datetime_cols"],
        dimension_cols=summary["dimension_cols"],
        metric_cols=summary["metric_cols"],
        chart_count=goal.get("chart_count", 1),
        user_language=goal.get("user_language", "zh"),
    )
    try:
        intents = llm_call_json_list(prompt)
    except ValueError as exc:
        logger.warning("plan_dashboard LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "type": "llm_format_error",
                    "message": f"Failed to plan dashboard: {exc}",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
    preferred_viz = goal.get("preferred_viz")
    if preferred_viz:
        intents = [
            {
                **intent,
                "preferred_viz": intent.get("preferred_viz") or preferred_viz,
            }
            for intent in intents
        ]

    return Command(
        update={
            "chart_intents": intents,
            "current_chart_index": 0,
            # Note: do NOT reset created_charts here — operator.add reducer
            # accumulates across nodes; initial_state in runner.py sets it to []
        },
        goto="single_chart_subgraph",
    )


# ── Node P6: create_dashboard [Code] ───────────────────────────────


def create_dashboard(
    state: DashboardState,
) -> Command[Literal["__end__"]]:
    from superset.ai.tools.create_dashboard import CreateDashboardTool

    charts = state.get("created_charts", [])

    # Precondition 1: created_charts non-empty
    if not charts:
        return Command(
            update={
                "last_error": {
                    "type": "no_charts",
                    "message": "没有成功创建的图表，无法创建仪表板",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Precondition 2: all charts have chart_id
    invalid = [c for c in charts if "chart_id" not in c]
    if invalid:
        return Command(
            update={
                "last_error": {
                    "type": "invalid_charts",
                    "message": f"{len(invalid)} 张图表缺少 chart_id",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Precondition 3: chart count meets minimum threshold
    expected = len(state.get("chart_intents", []))
    if expected > 0 and len(charts) < max(1, expected // 2):
        return Command(
            update={
                "last_error": {
                    "type": "insufficient_charts",
                    "message": f"期望 {expected} 张图表，仅创建了 {len(charts)} 张",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Precondition 4: idempotency check
    request_id = state.get("request_id", "")
    existing = _find_existing_dashboard(request_id)
    if existing:
        return Command(
            update={"created_dashboard": existing},
            goto="__end__",
        )

    # Create dashboard
    goal = state.get("goal", {})
    title = goal.get("target_table", "AI") + " 仪表板"
    chart_ids = [c["chart_id"] for c in charts]

    tool = CreateDashboardTool()
    result_str = tool.run({
        "dashboard_title": title,
        "chart_ids": chart_ids,
        "description": f"由 AI Agent 生成 | request_id={request_id}",
    })

    if result_str.startswith("Error"):
        return Command(
            update={
                "last_error": {
                    "type": "create_dashboard_failed",
                    "message": result_str,
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    return Command(
        update={"created_dashboard": json.loads(result_str)},
        goto="__end__",
    )


def _find_existing_dashboard(request_id: str) -> dict[str, Any] | None:
    """Idempotency: find dashboard created by same request in last 30 min."""
    from superset import db
    from superset.models.dashboard import Dashboard

    if not request_id:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    row = (
        db.session.query(Dashboard)
        .filter(
            Dashboard.description.contains(f"request_id={request_id}"),
            Dashboard.created_on >= cutoff,
        )
        .first()
    )
    if row:
        return {
            "dashboard_id": row.id,
            "dashboard_title": row.dashboard_title,
            "dashboard_url": f"/superset/dashboard/{row.id}/",
            "message": f"Dashboard already exists (id={row.id}), reusing.",
        }
    return None
