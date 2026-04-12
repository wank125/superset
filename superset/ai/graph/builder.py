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
"""Graph builder — assembles parent graph + child subgraph for StateGraph agent."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import START, StateGraph
from langgraph.types import Command, RetryPolicy

from superset.ai.graph.state import DashboardState, SingleChartState

logger = logging.getLogger(__name__)


def _build_single_chart_subgraph() -> StateGraph:
    """Child subgraph: single chart generation pipeline."""
    from superset.ai.graph import nodes_child as child

    b = StateGraph(SingleChartState)
    b.add_node("plan_query", child.plan_query)
    b.add_node("validate_sql", child.validate_sql)
    b.add_node(
        "execute_query",
        child.execute_query,
        retry=RetryPolicy(
            max_attempts=2,
            retry_on=lambda exc: isinstance(
                exc, (TimeoutError, ConnectionError, OSError)
            ),
        ),
    )
    b.add_node("analyze_result", child.analyze_result)
    b.add_node("select_chart", child.select_chart)
    b.add_node(
        "normalize_chart_params", child.normalize_chart_params
    )
    b.add_node("repair_chart_params", child.repair_chart_params)
    b.add_node(
        "create_chart",
        child.create_chart,
        retry=RetryPolicy(max_attempts=2),
    )

    b.add_edge(START, "plan_query")
    # All other edges are determined by Command(goto=...) inside nodes
    return b.compile()


def _make_subgraph_wrapper(subgraph: Any) -> Any:
    """Create a wrapper function that maps parent state to child state and back.

    The wrapper:
    1. Extracts the current chart intent from parent state
    2. Builds a SingleChartState from parent state fields
    3. Invokes the child subgraph
    4. Maps the child output back to parent state (created_charts accumulation)
    """

    def subgraph_node(
        state: DashboardState,
    ) -> Command[Literal["after_subgraph"]]:
        idx = state.get("current_chart_index", 0)
        intents = state.get("chart_intents", [])
        summary = state.get("schema_summary")

        if not intents or idx >= len(intents) or not summary:
            return Command(goto="after_subgraph")

        intent = intents[idx]

        # Build child state from parent state
        child_input: dict[str, Any] = {
            "chart_intent": intent,
            "schema_summary": summary,
            "database_id": state["database_id"],
            "request_id": state.get("request_id", ""),
            "repair_attempts": 0,
            "sql_attempts": 0,
        }

        # Invoke child subgraph
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": state.get("session_id", "default"),
            },
        }
        result = subgraph.invoke(child_input, config=config)

        # Map child output back to parent state
        updates: dict[str, Any] = {}

        created_chart = result.get("created_chart")
        if created_chart:
            # Use operator.add annotation — append to created_charts list
            updates["created_charts"] = [created_chart]

        last_error = result.get("last_error")
        if last_error:
            logger.warning(
                "Subgraph for chart %d ended with error: %s",
                idx,
                last_error.get("message", ""),
            )

        return Command(update=updates, goto="after_subgraph")

    return subgraph_node


def _after_subgraph_dashboard(
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph", "create_dashboard"]]:
    """Dashboard mode: loop through charts then create dashboard."""
    idx = state.get("current_chart_index", 0)
    intents = state.get("chart_intents", [])
    new_idx = idx + 1

    if new_idx < len(intents):
        return Command(
            update={"current_chart_index": new_idx},
            goto="single_chart_subgraph",
        )

    return Command(
        update={"current_chart_index": new_idx},
        goto="create_dashboard",
    )


def _after_subgraph_chart(
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph", "__end__"]]:
    """Chart mode: loop through charts then end."""
    idx = state.get("current_chart_index", 0)
    intents = state.get("chart_intents", [])
    new_idx = idx + 1

    if new_idx < len(intents):
        return Command(
            update={"current_chart_index": new_idx},
            goto="single_chart_subgraph",
        )

    return Command(
        update={"current_chart_index": new_idx},
        goto="__end__",
    )


def build_dashboard_graph() -> Any:
    """Parent graph: full dashboard generation flow."""
    from superset.ai.graph import nodes_parent as parent

    subgraph = _build_single_chart_subgraph()
    subgraph_node = _make_subgraph_wrapper(subgraph)

    b = StateGraph(DashboardState)
    b.add_node("parse_request", parent.parse_request)
    b.add_node("search_dataset", parent.search_dataset)
    b.add_node("select_dataset", parent.select_dataset)
    b.add_node("read_schema", parent.read_schema)
    b.add_node("plan_dashboard", parent.plan_dashboard)
    b.add_node("single_chart_subgraph", subgraph_node)
    b.add_node(
        "after_subgraph", _after_subgraph_dashboard
    )
    b.add_node("create_dashboard", parent.create_dashboard)

    b.add_edge(START, "parse_request")
    # All other edges are determined by Command(goto=...) inside nodes

    return b.compile()


def build_chart_graph() -> Any:
    """Single-chart mode: includes plan_dashboard but skips create_dashboard."""
    from superset.ai.graph import nodes_parent as parent

    subgraph = _build_single_chart_subgraph()
    subgraph_node = _make_subgraph_wrapper(subgraph)

    b = StateGraph(DashboardState)
    b.add_node("parse_request", parent.parse_request)
    b.add_node("search_dataset", parent.search_dataset)
    b.add_node("select_dataset", parent.select_dataset)
    b.add_node("read_schema", parent.read_schema)
    b.add_node("plan_dashboard", parent.plan_dashboard)
    b.add_node("single_chart_subgraph", subgraph_node)
    b.add_node(
        "after_subgraph", _after_subgraph_chart
    )

    b.add_edge(START, "parse_request")
    return b.compile()
