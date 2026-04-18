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
"""Graph runner — executes the StateGraph with node-level real-time event streaming."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from superset.ai.agent.events import AgentEvent
from superset.ai.errors import format_user_facing_error

logger = logging.getLogger(__name__)

# Node → (start message, done message)
_NODE_PROGRESS: dict[str, tuple[str, str | None]] = {
    "select_database": ("选择数据库...", "数据库已确定"),
    "classify_intent": ("判断意图...", "意图识别完成"),
    "load_existing_chart": ("加载图表...", "图表加载完成"),
    "apply_chart_modification": ("计算修改...", "修改方案已生成"),
    "update_chart": ("更新图表...", None),
    "parse_request": ("理解请求...", "请求解析完成"),
    "search_dataset": ("搜索数据集...", "数据集搜索完成"),
    "select_dataset": ("选择数据集...", "数据集已确定"),
    "read_schema": ("读取数据结构...", "Schema 读取完成"),
    "plan_dashboard": ("规划图表...", "图表规划完成"),
    "review_analysis": ("审核分析计划...", "计划审核完成"),
    "plan_query": ("生成查询计划...", "SQL 计划生成完成"),
    "validate_sql": ("校验 SQL...", "SQL 校验通过"),
    "execute_query": ("执行查询...", "查询执行完成"),
    "analyze_result": ("分析数据形态...", "数据分析完成"),
    "select_chart": ("选择图表类型...", "图表类型已选定"),
    "normalize_chart_params": ("编译图表参数...", "参数编译完成"),
    "repair_chart_params": ("修复参数错误...", "参数已修复"),
    "create_chart": ("创建图表...", None),
    "create_dashboard": ("创建仪表板...", None),
    "after_subgraph": (None, None),  # routing only, no visible output
    "clarify_user": ("等待用户选择...", None),
}


def _parse_text_table_for_event(
    text: str,
) -> dict[str, Any] | None:
    """Parse execute_sql text output into structured columns + rows for frontend charts."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 3:
        return None

    # Find separator line (dashes + pipes)
    sep_idx = next(
        (i for i, l in enumerate(lines) if set(l) <= {"-", "|", "+", " "}),
        -1,
    )
    if sep_idx < 1:
        return None

    headers = [h.strip() for h in lines[sep_idx - 1].split("|") if h.strip()]
    if not headers:
        return None

    rows: list[dict[str, Any]] = []
    for line in lines[sep_idx + 1 :]:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not cells:
            continue
        row: dict[str, Any] = {}
        for i, h in enumerate(headers):
            val = cells[i] if i < len(cells) else ""
            num = None
            try:
                num = int(val)
            except ValueError:
                try:
                    num = float(val)
                except ValueError:
                    pass
            row[h] = num if num is not None else val
        rows.append(row)

    if not rows:
        return None

    # Infer column types
    columns: list[dict[str, str]] = []

    def _check_is_num(val: str) -> bool:
        if not val:
            return True
        try:
            float(val)
            return True
        except ValueError:
            return False

    for h in headers:
        samples = [str(r.get(h, "")) for r in rows[:5]]
        is_num = all(_check_is_num(s) for s in samples)
        # DATETIME detection: use column name heuristic first,
        # then conservative value pattern (must look like YYYY-MM or YYYY/MM)
        is_dttm_by_name = any(
            kw in h.lower()
            for kw in ("date", "time", "dt", "day", "month", "year", "ds")
        )
        is_dttm_by_val = (
            all(
                re.match(r"\d{4}[-/]\d{2}", s)
                for s in samples
                if s
            )
            if samples else False
        )
        is_dttm = is_dttm_by_name or is_dttm_by_val
        col_type = (
            "DATETIME" if is_dttm else "FLOAT" if is_num else "STRING"
        )
        columns.append({"name": h, "type": col_type, "is_dttm": is_dttm})

    return {"columns": columns, "rows": rows}


def run_graph(  # noqa: C901
    *,
    agent_mode: str,
    user_id: int,
    session_id: str,
    database_id: int | None = None,
    schema_name: str | None,
    message: str,
    channel_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    previous_charts: list[dict[str, Any]] | None = None,
) -> Iterator[AgentEvent]:
    """Build and execute the StateGraph, yielding real-time AgentEvents."""
    from superset.ai.graph.builder import build_chart_graph, build_dashboard_graph
    from superset.ai.graph.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = (
        build_dashboard_graph(checkpointer=checkpointer)
        if agent_mode == "dashboard"
        else build_chart_graph(checkpointer=checkpointer)
    )
    request_id = str(uuid.uuid4())

    # Detect if the user is confirming a previously-blocked request.
    # Two scenarios:
    # 1. Phase 19: analysis_plan was published in a prior turn — detect it
    #    and set execution_mode="direct" to skip re-planning.
    # 2. tasks.py confirmation gate: Turn 1 was blocked before graph ran,
    #    so no analysis_plan exists.  The original request was stored as
    #    a tool_summary("original_request") — look it up directly.
    _execution_mode: str | None = None
    _effective_request = message
    _history = conversation_history or []
    _found_plan = False
    logger.info(
        "plan_detection request=%r history_entries=%d",
        message[:80], len(_history),
    )

    # Helper: find the stored original_request from tool_summary.
    def _find_original_request() -> str | None:
        for entry in reversed(_history[-10:]):
            if (
                entry.get("role") == "tool_summary"
                and entry.get("tool") == "original_request"
                and entry.get("content")
            ):
                return entry["content"]
        return None

    for entry in reversed(_history[-10:]):
        role = entry.get("role", "")
        tool = entry.get("tool", "")
        logger.debug("plan_detection_scan role=%s tool=%s", role, tool)
        if role == "tool_summary" and tool == "analysis_plan":
            from superset.ai.agent.confirmation import is_creation_confirmed

            _found_plan = True
            if is_creation_confirmed(message):
                _execution_mode = "direct"
                # Prefer explicitly stored original_request over history
                # position traversal (fragile if conversation has extra turns).
                stored = _find_original_request()
                if stored:
                    _effective_request = stored
                else:
                    # Legacy fallback: walk history for 2nd user message
                    user_count = 0
                    for prior in reversed(_history):
                        if prior.get("role") == "user" and prior.get("content"):
                            user_count += 1
                            if user_count == 2:
                                _effective_request = prior["content"]
                                break
                logger.info(
                    "plan_confirmation_detected effective_request=%r",
                    _effective_request[:80],
                )
            break

    # Fallback: tasks.py blocked Turn 1 (no analysis_plan in history),
    # but the current message is a confirmation phrase.
    if not _found_plan:
        from superset.ai.agent.confirmation import is_creation_confirmed

        if is_creation_confirmed(message):
            _execution_mode = "direct"
            stored = _find_original_request()
            if stored:
                _effective_request = stored
            else:
                # Legacy fallback: walk history for 2nd user message
                user_count = 0
                for prior in reversed(_history):
                    if prior.get("role") == "user" and prior.get("content"):
                        user_count += 1
                        if user_count == 2:
                            _effective_request = prior["content"]
                            break
            logger.info(
                "tasks_confirmation_fallback effective_request=%r "
                "execution_mode=direct",
                _effective_request[:80],
            )

    initial_state: dict[str, Any] = {
        "request": _effective_request,
        "request_id": request_id,
        "session_id": session_id,
        "user_id": user_id,
        "database_id": database_id,
        "schema_name": schema_name,
        "agent_mode": agent_mode,
        "channel_id": channel_id or "",
        "conversation_history": _history,
        "created_charts": [],
        "repair_attempts": 0,
        "sql_attempts": 0,
        "previous_charts": previous_charts or [],
        "execution_mode": _execution_mode,
    }
    config: dict[str, Any] = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 100,
    }

    # Collect terminal state for conversation summary
    created_charts: list[dict[str, Any]] = []
    created_dashboard: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None
    last_sql: str | None = None
    last_table_name: str | None = None
    last_analysis_intent: str | None = None
    last_row_count: int | None = None

    # Transient error retry — same pattern as LangChain runner.
    # Retries on LLM API connection issues; non-retryable errors
    # break immediately.
    _RETRYABLE = (
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        ConnectionError,
        TimeoutError,
    )
    max_retries = 2

    for attempt in range(1, max_retries + 1):
        try:
            # stream_mode="updates" yields after each node completes
            t_node_start = time.monotonic()
            for state_update in graph.stream(
                initial_state, config=config, stream_mode="updates",
            ):
                if not isinstance(state_update, dict):
                    continue
                for node_name, node_output in state_update.items():
                    elapsed_ms = int((time.monotonic() - t_node_start) * 1000)
                    logger.info(
                        "graph_node_complete request_id=%s node=%s "
                        "elapsed_ms=%d",
                        request_id, node_name, elapsed_ms,
                    )
                    t_node_start = time.monotonic()
                    if isinstance(node_output, dict):
                        # Track terminal outputs for conversation summary
                        _acc = node_output.get("created_charts")
                        if _acc:
                            created_charts.extend(_acc)
                        _cd = node_output.get("created_dashboard")
                        if _cd:
                            created_dashboard = _cd
                        _le = node_output.get("last_error")
                        if isinstance(_le, dict):
                            last_error = _le
                        # Phase 11: collect SQL, table, intent for rich summary
                        _sql = node_output.get("sql")
                        if _sql:
                            last_sql = _sql
                        _schema = node_output.get("schema_summary")
                        if isinstance(_schema, dict) and _schema.get("table_name"):
                            last_table_name = _schema["table_name"]
                        _goal = node_output.get("goal")
                        if isinstance(_goal, dict) and _goal.get("analysis_intent"):
                            last_analysis_intent = _goal["analysis_intent"]
                        _summary = node_output.get("query_result_summary")
                        if isinstance(_summary, dict) and "row_count" in _summary:
                            last_row_count = _summary["row_count"]
                        yield from _emit_node_events(node_name, node_output)
            break  # success

        except _RETRYABLE as exc:
            logger.warning(
                "Graph transient error (attempt %d/%d): %s",
                attempt, max_retries, exc,
            )
            if attempt < max_retries:
                yield AgentEvent(
                    type="thinking",
                    data={
                        "content": (
                            f"连接暂时中断，正在重试 ({attempt}/{max_retries})..."
                        ),
                    },
                )
                continue
            yield AgentEvent(
                type="error",
                data={"message": "AI 服务暂时不可用，请稍后重试。"},
            )

        except Exception as exc:
            logger.exception("Graph execution failed")
            yield AgentEvent(
                type="error",
                data={"message": f"处理出错：{format_user_facing_error(exc)}"},
            )
            break

    # Build a conversation summary for multi-turn context
    summary = _build_summary(
        message,
        created_charts,
        created_dashboard,
        last_error,
        table_name=last_table_name,
        sql=last_sql,
        analysis_intent=last_analysis_intent,
        row_count=last_row_count,
    )
    done_data: dict[str, Any] = {"summary": summary}
    # Embed structured data for callers (e.g. tasks.py) to extract
    # reliably, even when child events are suppressed by child_events_published.
    if created_charts:
        done_data["created_charts"] = created_charts
    if last_sql:
        done_data["sql"] = last_sql
    if last_table_name:
        done_data["table_name"] = last_table_name
    if last_row_count is not None:
        done_data["row_count"] = last_row_count
    yield AgentEvent(type="done", data=done_data)


def _emit_node_events(  # noqa: C901
    node_name: str,
    node_output: dict[str, Any],
) -> Iterator[AgentEvent]:
    """Translate a node's output into AgentEvent(s)."""

    progress = _NODE_PROGRESS.get(node_name)

    # Special nodes with business payloads
    if node_name == "single_chart_subgraph":
        # The wrapper invokes the child subgraph synchronously, so
        # inner create_chart events don't appear in stream updates.
        # Emit chart_created from the wrapper's accumulated output.
        if node_output.get("child_events_published"):
            return
        created_charts = node_output.get("created_charts", [])
        for chart in created_charts:
            yield AgentEvent(type="chart_created", data=chart)
        if not created_charts and node_output.get("last_error"):
            yield from _emit_last_error(node_output["last_error"])
        return

    if node_name == "create_chart":
        chart = node_output.get("created_chart")
        if chart:
            yield AgentEvent(type="chart_created", data=chart)
        elif node_output.get("last_error"):
            yield from _emit_last_error(node_output["last_error"])
        return

    if node_name == "update_chart":
        chart = node_output.get("created_chart")
        if chart:
            yield AgentEvent(type="chart_updated", data=chart)
        elif node_output.get("last_error"):
            yield from _emit_last_error(node_output["last_error"])
        return

    if node_name == "create_dashboard":
        dash = node_output.get("created_dashboard")
        if dash:
            yield AgentEvent(type="dashboard_created", data=dash)
        elif node_output.get("last_error"):
            yield from _emit_last_error(node_output["last_error"])
        return

    if node_name == "review_analysis":
        # analysis_plan is already emitted by _publish_plan_event inside
        # the review_analysis node — don't duplicate here.
        return

    if node_name == "validate_sql":
        # validate_sql writes the compiled sql field after successful compilation
        sql = node_output.get("sql")
        if sql:
            yield AgentEvent(type="sql_generated", data={"sql": sql})

    if node_name == "analyze_result":
        summary = node_output.get("query_result_summary")
        if summary:
            insight = summary.get("insight")
            event_data: dict[str, Any] = {
                "row_count": summary.get("row_count"),
                "suitability": summary.get("suitability_flags"),
            }
            # Include parsed columns and rows for inline chart rendering
            query_result_raw = node_output.get("query_result_raw")
            if query_result_raw:
                parsed = _parse_text_table_for_event(query_result_raw)
                if parsed:
                    event_data["columns"] = parsed["columns"]
                    event_data["rows"] = parsed["rows"]
            if insight:
                event_data["insight"] = insight
            suggest = node_output.get("suggest_questions")
            if suggest:
                event_data["suggest_questions"] = suggest
            stats = node_output.get("statistics")
            if stats:
                event_data["statistics"] = stats
            yield AgentEvent(type="data_analyzed", data=event_data)
            # Phase 11: emit insight event for frontend display
            if insight:
                yield AgentEvent(
                    type="insight_generated",
                    data={"insight": insight},
                )

    # Error/repair events
    if node_name == "repair_chart_params":
        err = node_output.get("last_error", {})
        yield AgentEvent(
            type="error_fixed",
            data={"message": f"正在修复: {err.get('message', '')[:100]}"},
        )
        return

    last_err = node_output.get("last_error")
    if isinstance(last_err, dict) and last_err.get("recoverable") is False:
        yield AgentEvent(type="error", data=last_err)
        return

    # Default: thinking progress event
    if progress:
        _, done_msg = progress
        if done_msg:
            yield AgentEvent(type="thinking", data={"content": done_msg})


def _emit_last_error(last_error: dict[str, Any]) -> Iterator[AgentEvent]:
    """Emit recoverable graph errors without terminating the client stream."""
    if last_error.get("recoverable"):
        yield AgentEvent(
            type="error_fixed",
            data={"message": f"正在修复: {last_error.get('message', '')[:100]}"},
        )
        return

    yield AgentEvent(type="error", data=last_error)


def _build_summary(
    user_message: str,
    created_charts: list[dict[str, Any]],
    created_dashboard: dict[str, Any] | None,
    last_error: dict[str, Any] | None,
    *,
    table_name: str | None = None,
    sql: str | None = None,
    analysis_intent: str | None = None,
    row_count: int | None = None,
) -> str:
    """Build a text summary of graph execution for conversation history."""
    parts: list[str] = []
    if last_error and not created_charts:
        return f"任务失败: {last_error.get('message', 'unknown error')}"
    if table_name:
        parts.append(f"数据集: {table_name}")
    if analysis_intent:
        parts.append(f"分析意图: {analysis_intent}")
    if sql:
        # Truncate SQL to avoid bloating the conversation history
        sql_preview = sql[:500] + ("..." if len(sql) > 500 else "")
        parts.append(f"SQL: {sql_preview}")
    if row_count is not None:
        parts.append(f"查询结果行数: {row_count}")
    if created_charts:
        for c in created_charts:
            name = c.get("slice_name", "未命名")
            viz = c.get("viz_type", "unknown")
            cid = c.get("chart_id", "?")
            parts.append(f"创建了图表「{name}」(type={viz}, id={cid})")
    if created_dashboard:
        title = created_dashboard.get("dashboard_title", "未命名")
        did = created_dashboard.get("dashboard_id", "?")
        parts.append(f"创建了仪表板「{title}」(id={did})")
    if not parts:
        return "未生成任何图表"
    return "; ".join(parts)
