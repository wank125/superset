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
}


def run_graph(  # noqa: C901
    *,
    agent_mode: str,
    user_id: int,
    session_id: str,
    database_id: int,
    schema_name: str | None,
    message: str,
) -> Iterator[AgentEvent]:
    """Build and execute the StateGraph, yielding real-time AgentEvents."""
    from superset.ai.graph.builder import build_chart_graph, build_dashboard_graph

    graph = (
        build_dashboard_graph()
        if agent_mode == "dashboard"
        else build_chart_graph()
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
        "created_charts": [],
        "repair_attempts": 0,
        "sql_attempts": 0,
    }
    config: dict[str, Any] = {
        "configurable": {"thread_id": session_id},
    }

    try:
        # stream_mode="updates" yields after each node completes
        for state_update in graph.stream(
            initial_state, config=config, stream_mode="updates",
        ):
            if not isinstance(state_update, dict):
                continue
            for node_name, node_output in state_update.items():
                if isinstance(node_output, dict):
                    yield from _emit_node_events(node_name, node_output)
    except Exception as exc:
        logger.exception("Graph execution failed")
        yield AgentEvent(type="error", data={"message": str(exc)})

    yield AgentEvent(type="done", data={})


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
        created_charts = node_output.get("created_charts", [])
        for chart in created_charts:
            yield AgentEvent(type="chart_created", data=chart)
        if not created_charts and node_output.get("last_error"):
            yield AgentEvent(
                type="error", data=node_output["last_error"],
            )
        return

    if node_name == "create_chart":
        chart = node_output.get("created_chart")
        if chart:
            yield AgentEvent(type="chart_created", data=chart)
        elif node_output.get("last_error"):
            yield AgentEvent(
                type="error", data=node_output["last_error"],
            )
        return

    if node_name == "create_dashboard":
        dash = node_output.get("created_dashboard")
        if dash:
            yield AgentEvent(type="dashboard_created", data=dash)
        elif node_output.get("last_error"):
            yield AgentEvent(
                type="error", data=node_output["last_error"],
            )
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
