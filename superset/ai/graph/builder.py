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
    b.add_node("generate_questions", child.generate_questions)
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


def _make_subgraph_wrapper(
    subgraph: Any,
    *,
    skip_create_chart: bool = False,
) -> Any:  # noqa: C901
    """Create a wrapper function that maps parent state to child state and back.

    The wrapper:
    1. Extracts the current chart intent from parent state
    2. Builds a SingleChartState from parent state fields
    3. Streams the child subgraph, publishing real-time events to Redis
    4. Maps the child output back to parent state (created_charts accumulation)
    """

    def subgraph_node(  # noqa: C901
        state: DashboardState,
    ) -> Command[Literal["after_subgraph"]]:
        idx = state.get("current_chart_index", 0)
        intents = state.get("chart_intents", [])
        summary = state.get("schema_summary")
        schema_cache = state.get("schema_cache") or {}

        if not intents or idx >= len(intents):
            return Command(goto="after_subgraph")

        intent = intents[idx]

        # Phase 18: per-chart dataset resolution
        target_table = intent.get("target_table")
        if target_table or not summary:
            # Multi-dataset mode: resolve schema per chart
            from superset.ai.graph.nodes_parent import resolve_dataset

            resolved = resolve_dataset(
                target_table,
                state["database_id"],
                state.get("schema_name"),
                schema_cache,
            )
            if not resolved:
                logger.warning(
                    "Failed to resolve dataset for chart %d (table=%s)",
                    idx,
                    target_table,
                )
                return Command(goto="after_subgraph")
            summary = resolved

        if not summary:
            return Command(goto="after_subgraph")

        # Build child state from parent state
        child_input: dict[str, Any] = {
            "chart_intent": intent,
            "schema_summary": summary,
            "database_id": state["database_id"],
            "request_id": state.get("request_id", ""),
            "channel_id": state.get("channel_id", ""),
            "skip_create_chart": skip_create_chart,
            "repair_attempts": 0,
            "sql_attempts": 0,
        }

        config: dict[str, Any] = {
            "configurable": {
                "thread_id": state.get("session_id", "default"),
            },
        }

        # Stream child subgraph and publish events in real-time
        channel_id = state.get("channel_id")
        stream_mgr = None
        if channel_id:
            from superset.ai.streaming.manager import AiStreamManager

            stream_mgr = AiStreamManager()

        result: dict[str, Any] = {}
        for sub_update in subgraph.stream(
            child_input, config=config, stream_mode="updates",
        ):
            if not isinstance(sub_update, dict):
                continue
            for child_node, child_output in sub_update.items():
                if not isinstance(child_output, dict):
                    continue
                # Keep track of latest state for each node
                result.update(child_output)
                # Publish child node events directly to Redis
                if stream_mgr:
                    _publish_child_events(
                        stream_mgr, channel_id, child_node, child_output,
                    )

        # Map child output back to parent state
        updates: dict[str, Any] = {}

        # Phase 18: propagate schema_cache updates back to parent state
        if schema_cache:
            updates["schema_cache"] = schema_cache

        created_chart = result.get("created_chart")
        if created_chart:
            # Phase 19b: attach suggested_width from child state
            suggested_width = result.get("suggested_width", 4)
            created_chart["suggested_width"] = suggested_width
            # Use operator.add annotation — append to created_charts list
            updates["created_charts"] = [created_chart]
            updates["child_events_published"] = stream_mgr is not None

        last_error = result.get("last_error")
        if last_error:
            logger.warning(
                "Subgraph for chart %d ended with error: %s",
                idx,
                last_error.get("message", ""),
            )

        return Command(update=updates, goto="after_subgraph")

    return subgraph_node


def _publish_child_events(
    stream_mgr: Any,
    channel_id: str,
    node_name: str,
    node_output: dict[str, Any],
) -> None:
    """Publish child subgraph node events to the Redis stream.

    Reuses the same progress mapping and event logic as the parent runner
    so the frontend sees a unified step list.
    """
    from superset.ai.graph.runner import _emit_node_events

    # Re-use the parent's event translation logic
    for event in _emit_node_events(node_name, node_output):
        stream_mgr.publish_event(channel_id, event)


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


def build_dashboard_graph(checkpointer: Any = None) -> Any:
    """Parent graph: full dashboard generation flow."""
    from superset.ai.graph import nodes_parent as parent

    subgraph = _build_single_chart_subgraph()
    subgraph_node = _make_subgraph_wrapper(subgraph)

    b = StateGraph(DashboardState)
    # Database auto-selection
    b.add_node("select_database", parent.select_database)
    # Phase 14: chart modification nodes
    b.add_node("check_schema", parent.check_schema)
    b.add_node("classify_intent", parent.classify_intent)
    b.add_node("load_existing_chart", parent.load_existing_chart)
    b.add_node("apply_chart_modification", parent.apply_chart_modification)
    b.add_node("update_chart", parent.update_chart)
    # Existing nodes
    b.add_node("parse_request", parent.parse_request)
    b.add_node("search_dataset", parent.search_dataset)
    b.add_node("select_dataset", parent.select_dataset)
    b.add_node("clarify_user", parent.clarify_user)
    b.add_node("read_schema", parent.read_schema)
    b.add_node("plan_dashboard", parent.plan_dashboard)
    b.add_node("review_analysis", parent.review_analysis)
    b.add_node("single_chart_subgraph", subgraph_node)
    b.add_node(
        "after_subgraph", _after_subgraph_dashboard
    )
    b.add_node("create_dashboard", parent.create_dashboard)

    b.add_edge(START, "select_database")
    # All other edges are determined by Command(goto=...) inside nodes

    return b.compile(checkpointer=checkpointer)


def build_chart_graph(checkpointer: Any = None) -> Any:
    """Single-chart mode: includes plan_dashboard but skips create_dashboard."""
    from superset.ai.graph import nodes_parent as parent

    subgraph = _build_single_chart_subgraph()
    subgraph_node = _make_subgraph_wrapper(subgraph, skip_create_chart=True)

    b = StateGraph(DashboardState)
    b.add_node("select_database", parent.select_database)
    b.add_node("check_schema", parent.check_schema)
    b.add_node("classify_intent", parent.classify_intent)
    b.add_node("load_existing_chart", parent.load_existing_chart)
    b.add_node("apply_chart_modification", parent.apply_chart_modification)
    b.add_node("update_chart", parent.update_chart)
    b.add_node("parse_request", parent.parse_request)
    b.add_node("search_dataset", parent.search_dataset)
    b.add_node("select_dataset", parent.select_dataset)
    b.add_node("clarify_user", parent.clarify_user)
    b.add_node("read_schema", parent.read_schema)
    b.add_node("plan_dashboard", parent.plan_dashboard)
    b.add_node("review_analysis", parent.review_analysis)
    b.add_node("single_chart_subgraph", subgraph_node)
    b.add_node("after_subgraph", _after_subgraph_chart)

    b.add_edge(START, "select_database")
    return b.compile(checkpointer=checkpointer)
