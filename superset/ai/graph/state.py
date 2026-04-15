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
"""State definitions for the LangGraph StateGraph agent."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

# ── Sub-types ────────────────────────────────────────────────────────


class SchemaSummary(TypedDict):
    datasource_id: int
    table_name: str
    datetime_cols: list[str]
    dimension_cols: list[str]  # groupable string cols
    metric_cols: list[str]  # numeric cols
    saved_metrics: list[str]  # saved metric names
    saved_metric_expressions: dict[str, str]  # saved metric name -> SQL expression
    main_dttm_col: str | None
    column_descriptions: dict[str, str]  # Phase 12: col_name → description
    column_verbose_names: dict[str, str]  # Phase 12: col_name → verbose_name
    business_metrics: dict[str, Any]  # Phase 13: business KPI → metric def


class ResultSummary(TypedDict):
    row_count: int
    columns: list[dict[str, Any]]
    has_datetime: bool
    datetime_col: str | None
    datetime_cardinality: int
    numeric_cols: list[str]
    string_cols: list[str]
    low_cardinality_cols: list[str]  # distinct < 20
    suitability_flags: dict[str, bool]
    insight: str | None  # Phase 11: LLM-generated one-line data insight


class ChartIntent(TypedDict):
    """Parent graph plan_dashboard output — per-chart plan."""

    chart_index: int
    analysis_intent: str  # trend|comparison|composition|distribution|kpi
    slice_name: str
    sql_hint: str  # optional hint for plan_query
    preferred_viz: str | None
    target_table: str | None  # Phase 18: per-chart target table for multi-dataset


class ChartPlan(TypedDict):
    """Child graph select_chart output — semantic intent."""

    viz_type: str
    slice_name: str
    semantic_params: dict[str, Any]
    rationale: str


# ── Parent State ─────────────────────────────────────────────────────


class DashboardState(TypedDict, total=False):
    # Input
    request: str
    request_id: str
    session_id: str
    user_id: int
    database_id: int
    schema_name: str | None
    agent_mode: str  # "chart" | "dashboard"
    channel_id: str  # Redis stream channel for real-time event publishing
    conversation_history: list[dict[str, Any]]  # prior turns for context

    # parse_request output
    goal: dict[str, Any]

    # search_dataset + select_dataset output
    dataset_candidates: list[dict[str, Any]]
    selected_dataset: dict[str, Any] | None

    # read_schema output
    schema_raw: dict[str, Any] | None
    schema_summary: SchemaSummary | None

    # plan_dashboard output (chart mode: 1 item)
    chart_intents: list[ChartIntent]
    current_chart_index: int

    # create_chart accumulation (subgraph writes via operator.add)
    created_charts: Annotated[list[dict[str, Any]], operator.add]
    child_events_published: bool

    # Phase 18: multi-dataset schema cache (table_name → SchemaSummary)
    schema_cache: dict[str, SchemaSummary]

    # create_dashboard output
    created_dashboard: dict[str, Any] | None

    # Phase 17: clarification state
    clarify_question: str | None
    clarify_type: str | None           # "dataset_selection" | "general"
    clarify_options: list[dict] | None  # [{"label", "value", "description"}]
    answer_prefix: str | None          # 供上下文参考

    # Phase 14: chart modification
    previous_charts: list[dict[str, Any]]       # 上一轮创建的图表（从 tool_summary 提取）
    reference_chart_id: int | None              # 前端指定要修改的图表 ID（可选）
    existing_chart: dict[str, Any] | None       # load_existing_chart 加载的完整图表
    modification: dict[str, Any] | None         # apply_chart_modification 计算的变更集

    # Phase 19: plan analysis confirmation
    execution_mode: str | None            # "plan" | "direct" | None（None 时自动判断）
    analysis_plan: dict[str, Any] | None  # review_analysis 输出的结构化计划

    # Error tracking
    last_error: dict[str, Any] | None


# ── Child State ──────────────────────────────────────────────────────


class SingleChartState(TypedDict, total=False):
    # Injected from parent
    chart_intent: ChartIntent
    schema_summary: SchemaSummary
    database_id: int
    request_id: str
    channel_id: str  # Redis stream channel for retrying events

    # plan_query output
    sql_plan: dict[str, Any] | None

    # validate_sql output
    sql: str | None
    sql_valid: bool

    # execute_query output
    query_result_raw: str | None

    # analyze_result output
    query_result_summary: ResultSummary | None
    suggest_questions: list[str]  # Phase merge-1: follow-up question suggestions
    statistics: dict[str, str]  # Period-over-period stats for KPI cards

    # select_chart output
    chart_plan: ChartPlan | None

    # normalize_chart_params output
    chart_form_data: dict[str, Any] | None

    # create_chart output
    created_chart: dict[str, Any] | None
    suggested_width: int  # Phase 19b: dashboard grid width, passed to parent

    # Error tracking
    last_error: dict[str, Any] | None
    repair_attempts: int
    sql_attempts: int
