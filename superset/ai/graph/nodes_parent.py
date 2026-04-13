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
  P3b clarify_user      [Code]
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
  "target_tables": ["<table1>", "<table2>", null],
  "analysis_intent": "<trend|comparison|composition|distribution|kpi>",
  "preferred_viz": "<viz_type or null>",
  "chart_count": <int — count of distinct charts requested by the user>,
  "time_hint": "<monthly|daily|yearly|null>",
  "user_language": "zh" or "en",
  "multi_dataset": true or false
}}

Rules for chart_count:
- If the user lists multiple charts (e.g. "1.xxx 2.xxx 3.xxx"), count them exactly.
- If the user mentions a dashboard with N chart types, chart_count = N.
- For a single chart, chart_count = 1.

Rules for target_tables and multi_dataset:
- If the user mentions or implies MULTIPLE different tables/datasets, set multi_dataset=true AND fill target_tables with the table names.
- CRITICAL: When multi_dataset=true, target_tables MUST be a non-empty array of table name strings.
- If only one table is mentioned or implied, set multi_dataset=false and target_tables to null.
- Example: "1.消息趋势(messages) 2.用户分布(users)" → multi_dataset=true, target_tables=["messages","users"]
- Extract table names from parentheses, explicit mentions, or context clues.

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
{business_metrics_hint}
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
- When business metrics are available, prefer them for KPI-related charts
- slice_name should be in {user_language}
"""

PLAN_DASHBOARD_PROMPT_V2 = """\
Plan chart intents for a multi-dataset dashboard. Return ONLY valid JSON array.

User request: {request}
Analysis goal: {analysis_intent}
Preferred visualization: {preferred_viz}
Available tables in database: {available_tables}
Requested chart count: {chart_count}

Output (array of {chart_count} items):
[
  {{
    "chart_index": 0,
    "analysis_intent": "<trend|comparison|composition|distribution|kpi>",
    "preferred_viz": "<preferred_viz or null>",
    "slice_name": "<chart title>",
    "target_table": "<best table for this chart>",
    "sql_hint": "<optional hint for SQL generation>"
  }}
]

Rules:
- target_table must be one of the available tables, pick the most suitable one for each chart's analysis
- Different charts CAN use different tables
- If chart_count=1, output exactly 1 item
- Different charts should show different aspects of the data
- slice_name should be in {user_language}
"""


# ── Helpers ─────────────────────────────────────────────────────────


def _count_numbered_items(text: str) -> int:
    """Count numbered list items like '1.xxx 2.xxx' in user request text."""
    import re as _re

    # Match patterns like "1.", "1、", "1)" followed by content
    matches = _re.findall(r"(?:^|[\s，,：:])(\d{1,2})[.、)]\s*\S", text)
    if not matches:
        return 0
    # Verify sequential numbering starting from 1
    nums = sorted(int(m) for m in matches)
    result = nums[-1] if nums == list(range(1, nums[-1] + 1)) else 0
    if result == 0 and text:
        logger.debug(
            "numbered list detection found non-sequential items: %s in %r",
            matches,
            text[:80],
        )
    return result


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
) -> Command[Literal["search_dataset", "plan_dashboard", "__end__"]]:
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
        goal["multi_dataset"] = False

    # Fallback: detect chart_count from numbered lists if LLM returned 1
    if goal.get("chart_count", 1) <= 1 and state.get("agent_mode") == "dashboard":
        detected = _count_numbered_items(state["request"])
        if detected > 1:
            goal["chart_count"] = detected

    # Phase 18: multi-dataset detection
    # Check both LLM output and request text for multiple table references
    if state.get("agent_mode") != "chart":
        target_tables = goal.get("target_tables") or []
        # Filter out nulls
        target_tables = [t for t in target_tables if t]

        # Fallback: extract table names from parentheses in the full request context
        # e.g. "1.每周消息趋势(messages) 2.用户分布(users)" → ["messages", "users"]
        if len(target_tables) < 2:
            import re as _re

            # Combine current request and conversation history for extraction
            combined_text = state["request"]
            for entry in (state.get("conversation_history") or []):
                combined_text += " " + (entry.get("content", "") or "")

            paren_tables = _re.findall(
                r"\(([a-zA-Z_]\w{1,50})\)", combined_text
            )
            if len(paren_tables) >= 2:
                # Validate against actual datasets — filter out false positives
                all_datasets = _get_all_accessible_datasets(state.get("database_id"))
                valid = {d.lower() for d in all_datasets}
                validated = [t for t in paren_tables if t.lower() in valid]
                if len(validated) >= 2:
                    # Deduplicate while preserving order
                    target_tables = list(dict.fromkeys(validated))
                    logger.info(
                        "Extracted target_tables from parentheses (validated): %s",
                        target_tables,
                    )

        if len(target_tables) >= 2:
            goal["target_tables"] = target_tables
            goal["multi_dataset"] = True
            logger.info(
                "Multi-dataset mode: %d tables detected: %s",
                len(target_tables),
                target_tables,
            )
            return Command(
                update={"goal": goal, "schema_cache": {}},
                goto="plan_dashboard",
            )

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
) -> Command[Literal["read_schema", "search_dataset", "clarify_user", "__end__"]]:
    candidates = state.get("dataset_candidates", [])
    target = (state.get("goal", {}) or {}).get("target_table") or ""
    target = target.lower()

    # No candidates → ask user to pick from all available datasets
    if not candidates:
        database_id = state.get("database_id")
        all_datasets = (
            _get_all_accessible_datasets(database_id)
            if database_id is not None
            else []
        )
        if all_datasets:
            return Command(
                update={
                    "clarify_question": "未找到匹配的数据集，当前数据库有以下可用表：",
                    "clarify_type": "dataset_selection",
                    "clarify_options": [
                        {"label": d, "value": d} for d in all_datasets[:10]
                    ],
                    "answer_prefix": (
                        f"{state.get('request', '')}，使用数据集 {{value}}"
                    ),
                },
                goto="clarify_user",
            )
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
    scored: list[tuple[float, Any]] = []
    for c in candidates:
        name = c.get("table_name", c) if isinstance(c, dict) else c
        name_lower = str(name).lower()
        desc = (c.get("description", "") or "").lower() if isinstance(c, dict) else ""
        match_score = float(c.get("match_score", 0)) if isinstance(c, dict) else 0.0

        # Base score from string matching
        if name_lower == target:
            score = 100
        elif name_lower.startswith(target):
            score = 50
        elif target in name_lower:
            score = 20
        else:
            score = 0

        # Phase 12: add fuzzy match_score weight (0-60 range)
        if score == 0 and match_score > 0:
            score = match_score * 60

        # Phase 12: bonus for description keyword match
        if target in desc and score < 100:
            score += 10

        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    # Require a minimum score to avoid selecting an unrelated dataset
    if best_score == 0:
        options = [
            {
                "label": c.get("table_name", str(c)),
                "value": c.get("table_name", str(c)),
                "description": c.get("description", ""),
            }
            for c in candidates[:8]
        ]
        return Command(
            update={
                "clarify_question": "找到多个可能的数据集，请选择：",
                "clarify_type": "dataset_selection",
                "clarify_options": options,
                "answer_prefix": f"{state['request']}，使用数据集 {{value}}",
            },
            goto="clarify_user",
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


def _get_all_accessible_datasets(database_id: int) -> list[str]:
    """Return table names for all datasets in the given database."""
    try:
        from superset import db
        from superset.connectors.sqla.models import SqlaTable

        tables = (
            db.session.query(SqlaTable.table_name)
            .filter(SqlaTable.database_id == database_id)
            .order_by(SqlaTable.table_name)
            .limit(10)
            .all()
        )
        return [t.table_name for t in tables]
    except Exception:
        logger.warning("Failed to list datasets for database %s", database_id)
        return []


# ── Node P3b: clarify_user [Code] ───────────────────────────────────


def clarify_user(
    state: DashboardState,
) -> Command[Literal["__end__"]]:
    """Publish a clarification message and end the graph gracefully.

    Sends a structured ``clarify`` event with options data for frontends
    that support it, plus a ``text_chunk`` fallback for basic rendering.
    """
    from superset.ai.agent.events import AgentEvent
    from superset.ai.streaming.manager import AiStreamManager

    question = state.get("clarify_question", "请补充信息：")
    options = state.get("clarify_options") or []

    # Build natural-language clarification text
    lines = [question]
    for i, opt in enumerate(options, 1):
        label = opt.get("label", "")
        desc = opt.get("description", "")
        if desc:
            lines.append(f"  {i}. {label} ({desc})")
        else:
            lines.append(f"  {i}. {label}")
    lines.append("请告诉我你想用哪个？")
    text = "\n".join(lines)

    channel_id = state.get("channel_id")
    if channel_id:
        stream = AiStreamManager()
        # Structured event for frontends that handle clarify UI
        stream.publish_event(
            channel_id,
            AgentEvent(
                type="clarify",
                data={
                    "question": question,
                    "clarify_type": state.get("clarify_type", "dataset_selection"),
                    "options": options,
                    "context": {
                        "original_request": state.get("request", ""),
                        "answer_prefix": state.get("answer_prefix", ""),
                    },
                },
            ),
        )
        # Text fallback for basic rendering
        stream.publish_event(
            channel_id,
            AgentEvent(type="text_chunk", data={"content": text}),
        )

    return Command(update={"last_error": None}, goto="__end__")


def _build_schema_summary(raw: dict[str, Any]) -> SchemaSummary:
    """Build a SchemaSummary from a raw dataset dict (SearchDatasetsTool result)."""
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
    column_descriptions = {
        c["name"]: c["description"]
        for c in columns
        if c.get("description")
    }
    column_verbose_names = {
        c["name"]: c["verbose_name"]
        for c in columns
        if c.get("verbose_name")
    }

    # Load business metrics
    business_metrics: dict[str, Any] = {}
    try:
        from superset.ai.metric_catalog import find_metrics_for_table

        raw_metrics = find_metrics_for_table(raw["table_name"])
        business_metrics = {
            name: {
                "sql": defn["sql"],
                "description": defn.get("description", ""),
                "aliases": defn.get("aliases", []),
                "unit": defn.get("unit"),
            }
            for name, defn in raw_metrics.items()
        }
    except Exception:
        logger.debug("Failed to load business metrics", exc_info=True)

    return {
        "datasource_id": raw["datasource_id"],
        "table_name": raw["table_name"],
        "datetime_cols": datetime_cols[:5],
        "dimension_cols": dimension_cols[:10],
        "metric_cols": metric_cols[:10],
        "saved_metrics": saved_metrics[:10],
        "saved_metric_expressions": saved_metric_expressions,
        "main_dttm_col": raw.get("main_datetime_column"),
        "column_descriptions": column_descriptions,
        "column_verbose_names": column_verbose_names,
        "business_metrics": business_metrics,
    }


# ── Node P4: read_schema [Code] ────────────────────────────────────


def read_schema(
    state: DashboardState,
) -> Command[Literal["plan_dashboard"]]:
    dataset = state["selected_dataset"]
    raw = dataset  # SearchDatasetsTool already returns complete info

    # Guard: ensure datasource_id exists (defensive against re-search edge cases)
    if not raw.get("datasource_id"):
        return Command(
            update={
                "last_error": {
                    "type": "no_datasource_id",
                    "message": "数据集缺少 datasource_id，无法读取 Schema",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    summary = _build_schema_summary(raw)
    return Command(
        update={"schema_raw": raw, "schema_summary": summary},
        goto="plan_dashboard",
    )


# ── Node P5: plan_dashboard [LLM] ──────────────────────────────────


def plan_dashboard(  # noqa: C901
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph"]]:
    goal = state.get("goal", {})
    summary = state.get("schema_summary")
    is_multi = goal.get("multi_dataset") and not summary

    if is_multi:
        # Phase 18: multi-dataset mode — no schema, use V2 prompt with table list
        database_id = state.get("database_id")
        available_tables = (
            _get_all_accessible_datasets(database_id)
            if database_id is not None
            else []
        )
        if not available_tables:
            return Command(
                update={
                    "last_error": {
                        "type": "no_dataset",
                        "message": "数据库中没有可用的数据集",
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
        prompt = PLAN_DASHBOARD_PROMPT_V2.format(
            request=state["request"][:200],
            analysis_intent=goal.get("analysis_intent", "trend"),
            preferred_viz=goal.get("preferred_viz"),
            available_tables=available_tables,
            chart_count=goal.get("chart_count", 1),
            user_language=goal.get("user_language", "zh"),
        )
    else:
        # Single-dataset mode — use existing prompt with schema columns
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

        # Phase 13: build business metrics hint for dashboard planning
        biz_metrics = summary.get("business_metrics", {})
        if biz_metrics:
            biz_hint_lines = [
                f"  - {name}: {m.get('description', '')}"
                for name, m in list(biz_metrics.items())[:5]
            ]
            business_metrics_hint = (
                "Business metrics (prefer these for KPI charts):\n"
                + "\n".join(biz_hint_lines)
            )
        else:
            business_metrics_hint = ""

        prompt = PLAN_DASHBOARD_PROMPT.format(
            request=state["request"][:200],
            analysis_intent=goal.get("analysis_intent", "trend"),
            preferred_viz=goal.get("preferred_viz"),
            datetime_cols=summary["datetime_cols"],
            dimension_cols=summary["dimension_cols"],
            metric_cols=summary["metric_cols"],
            business_metrics_hint=business_metrics_hint,
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

    # Phase 18: for multi-dataset mode, backfill target_table via keyword matching
    if is_multi:
        target_tables = goal.get("target_tables") or []
        _backfill_target_tables(intents, target_tables)

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
    # Phase 18: multi-dataset mode may not have target_table; derive title from request
    table_name = goal.get("target_table") or ""
    if not table_name:
        target_tables = goal.get("target_tables") or []
        if target_tables:
            table_name = target_tables[0]
    title = (table_name or "AI") + " 仪表板"
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


# ── Phase 18: multi-dataset helpers ────────────────────────────────


def _backfill_target_tables(
    intents: list[dict[str, Any]],
    target_tables: list[str],
) -> None:
    """Assign target_table to intents that lack one via keyword matching.

    Strategy:
      1. For each intent without a target_table, scan its slice_name and
         sql_hint for keywords that match a target table name.
      2. If no keyword match, fall back to round-robin assignment of
         unclaimed tables.
    """
    if not target_tables or not intents:
        return

    # Track which tables have already been claimed (by LLM or keyword match)
    claimed: set[str] = set()
    for intent in intents:
        tt = intent.get("target_table")
        if tt and tt in target_tables:
            claimed.add(tt)

    # Pass 1: keyword matching
    unassigned: list[int] = []
    for i, intent in enumerate(intents):
        if intent.get("target_table"):
            continue
        # Combine text fields for matching
        text = (
            f"{intent.get('slice_name', '')} {intent.get('sql_hint', '')}"
        ).lower()
        matched: str | None = None
        for table in target_tables:
            if table in claimed:
                continue
            # Match table name as substring (e.g. "messages" in "消息趋势")
            # Also match the table name as a whole word
            table_lower = table.lower()
            # Check if the table name (or its stem) appears in the text
            # This handles both "messages" and "message" patterns
            table_stem = table_lower.rstrip("s") if table_lower.endswith("s") else table_lower
            if table_lower in text or table_stem in text:
                matched = table
                break
        if matched:
            intent["target_table"] = matched
            claimed.add(matched)
        else:
            unassigned.append(i)

    # Pass 2: round-robin fallback for remaining unassigned intents
    available = [t for t in target_tables if t not in claimed]
    for idx, intent_idx in enumerate(unassigned):
        if idx < len(available):
            intents[intent_idx]["target_table"] = available[idx]
            claimed.add(available[idx])


def resolve_dataset(
    target_table: str | None,
    database_id: int,
    schema_name: str | None,
    schema_cache: dict[str, SchemaSummary],
) -> SchemaSummary | None:
    """Resolve a dataset by table name, using cache to avoid re-searching."""
    if target_table and target_table in schema_cache:
        return schema_cache[target_table]

    # Fallback: if no target_table, use the first cached entry
    if not target_table and schema_cache:
        return next(iter(schema_cache.values()))

    # Search for the dataset
    from superset.ai.tools.search_datasets import SearchDatasetsTool

    tool = SearchDatasetsTool(database_id=database_id, schema_name=schema_name)
    result_str = tool.run({"table_name": target_table or ""})

    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        logger.warning("resolve_dataset: failed to parse search result")
        return None

    if result.get("status") != "found":
        logger.warning(
            "resolve_dataset: table '%s' not found (status=%s)",
            target_table,
            result.get("status"),
        )
        return None

    summary = _build_schema_summary(result)
    if target_table:
        schema_cache[target_table] = summary
    return summary
