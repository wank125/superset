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
  P0  classify_intent          [Code + optional LLM]
  P0b load_existing_chart      [Code]
  P0c apply_chart_modification [LLM]
  P0d update_chart             [Code]
  P1  parse_request            [LLM]
  P2  search_dataset           [Code]
  P3  select_dataset           [Code]
  P3b clarify_user             [Code]
  P4  read_schema              [Code]
  P5  plan_dashboard           [LLM]
  P5b review_analysis          [Code]
  P6  create_dashboard         [Code]
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


# ── Phase 14: chart modification prompts ──────────────────────────


CLASSIFY_INTENT_PROMPT = """\
Classify this request as 'new' or 'modify'.

'modify' examples:
  - "改成折线图" / "change to line chart"
  - "加一个过滤" / "add filter"
  - "把第一个图改一下" / "update the first chart"
  - "那个饼图换成柱状图"
  - "颜色换成红色" / "change color to red"

'new' examples:
  - "帮我做一个销售趋势图"
  - "创建 birth_names 的分析"
  - "用 messages 表创建仪表板"

Respond ONLY: {{"intent": "new"}} or {{"intent": "modify"}}

Request: {request}
"""

MODIFY_CHART_PROMPT = """\
The user wants to modify an existing chart. Return ONLY valid JSON.

Existing chart:
  viz_type: {viz_type}
  slice_name: {slice_name}
  current params: {form_data_summary}

Available viz types: echarts_timeseries_bar, echarts_timeseries_line, pie, table, big_number_total

User request: {request}

Return the modifications to apply:
{{
  "viz_type": "<new viz_type or same>",
  "slice_name": "<new name or same>",
  "param_changes": {{
    "<key>": "<value>"
  }}
}}

Rules:
- Only include keys that need to change in param_changes
- If viz_type changes, update it at the top level
- Keep slice_name the same unless user explicitly asks to rename
- For filter changes, use adhoc_filters array
- For metric changes, use metrics (list) or metric (single)
"""


# ── Node P0: classify_intent [Code + optional LLM] ────────────────


def classify_intent(
    state: DashboardState,
) -> Command[Literal["parse_request", "load_existing_chart"]]:
    """Classify user intent as 'new' or 'modify'.

    Fast path: if no previous charts exist or no modify keywords in request,
    route directly to parse_request (zero LLM overhead).
    Slow path: LLM confirms when modify keywords are present.
    """
    has_previous = bool(state.get("previous_charts"))
    request_text = state.get("request", "").lower()

    # Fast path: no history or no modify keywords → new
    modify_keywords = [
        "改", "换", "修改", "更新", "变成", "换成", "调",
        "change", "update", "modify", "switch", "replace", "turn into",
    ]
    if not has_previous or not any(kw in request_text for kw in modify_keywords):
        return Command(goto="parse_request")

    # Slow path: LLM confirmation
    try:
        result = llm_call_json(
            CLASSIFY_INTENT_PROMPT.format(request=state["request"][:300]),
        )
        intent = result.get("intent", "new")
    except Exception:
        intent = "new"  # fallback to new on LLM failure

    if intent == "modify":
        logger.info("classify_intent: modify path for request=%r", state["request"][:80])
        return Command(goto="load_existing_chart")
    return Command(goto="parse_request")


# ── Node P0b: load_existing_chart [Code] ──────────────────────────


def load_existing_chart(
    state: DashboardState,
) -> Command[Literal["apply_chart_modification", "parse_request"]]:
    """Load the most recent chart from Superset DB for modification."""
    from superset import db
    from superset.models.slice import Slice

    previous = state.get("previous_charts", [])
    if not previous:
        return Command(goto="parse_request")

    # Determine target: reference_chart_id or most recent
    ref_id = state.get("reference_chart_id")
    target = (
        next((c for c in previous if c.get("chart_id") == ref_id), previous[-1])
        if ref_id
        else previous[-1]
    )

    chart_id = target.get("chart_id")
    if not chart_id:
        logger.warning("load_existing_chart: no chart_id in target %s", target)
        return Command(goto="parse_request")

    slice_obj = db.session.get(Slice, chart_id)
    if not slice_obj:
        logger.warning("load_existing_chart: Slice #%s not found", chart_id)
        return Command(goto="parse_request")

    # Permission check: verify current user can write charts and access datasource
    try:
        from superset.extensions import security_manager

        if not security_manager.can_access("can_write", "Chart"):
            logger.warning("load_existing_chart: user lacks Chart write permission")
            return Command(goto="parse_request")
    except Exception:
        logger.warning("load_existing_chart: permission check failed")
        return Command(goto="parse_request")

    return Command(
        update={
            "existing_chart": {
                "chart_id": slice_obj.id,
                "slice_name": slice_obj.slice_name,
                "viz_type": slice_obj.viz_type,
                "form_data": json.loads(slice_obj.params or "{}"),
                "datasource_id": slice_obj.datasource_id,
            },
        },
        goto="apply_chart_modification",
    )


# ── Node P0c: apply_chart_modification [LLM] ──────────────────────


def apply_chart_modification(
    state: DashboardState,
) -> Command[Literal["update_chart", "__end__"]]:
    """Use LLM to compute changes for the existing chart."""
    existing = state.get("existing_chart", {})
    if not existing:
        return Command(
            update={
                "last_error": {
                    "type": "no_existing_chart",
                    "message": "未加载到已有图表",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    form_data = existing.get("form_data", {})
    # Build a concise summary for the prompt (avoid token bloat)
    form_summary_keys = {
        "viz_type", "metrics", "metric", "groupby", "x_axis",
        "granularity_sqla", "time_range", "adhoc_filters", "columns",
        "row_limit",
    }
    form_summary = {k: form_data[k] for k in form_summary_keys if k in form_data}

    prompt = MODIFY_CHART_PROMPT.format(
        viz_type=existing.get("viz_type", ""),
        slice_name=existing.get("slice_name", ""),
        form_data_summary=json.dumps(form_summary)[:500],
        request=state["request"][:300],
    )

    try:
        changes = llm_call_json(prompt)
    except ValueError as exc:
        logger.warning("apply_chart_modification LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "type": "modify_parse_error",
                    "message": f"修改方案解析失败: {exc}",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Merge changes into form_data
    new_form_data = {**form_data}
    param_changes = changes.get("param_changes", {})
    new_form_data.update(param_changes)

    new_viz_type = changes.get("viz_type") or existing.get("viz_type", "")
    new_form_data["viz_type"] = new_viz_type

    new_slice_name = changes.get("slice_name") or existing.get("slice_name", "")

    return Command(
        update={
            "modification": {
                "chart_id": existing["chart_id"],
                "new_viz_type": new_viz_type,
                "new_slice_name": new_slice_name,
                "new_form_data": new_form_data,
            },
        },
        goto="update_chart",
    )


# ── Node P0d: update_chart [Code] ─────────────────────────────────


def update_chart(
    state: DashboardState,
) -> Command[Literal["__end__"]]:
    """Apply modifications to existing chart in Superset DB."""
    from superset import db
    from superset.models.slice import Slice

    mod = state.get("modification", {})
    chart_id = mod.get("chart_id")

    if not chart_id:
        return Command(
            update={
                "last_error": {
                    "type": "no_chart_id",
                    "message": "缺少 chart_id，无法更新图表",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    slice_obj = db.session.get(Slice, chart_id)
    if not slice_obj:
        return Command(
            update={
                "last_error": {
                    "type": "chart_not_found",
                    "message": f"图表 #{chart_id} 不存在",
                    "recoverable": False,
                },
            },
            goto="__end__",
        )

    # Permission check: verify write access
    try:
        from superset.extensions import security_manager

        if not security_manager.can_access("can_write", "Chart"):
            return Command(
                update={
                    "last_error": {
                        "type": "permission_denied",
                        "message": "没有图表修改权限",
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
    except Exception:
        logger.warning("update_chart: permission check failed")

    slice_obj.viz_type = mod.get("new_viz_type", slice_obj.viz_type)
    slice_obj.slice_name = mod.get("new_slice_name", slice_obj.slice_name)
    slice_obj.params = json.dumps(mod.get("new_form_data", {}))
    db.session.commit()

    logger.info("update_chart: updated chart #%d", chart_id)
    # Note: uses "created_chart" key (same as create_chart node) so that
    # tasks.py persists it via add_tool_summary("create_chart", ...) for
    # next-turn context. The "action": "updated" field distinguishes this
    # from a newly created chart. runner.py maps this node to the
    # "chart_updated" event type instead of "chart_created".
    return Command(
        update={
            "created_chart": {
                "chart_id": slice_obj.id,
                "slice_name": slice_obj.slice_name,
                "viz_type": slice_obj.viz_type,
                "explore_url": f"/explore/?slice_id={slice_obj.id}",
                "message": f"已更新图表 #{slice_obj.id}",
                "action": "updated",
            },
        },
        goto="__end__",
    )


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
    # Phase 19: propagate match score so review_analysis can use it
    base_goal = {**state.get("goal", {}), "dataset_match_score": best_score}
    if (
        "datasource_id" not in selected
        and selected.get("table_name")
    ):
        goal = {**base_goal, "target_table": selected["table_name"]}
        return Command(
            update={"goal": goal},
            goto="search_dataset",
        )
    return Command(
        update={"selected_dataset": selected, "goal": base_goal},
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
) -> Command[Literal["review_analysis"]]:
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
        goto="review_analysis",
    )


# ── Phase 19: plan analysis confirmation helpers ───────────────────


def _compute_confidence(state: DashboardState) -> float:
    """Compute confidence score from risk signals. Returns 0.0-1.0."""
    risk_score = 0.0

    goal = state.get("goal", {})

    # Signal 1: dataset selection uncertainty
    if goal.get("dataset_match_score", 100) < 50:
        risk_score += 30

    # Signal 2: multi-topic (≥3 different analysis_intent values)
    intents = state.get("chart_intents", [])
    unique_intents = len({i.get("analysis_intent") for i in intents if i.get("analysis_intent")})
    if unique_intents >= 3:
        risk_score += 20

    # Signal 3: dashboard mode + 3+ charts
    if state.get("agent_mode") == "dashboard" and len(intents) >= 3:
        risk_score += 20

    # Signal 4: derived/ratio metrics present
    summary = state.get("schema_summary") or {}
    biz_metrics = summary.get("business_metrics", {})
    if biz_metrics:
        risk_score += 15

    # Signal 5: no time column
    if not summary.get("datetime_cols"):
        risk_score += 10

    # Signal 6: multi-dataset mode
    if goal.get("multi_dataset"):
        risk_score += 10

    # Map risk_score (0-100+) to confidence (1.0-0.0)
    confidence = max(0.0, 1.0 - risk_score / 100.0)
    return confidence


def _get_dataset_reason(goal: dict[str, Any], summary: dict[str, Any]) -> str:
    """Derive a short reason string for why this dataset was selected."""
    target = (goal.get("target_table") or "").lower()
    table_name = (summary.get("table_name") or "").lower()
    if target and target == table_name:
        return "精确匹配用户指定的表名"
    if target and target in table_name:
        return "部分匹配用户指定的表名"
    if target:
        return "根据关键词匹配选择的数据集"
    return "自动选择的数据集"


def _describe_time_range(summary: dict[str, Any]) -> str:
    """Describe time range availability from schema summary."""
    dt_cols = summary.get("datetime_cols", [])
    if not dt_cols:
        return "未找到时间列"
    main = summary.get("main_dttm_col")
    if main:
        return f"可用时间列: {main}（未指定范围，默认全量数据）"
    return f"可用时间列: {', '.join(dt_cols[:3])}（未指定范围）"


def _extract_assumptions(
    goal: dict[str, Any],
    summary: dict[str, Any],
    intents: list[dict[str, Any]],
) -> list[str]:
    """Extract key assumptions and risks for the plan."""
    assumptions: list[str] = []

    # Assumption about metrics
    metric_cols = summary.get("metric_cols", [])
    if metric_cols:
        assumptions.append(f"假设 {metric_cols[0]} 为主要度量指标")

    # Assumption about time
    if not goal.get("time_hint") and summary.get("datetime_cols"):
        assumptions.append("未指定时间范围，默认使用全量数据")

    # Risk: low confidence
    match_score = goal.get("dataset_match_score", 100)
    if match_score < 50:
        assumptions.append(f"数据集匹配置信度较低（score={match_score}），请确认表选择是否正确")

    # Risk: multi-dataset
    if goal.get("multi_dataset"):
        target_tables = goal.get("target_tables") or []
        if target_tables:
            assumptions.append(f"多数据集模式，涉及表: {', '.join(target_tables[:5])}")

    return assumptions[:5]  # cap at 5


def _build_analysis_plan(state: DashboardState, confidence: float) -> dict[str, Any]:
    """Build a structured analysis plan from current state (no LLM)."""
    goal = state.get("goal", {})
    summary = state.get("schema_summary") or {}
    intents = state.get("chart_intents", [])

    return {
        "dataset": summary.get("table_name") or goal.get("target_table", ""),
        "dataset_reason": _get_dataset_reason(goal, summary),
        "metrics_dimensions": {
            "metrics": summary.get("metric_cols", [])[:5],
            "dimensions": summary.get("dimension_cols", [])[:5],
        },
        "time_range": _describe_time_range(summary),
        "charts": [
            {
                "index": i.get("chart_index", idx),
                "title": i.get("slice_name", ""),
                "intent": i.get("analysis_intent", ""),
                "viz": i.get("preferred_viz", ""),
                "target_table": i.get("target_table"),
            }
            for idx, i in enumerate(intents)
        ],
        "assumptions_risks": _extract_assumptions(goal, summary, intents),
        "confidence": round(confidence, 2),
    }


def _format_plan_text(plan: dict[str, Any]) -> str:
    """Format the analysis plan as readable text for text_chunk fallback."""
    lines: list[str] = ["📋 分析计划\n"]

    # Dataset
    ds = plan.get("dataset", "")
    reason = plan.get("dataset_reason", "")
    if ds:
        lines.append(f"数据集：{ds}（{reason}）")

    # Metrics & Dimensions
    md = plan.get("metrics_dimensions", {})
    metrics = md.get("metrics", [])
    dims = md.get("dimensions", [])
    if metrics or dims:
        parts = []
        if metrics:
            parts.append(f"指标：{', '.join(metrics[:3])}")
        if dims:
            parts.append(f"维度：{', '.join(dims[:3])}")
        lines.append(" | ".join(parts))

    # Time range
    time_range = plan.get("time_range", "")
    if time_range:
        lines.append(f"时间：{time_range}")

    # Charts
    charts = plan.get("charts", [])
    if charts:
        lines.append(f"图表（{len(charts)} 张）：")
        for chart in charts:
            idx = chart.get("index", 0) + 1
            title = chart.get("title", "")
            intent = chart.get("intent", "")
            viz = chart.get("viz", "")
            parts = [f"  {idx}. {title}"]
            if intent:
                parts.append(intent)
            if viz:
                parts.append(viz)
            lines.append(" — ".join(parts))

    # Assumptions
    assumptions = plan.get("assumptions_risks", [])
    if assumptions:
        lines.append("")
        for a in assumptions:
            lines.append(f"⚠ 假设：{a}")

    # Confidence & action
    confidence = plan.get("confidence", 1.0)
    lines.append(f"\n置信度：{confidence:.0%}")
    lines.append('💡 回复"确认执行"继续，或告诉我需要调整的地方')

    return "\n".join(lines)


def _publish_plan_event(state: DashboardState, plan: dict[str, Any]) -> None:
    """Publish analysis_plan event + text_chunk fallback to the stream."""
    from superset.ai.agent.events import AgentEvent
    from superset.ai.streaming.manager import AiStreamManager

    channel_id = state.get("channel_id")
    if not channel_id:
        return

    stream = AiStreamManager()

    # Structured event (for future dedicated UI)
    stream.publish_event(
        channel_id,
        AgentEvent(type="analysis_plan", data=plan),
    )

    # Text fallback (for current frontend rendering)
    text = _format_plan_text(plan)
    stream.publish_event(
        channel_id,
        AgentEvent(type="text_chunk", data={"content": text}),
    )


# ── Node P5b: review_analysis [Code] ───────────────────────────────


def review_analysis(
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph", "__end__"]]:
    """Review analysis plan — decide direct execution or plan confirmation.

    Phase 19: placed after plan_dashboard, before single_chart_subgraph.
    - If execution_mode is already "direct" (second turn after confirmation),
      skip directly.
    - Compute confidence from risk signals (pure code, no LLM).
    - Low confidence → publish plan and halt (plan mode).
    - High confidence → continue to execution (direct mode).
    """
    # 1. Second turn after user confirmed: skip review
    if state.get("execution_mode") == "direct":
        return Command(goto="single_chart_subgraph")

    # 2. Compute confidence (pure code, zero LLM overhead)
    confidence = _compute_confidence(state)

    # 3. Determine mode: explicit parameter or auto-decide
    mode = state.get("execution_mode")
    if not mode:
        mode = "plan" if confidence < 0.7 else "direct"

    # 4. Build structured plan from current state
    plan = _build_analysis_plan(state, confidence)

    if mode == "plan":
        # Publish plan event, terminate graph and wait for user confirmation
        _publish_plan_event(state, plan)
        return Command(
            update={"analysis_plan": plan, "execution_mode": "plan"},
            goto="__end__",
        )

    # Direct mode: continue execution
    return Command(
        update={"analysis_plan": plan, "execution_mode": "direct"},
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
