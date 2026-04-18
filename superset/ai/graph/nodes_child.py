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
  C4b generate_questions   [LLM]
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


def _publish_retry(
    state: SingleChartState,
    *,
    node: str,
    reason: str,
    attempt: int,
) -> None:
    """Publish a retrying event to the Redis stream if channel_id is set."""
    channel_id = state.get("channel_id")
    if not channel_id:
        return
    try:
        from superset.ai.agent.events import AgentEvent
        from superset.ai.streaming.manager import AiStreamManager

        AiStreamManager().publish_event(
            channel_id,
            AgentEvent(
                type="retrying",
                data={"node": node, "reason": reason, "attempt": attempt},
            ),
        )
    except Exception as exc:
        logger.debug("Failed to publish retry event: %s", exc)


def _publish_chart_preview(
    state: SingleChartState,
    form_data: dict[str, Any],
    chart_plan: dict[str, Any],
) -> None:
    """Publish a chart_preview event with viz_type, form_data, and parsed query data."""
    channel_id = state.get("channel_id")
    if not channel_id:
        return
    try:
        from superset.ai.agent.events import AgentEvent
        from superset.ai.graph.runner import _parse_text_table_for_event
        from superset.ai.streaming.manager import AiStreamManager

        summary = state.get("query_result_summary") or {}
        insight = summary.get("insight") if isinstance(summary, dict) else None

        schema_summary = state.get("schema_summary") or {}
        preview_data: dict[str, Any] = {
            "viz_type": chart_plan.get("viz_type", "table"),
            "slice_name": chart_plan.get("slice_name", ""),
            "semantic_params": chart_plan.get("semantic_params", {}),
            "form_data": form_data,
            "datasource_id": schema_summary.get("datasource_id", 0),
            "insight": insight,
            "suggest_questions": state.get("suggest_questions") or [],
            "chart_index": (state.get("chart_intent") or {}).get("chart_index", 0),
        }

        # Attach parsed query result (columns + rows) so the frontend
        # can render an inline preview without a separate data_analyzed event.
        query_raw = state.get("query_result_raw", "")
        if query_raw:
            parsed = _parse_text_table_for_event(query_raw)
            if parsed:
                preview_data["columns"] = parsed["columns"]
                preview_data["rows"] = parsed["rows"]
                preview_data["row_count"] = len(parsed["rows"])

        AiStreamManager().publish_event(
            channel_id,
            AgentEvent(type="chart_preview", data=preview_data),
        )
    except Exception as exc:
        logger.debug("_publish_chart_preview failed: %s", exc)


# ── Prompts ─────────────────────────────────────────────────────────

PLAN_QUERY_PROMPT = """\
Generate a SQL query for the given chart goal. Return ONLY valid JSON.

Chart goal: {analysis_intent} — "{slice_name}"
{sql_hint}
Table: {table_name}
Columns:
  time: {datetime_cols}
  dimensions: {dimension_cols}
  metrics: {metric_cols}
  saved_metrics: {saved_metrics}
Column business descriptions (use these to map user intent to column names):
{column_descriptions_block}
Business metric definitions (PREFER THESE when user mentions business KPIs):
{business_metrics_block}
{error_hint}

Output:
{{
  "sql": "<complete SELECT statement>",
  "metric_expr": "<main aggregate expression, e.g. SUM(col)>",
  "dimensions": ["<groupby column names>"]
}}

Rules:
- Write a complete, executable SELECT query in the "sql" field
- Only use column names from the lists above
- Do not use saved metric names directly; convert them to SQL expressions
- When business metrics are defined, prefer their SQL expressions over guessing
- If the chart title mentions a dimension, include it in GROUP BY and dimensions[]
- For trend charts: include the time column in SELECT and GROUP BY;
  use DATE_TRUNC('month', col) or similar for time granularity
- For composition: include 1 low-cardinality dimension in GROUP BY
- Always include ORDER BY and LIMIT (max 500)
- Quote the table name with double quotes ONLY if it contains spaces or special characters
- Do NOT quote regular column names — use them bare (e.g. developer_type, NOT "developer_type")
- metric_expr: the main aggregate expression (e.g. SUM(num), COUNT(*))
- dimensions: list the GROUP BY column names (without aggregate functions)
- Do not include semicolons
"""

SELECT_CHART_PROMPT = """\
Choose the best chart type. Return ONLY valid JSON.

Chart goal: {analysis_intent} — "{slice_name}"
User preferred: {preferred_viz}

Query Plan (MUST map these closely to chart parameters):
  - Planned Metric(s): {planned_metric}
  - Planned Groupby: {planned_dimensions}

Data suitability:
{suitability_flags}
Columns: time={datetime_col}, numeric={numeric_cols}, low-cardinality={low_card_cols}

Chart types reference:
{chart_type_reference}

Output:
{{
  "viz_type": "<chosen>",
  "slice_name": "<chart title>",
  "semantic_params": {{
    "time_field": "<or null>",
    "metric": "<use Planned Metric here>",
    "metrics": ["<use Planned Metric here if plural>"],
    "groupby": <use Planned Groupby array here directly>,
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
) -> Command[Literal["validate_sql", "__end__"]]:
    summary: SchemaSummary = state["schema_summary"]
    intent = state["chart_intent"]
    error_hint = ""
    last_err = state.get("last_error")
    if last_err and last_err.get("node") in ("validate_sql", "execute_query"):
        error_hint = (
            f"\nPrevious attempt failed: {last_err['message']}\nFix the SQL."
        )

    sql_hint = intent.get("sql_hint", "")

    # Phase 12: build column descriptions block for the prompt
    col_desc = summary.get("column_descriptions", {})
    col_verbose = summary.get("column_verbose_names", {})
    # Merge: verbose_name as base, description values take precedence
    all_desc = {**col_verbose, **col_desc}
    col_desc_lines = "\n".join(
        f"  {col}: {desc}" for col, desc in list(all_desc.items())[:15]
    ) or "  (no business descriptions available)"

    # Phase 13: build business metrics block
    biz_metrics = summary.get("business_metrics", {})
    biz_lines = "\n".join(
        f"  {name}: {m.get('description', '')}\n    SQL: {m.get('sql', '')}"
        for name, m in list(biz_metrics.items())[:8]
    ) or "  (no business metrics defined for this table)"

    prompt = PLAN_QUERY_PROMPT.format(
        analysis_intent=intent["analysis_intent"],
        slice_name=intent["slice_name"],
        sql_hint=f"Hint: {sql_hint}" if sql_hint else "",
        table_name=summary["table_name"],
        datetime_cols=summary["datetime_cols"],
        dimension_cols=summary["dimension_cols"],
        metric_cols=summary["metric_cols"],
        saved_metrics=summary["saved_metrics"],
        column_descriptions_block=col_desc_lines,
        business_metrics_block=biz_lines,
        error_hint=error_hint,
    )
    try:
        sql_plan = llm_call_json(prompt)
    except ValueError as exc:
        logger.warning("plan_query LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "node": "plan_query",
                    "type": "llm_format_error",
                    "message": str(exc),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
    return Command(update={"sql_plan": sql_plan}, goto="validate_sql")


# ── Node C2: validate_sql [Code] ───────────────────────────────────


def validate_sql(
    state: SingleChartState,
) -> Command[Literal["execute_query", "plan_query"]]:
    plan = state.get("sql_plan") or {}
    summary: SchemaSummary = state["schema_summary"]
    sql = (plan.get("sql") or "").strip()
    issues: list[str] = []

    # Phase 1: SQL presence check
    if not sql:
        issues.append("LLM did not return a SQL query")
    else:
        # Clean trailing semicolons
        sql = sql.rstrip(";").strip()

        # Phase 1.5: Strip unnecessary double quotes from column names.
        # LLMs sometimes wrap column names in double quotes ("col_name"),
        # which breaks MySQL and confuses validation.  Only strip quotes
        # around known column names — leave table-name quoting intact.
        known_cols = set(
            summary["datetime_cols"]
            + summary["dimension_cols"]
            + summary["metric_cols"]
        )
        for col in known_cols:
            # Use word-boundary regex to avoid substring corruption
            # (e.g. don't break "full_name" when stripping quotes around "name").
            sql = re.sub(rf'"\b{re.escape(col)}\b"', col, sql)

        # Phase 2: Mutation check (same as ExecuteSqlTool)
        try:
            from superset.sql.parse import SQLScript

            script = SQLScript(sql, engine="sqlite")
            if script.has_mutation():
                issues.append(
                    "SQL contains forbidden DDL/DML statements "
                    "(INSERT, UPDATE, DELETE, DROP, etc.)"
                )
        except Exception as exc:
            logger.debug("SQLScript parse skipped in validate_sql: %s", exc)

        # Phase 3: Ensure LIMIT
        sql_upper = sql.upper()
        if "LIMIT" not in sql_upper:
            sql += " LIMIT 200"
        else:
            limit_match = re.search(r"LIMIT\s+(\d+)", sql, re.IGNORECASE)
            if limit_match and int(limit_match.group(1)) > 500:
                sql = re.sub(r"LIMIT\s+\d+", "LIMIT 500", sql, flags=re.IGNORECASE)

        # Phase 4: Column name validation
        issues.extend(_validate_sql_static(sql, known_cols))

    # Retry or fail
    if issues:
        msg = "; ".join(issues)
        if state.get("sql_attempts", 0) >= _MAX_SQL_ATTEMPTS:
            return Command(
                update={
                    "last_error": {
                        "node": "validate_sql",
                        "type": "validation_failed",
                        "message": msg,
                        "recoverable": False,
                    },
                },
                goto="__end__",
            )
        attempts = state.get("sql_attempts", 0) + 1
        _publish_retry(state, node="validate_sql", reason=msg, attempt=attempts)
        return Command(
            update={
                "last_error": {
                    "node": "validate_sql",
                    "type": "validation_failed",
                    "message": msg,
                    "recoverable": True,
                },
                "sql_attempts": attempts,
            },
            goto="plan_query",
        )

    # Defaults for downstream select_chart node
    plan.setdefault("metric_expr", "COUNT(*)")
    plan.setdefault("dimensions", [])

    return Command(
        update={
            "sql": sql,
            "sql_plan": plan,
            "sql_valid": True,
            "last_error": None,
        },
        goto="execute_query",
    )


def _split_top_level(text: str, delimiter: str) -> list[str]:
    """Split *text* by *delimiter*, but only at the top level (not inside parens).

    Handles nested parentheses so that ``COALESCE(SUM(x), 0)`` is kept as a
    single token when splitting by ``,``.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == delimiter and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _validate_sql_static(sql: str, known_cols: set[str]) -> list[str]:
    """Static checks: no DDL/DML, LIMIT exists, column names are valid."""
    issues: list[str] = []
    sql_upper = sql.upper().strip()

    for forbidden in (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    ):
        if sql_upper.startswith(forbidden):
            issues.append(f"Forbidden statement type: {forbidden}")

    if "LIMIT" not in sql_upper:
        issues.append("Missing LIMIT clause")

    # Extract column references from SELECT/GROUP BY/ORDER BY clauses
    # and verify they exist in known_cols (skip aggregate expressions and aliases)
    select_match = re.search(
        r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE | re.DOTALL,
    )
    if select_match and known_cols:
        select_part = select_match.group(1)
        # Split by commas that are NOT inside parentheses
        select_tokens = _split_top_level(select_part, ",")
        for part in select_tokens:
            part = part.strip()
            # Strip trailing AS alias
            part = re.split(r"\s+AS\s+", part, flags=re.IGNORECASE)[0].strip()
            # Skip aggregate expressions like SUM(col), COUNT(*)
            if "(" in part:
                continue
            # Skip wildcard
            if part == "*":
                continue
            # part is now a bare column name — validate it
            if part and part.lower() not in {c.lower() for c in known_cols}:
                issues.append(f"Unknown column '{part}' in SELECT")

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
        attempts = state.get("sql_attempts", 0) + 1
        _publish_retry(
            state,
            node="execute_query",
            reason=result_str[:200],
            attempt=attempts,
        )
        return Command(
            update={
                "last_error": {
                    "node": "execute_query",
                    "type": "sql_execution_error",
                    "message": result_str,
                    "recoverable": True,
                },
                "sql_attempts": attempts,
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
) -> Command[Literal["generate_questions"]]:
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
        "insight": None,
    }

    # Phase 11: generate one-line insight via LLM (best-effort, non-blocking)
    statistics: dict[str, str] = {}
    if row_count > 0 and numeric_cols:
        try:
            from superset.ai.graph.llm_helpers import _get_llm_response

            sample_rows = rows[:3] if rows else []
            is_kpi = row_count == 1 and len(numeric_cols) == 1

            if is_kpi:
                # For KPI results, ask LLM for insight + comparison statistics
                insight_prompt = (
                    f"这是查询结果（单行KPI数据）:\n"
                    f"  数据行: {sample_rows}\n"
                    f"  指标列: {numeric_cols}\n"
                    f"  维度列: {string_cols}\n"
                    f"  时间列: {datetime_col}\n\n"
                    f"请输出合法JSON，格式如下：\n"
                    f'{{"insight": "一句话洞察(30字内)", '
                    f'"statistics": {{"环比": "+X.X%", "同比": "+X.X%"}}}}\n\n'
                    f"注意：\n"
                    f"- insight 是一句话关键发现\n"
                    f"- statistics 是推测性的环比/同比变化，如果没有时间列则设为空对象\n"
                    f"- 如果数据不足以判断，statistics 置为空对象 {{}}\n"
                    f"输出 ONLY JSON，无其他内容。"
                )
            else:
                insight_prompt = (
                    f"Based on these data characteristics, write ONE sentence "
                    f"(max 30 chars in Chinese) describing the key finding:\n"
                    f"  row_count: {row_count}\n"
                    f"  numeric_cols: {numeric_cols[:3]}\n"
                    f"  datetime_col: {datetime_col}\n"
                    f"  low_card_cols: {low_card_cols[:3]}\n"
                    f"  sample (first 3 rows): {sample_rows}\n\n"
                    f"Output ONLY the insight sentence, nothing else."
                )

            raw_response = _get_llm_response(insight_prompt).strip()

            if is_kpi:
                try:
                    from superset.utils import json as superset_json

                    parsed = superset_json.loads(raw_response)
                    if isinstance(parsed, dict):
                        if parsed.get("insight"):
                            summary["insight"] = str(parsed["insight"])[:200]
                        if isinstance(parsed.get("statistics"), dict):
                            statistics = parsed["statistics"]
                except (ValueError, KeyError):
                    # Fallback: treat whole response as insight text
                    if raw_response:
                        summary["insight"] = raw_response[:200]
            else:
                if raw_response:
                    summary["insight"] = raw_response[:200]
        except Exception as exc:
            logger.warning("analyze_result insight generation failed: %s", exc)

    # Generate suggested follow-up questions for the frontend
    suggest_questions = _generate_suggest_questions(
        state, string_cols, numeric_cols, datetime_col,
    )

    return Command(
        update={
            "query_result_summary": summary,
            # Re-emit query_result_raw so runner._emit_node_events can
            # access it from this node's Command.update dict (runner reads
            # node_output, not state, for event data).
            "query_result_raw": state.get("query_result_raw", ""),
            "suggest_questions": suggest_questions,
            "statistics": statistics,
        },
        goto="generate_questions",
    )


# ── Node C4b: generate_questions [LLM] ──────────────────────────────


def generate_questions(
    state: SingleChartState,
) -> Command[Literal["select_chart"]]:
    from superset.ai.graph.llm_helpers import _get_llm_response

    summary = state.get("query_result_summary", {})
    
    # We use the previous node's fallback output as a default
    suggest_questions = state.get("suggest_questions") or []

    prompt = (
        f"这是数据查询的结果分析片段:\n"
        f"  行数: {summary.get('row_count')}\n"
        f"  指标列: {summary.get('numeric_cols')}\n"
        f"  维度列: {summary.get('string_cols')}\n"
        f"  时间列: {summary.get('datetime_col')}\n"
        f"  一句话洞察: {summary.get('insight')}\n"
        f"请根据上述数据特征，推理出 3 条用户可能最想进行下钻排查、或多维对比分析的后续跟进问题（用中文）。\n"
        f"请输出合法JSON，格式如下：\n"
        f'{{"suggest_questions": ["追问1", "追问2", "追问3"]}}\n\n'
        f"输出 ONLY JSON，无其他内容。"
    )

    try:
        raw_response = _get_llm_response(prompt).strip()
        from superset.utils import json as superset_json
        parsed = superset_json.loads(raw_response)
        if isinstance(parsed, dict) and isinstance(parsed.get("suggest_questions"), list):
            suggest_questions = [str(q) for q in parsed["suggest_questions"]][:3]
    except Exception as exc:
        logger.warning("generate_questions LLM generation failed: %s", exc)

    return Command(
        update={"suggest_questions": suggest_questions},
        goto="select_chart",
    )


def _generate_suggest_questions(
    state: SingleChartState,
    string_cols: list[str],
    numeric_cols: list[str],
    datetime_col: str | None,
) -> list[str]:
    """Generate 2-3 follow-up question suggestions based on the query result."""
    questions: list[str] = []

    if string_cols:
        questions.append(f"按 {string_cols[0]} 拆分分析")
    if datetime_col:
        questions.append("同比上周如何")
    if numeric_cols:
        questions.append("哪个维度贡献最大")

    # Fallback when no specific dimensions detected
    if not questions:
        questions.append("查看趋势变化")
        questions.append("导出详细数据")

    return questions[:3]


# ── Node C5: select_chart [LLM] ────────────────────────────────────


def select_chart(
    state: SingleChartState,
) -> Command[Literal["normalize_chart_params", "__end__"]]:
    intent = state["chart_intent"]
    result_summary = state["query_result_summary"]
    flags = result_summary["suitability_flags"]

    flag_str = "\n".join(
        f"  {k}=true" for k, v in flags.items() if v
    ) or "  (no strong signal, use table)"

    from superset.ai.chart_types.registry import get_chart_registry

    # Phase 14: Inject sql_plan to prevent dimension/metric loss
    sql_plan = state.get("sql_plan", {})
    planned_metric = sql_plan.get("metric_expr", "SUM(num)")
    if isinstance(planned_metric, list):
        planned_metric = ", ".join(planned_metric)
    planned_dimensions = json.dumps(sql_plan.get("dimensions", []))

    chart_ref = get_chart_registry().format_for_prompt()
    prompt = SELECT_CHART_PROMPT.format(
        analysis_intent=intent["analysis_intent"],
        slice_name=intent["slice_name"],
        preferred_viz=intent.get("preferred_viz", "auto"),
        planned_metric=planned_metric,
        planned_dimensions=planned_dimensions,
        suitability_flags=flag_str,
        datetime_col=result_summary.get("datetime_col"),
        numeric_cols=result_summary["numeric_cols"][:4],
        low_card_cols=result_summary["low_cardinality_cols"][:4],
        chart_type_reference=chart_ref,
    )
    try:
        chart_plan = llm_call_json(prompt)
    except ValueError as exc:
        logger.warning("select_chart LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "node": "select_chart",
                    "type": "llm_format_error",
                    "message": str(exc),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
    preferred_viz = intent.get("preferred_viz")
    # Phase 19b: determine suggested_width from chart type registry
    from superset.ai.graph.nodes_parent import _normalize_preferred_viz

    # Normalize preferred_viz before override to avoid short aliases (e.g. "bar")
    if preferred_viz:
        chart_plan["viz_type"] = _normalize_preferred_viz(preferred_viz) or preferred_viz
    viz_type = _normalize_preferred_viz(chart_plan.get("viz_type")) or "table"
    chart_plan["viz_type"] = viz_type
    desc = get_chart_registry().get(viz_type)
    suggested_width = desc.default_width if desc else 4
    return Command(
        update={"chart_plan": chart_plan, "suggested_width": suggested_width},
        goto="normalize_chart_params",
    )


# ── Node C6: normalize_chart_params [Code] ─────────────────────────


def normalize_chart_params(
    state: SingleChartState,
) -> Command[Literal["create_chart", "repair_chart_params", "__end__"]]:
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
        # Publish chart_preview event for inline rendering (no raw data)
        _publish_chart_preview(state, form_data=form_data, chart_plan=chart_plan)

        # Chart mode: skip create_chart — user saves via frontend preview
        # Dashboard mode: still run create_chart (dashboard needs chart IDs)
        skip_create = state.get("skip_create_chart", False)
        return Command(
            update={"chart_form_data": form_data, "last_error": None},
            goto="__end__" if skip_create else "create_chart",
        )
    except ValueError as exc:
        logger.warning(
            "normalize_chart_params failed (attempt %d): %s | chart_plan=%s",
            state.get("repair_attempts", 0) + 1,
            exc,
            json.dumps(chart_plan, ensure_ascii=False)[:500],
        )
        repair_count = state.get("repair_attempts", 0) + 1
        _publish_retry(
            state,
            node="normalize_chart_params",
            reason=str(exc),
            attempt=repair_count,
        )
        return Command(
            update={
                "last_error": {
                    "node": "normalize_chart_params",
                    "type": "compile_error",
                    "message": str(exc),
                    "recoverable": True,
                },
                "repair_attempts": repair_count,
            },
            goto="repair_chart_params",
        )


# ── Node C7: repair_chart_params [LLM] ─────────────────────────────


def repair_chart_params(
    state: SingleChartState,
) -> Command[Literal["normalize_chart_params", "__end__"]]:
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
    try:
        fixed = llm_call_json(prompt)
    except ValueError as exc:
        logger.warning("repair_chart_params LLM error: %s", exc)
        return Command(
            update={
                "last_error": {
                    "node": "repair_chart_params",
                    "type": "llm_format_error",
                    "message": str(exc),
                    "recoverable": False,
                },
            },
            goto="__end__",
        )
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
    suggested_width = state.get("suggested_width", 4)
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
                "suggested_width": suggested_width,
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
        update={
            "created_chart": json.loads(result_str),
            "suggested_width": suggested_width,
        },
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
