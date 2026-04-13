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
import time
import uuid
from collections.abc import Iterator
from typing import Any

from superset.ai.agent.events import AgentEvent

logger = logging.getLogger(__name__)

# Node → (start message, done message)
_NODE_PROGRESS: dict[str, tuple[str, str | None]] = {
    "parse_request": ("理解请求...", "请求解析完成"),
    "search_dataset": ("搜索数据集...", "数据集搜索完成"),
    "select_dataset": ("选择数据集...", "数据集已确定"),
    "read_schema": ("读取数据结构...", "Schema 读取完成"),
    "plan_dashboard": ("规划图表...", "图表规划完成"),
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


def run_graph(  # noqa: C901
    *,
    agent_mode: str,
    user_id: int,
    session_id: str,
    database_id: int,
    schema_name: str | None,
    message: str,
    channel_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
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

    initial_state: dict[str, Any] = {
        "request": message,
        "request_id": request_id,
        "session_id": session_id,
        "user_id": user_id,
        "database_id": database_id,
        "schema_name": schema_name,
        "agent_mode": agent_mode,
        "channel_id": channel_id or "",
        "conversation_history": conversation_history or [],
        "created_charts": [],
        "repair_attempts": 0,
        "sql_attempts": 0,
    }
    config: dict[str, Any] = {
        "configurable": {"thread_id": session_id},
    }

    try:
        # Collect terminal state for conversation summary
        created_charts: list[dict[str, Any]] = []
        created_dashboard: dict[str, Any] | None = None
        last_error: dict[str, Any] | None = None
        last_sql: str | None = None
        last_table_name: str | None = None
        last_analysis_intent: str | None = None
        last_row_count: int | None = None

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
                    "graph_node_complete request_id=%s node=%s elapsed_ms=%d",
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
    except Exception as exc:
        logger.exception("Graph execution failed")
        yield AgentEvent(type="error", data={"message": str(exc)})

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

    if node_name == "create_dashboard":
        dash = node_output.get("created_dashboard")
        if dash:
            yield AgentEvent(type="dashboard_created", data=dash)
        elif node_output.get("last_error"):
            yield from _emit_last_error(node_output["last_error"])
        return

    if node_name == "validate_sql":
        # validate_sql writes the compiled sql field after successful compilation
        sql = node_output.get("sql")
        if sql:
            yield AgentEvent(type="sql_generated", data={"sql": sql})

    if node_name == "analyze_result":
        summary = node_output.get("query_result_summary")
        if summary:
            yield AgentEvent(
                type="data_analyzed",
                data={
                    "row_count": summary.get("row_count"),
                    "suitability": summary.get("suitability_flags"),
                },
            )
            # Phase 11: emit insight event for frontend display
            insight = summary.get("insight")
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
