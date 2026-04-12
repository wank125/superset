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
"""LangChain-based agent runner — executes agents via LangGraph."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.prebuilt import create_react_agent

from superset.ai.agent.confirmation import (
    is_creation_confirmed,
    is_side_effect_tool,
)
from superset.ai.agent.events import AgentEvent
from superset.ai.agent.langchain.callbacks import SafeguardCallbackHandler
from superset.ai.agent.langchain.guard import (
    create_order_guard,
    ToolCallRepetitionGuard,
    ToolOrderGuard,
)
from superset.ai.agent.langchain.llm import get_langchain_llm
from superset.ai.agent.langchain.memory import LangChainMemoryAdapter
from superset.ai.agent.langchain.prompts import prompt_adapter
from superset.ai.agent.langchain.tools import tool_adapter
from superset.ai.config import get_max_turns
from superset.ai.runner import AgentRunner
from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.tools.base import BaseTool
from superset.ai.tools.create_chart import CreateChartTool
from superset.ai.tools.create_dashboard import CreateDashboardTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.ai.tools.get_chart_detail import GetChartDetailTool
from superset.ai.tools.get_dashboard_detail import GetDashboardDetailTool
from superset.ai.tools.get_dataset_detail import GetDatasetDetailTool
from superset.ai.tools.get_schema import GetSchemaTool
from superset.ai.tools.list_charts import ListChartsTool
from superset.ai.tools.list_dashboards import ListDashboardsTool
from superset.ai.tools.list_databases import ListDatabasesTool
from superset.ai.tools.query_history import QueryHistoryTool
from superset.ai.tools.report_status import ReportStatusTool
from superset.ai.tools.saved_query import SavedQueryTool
from superset.ai.tools.search_datasets import SearchDatasetsTool
from superset.ai.tools.whoami import WhoAmITool


@contextmanager
def _nullcontext() -> Iterator[None]:
    """Minimal no-op context manager for when no user override is needed."""
    yield None


logger = logging.getLogger(__name__)

# Map agent_type strings to their BaseTool classes and constructor kwargs
_TOOL_MAP: dict[str, list[tuple[type[BaseTool], list[str]]]] = {
    "nl2sql": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
    ],
    "chart": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
        (AnalyzeDataTool, ["database_id"]),
        (SearchDatasetsTool, ["database_id", "schema_name"]),
        (CreateChartTool, []),
    ],
    "debug": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
    ],
    "dashboard": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
        (AnalyzeDataTool, ["database_id"]),
        (SearchDatasetsTool, ["database_id", "schema_name"]),
        (CreateChartTool, []),
        (CreateDashboardTool, []),
    ],
    "copilot": [
        (ListDatabasesTool, []),
        (GetDatasetDetailTool, []),
        (ListChartsTool, []),
        (ListDashboardsTool, []),
        (WhoAmITool, []),
        (GetChartDetailTool, []),
        (GetDashboardDetailTool, []),
        (QueryHistoryTool, []),
        (SavedQueryTool, []),
        (ReportStatusTool, []),
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
        (SearchDatasetsTool, ["database_id", "schema_name"]),
    ],
}


def _instantiate_tools(
    agent_type: str,
    database_id: int | None,
    schema_name: str | None,
) -> list[BaseTool]:
    """Create BaseTool instances for the given agent type."""
    tool_specs = _TOOL_MAP.get(agent_type, _TOOL_MAP["nl2sql"])
    tools: list[BaseTool] = []
    for tool_cls, kwargs_keys in tool_specs:
        # Skip tools that require database_id when it is not provided
        if "database_id" in kwargs_keys and database_id is None:
            continue
        kwargs: dict[str, Any] = {}
        if "database_id" in kwargs_keys:
            kwargs["database_id"] = database_id
        if "default_schema" in kwargs_keys:
            kwargs["default_schema"] = schema_name
        if "schema_name" in kwargs_keys:
            kwargs["schema_name"] = schema_name
        tools.append(tool_cls(**kwargs))
    return tools


class LangChainAgentRunner(AgentRunner):
    """Run an agent using LangGraph's create_react_agent.

    Provides the same ``run(message) -> Iterator[AgentEvent]`` interface
    as ``LegacyAgentRunner``, so the factory can dispatch uniformly.
    """

    def __init__(
        self,
        agent_type: str,
        database_id: int | None,
        schema_name: str | None,
        user_id: int,
        session_id: str,
    ) -> None:
        self._agent_type = agent_type
        self._database_id = database_id
        self._schema_name = schema_name
        self._user_id = user_id
        self._session_id = session_id
        self._tool_guard = ToolCallRepetitionGuard(
            max_consecutive=3,
            tracked_tools={"create_chart", "create_dashboard"},
        )
        self._order_guard: ToolOrderGuard | None = create_order_guard(
            agent_type
        )
        self._content_parts: list[str] = []

    def run(self, message: str) -> Iterator[AgentEvent]:
        """Execute the agent and yield AgentEvent instances."""
        from superset.utils.core import override_user

        self._tool_guard.reset()
        self._content_parts = []
        if self._order_guard is not None:
            self._order_guard.reset()

        # Use override_user if a User object was provided via set_user().
        # This ensures g.user is a proper User instance for permission
        # checks inside tools (get_schema, create_chart, etc.).
        user = getattr(self, "_user", None)
        ctx = override_user(user) if user else _nullcontext()

        with ctx:
            yield from self._run_inner(message)

    def _run_inner(self, message: str) -> Iterator[AgentEvent]:
        """Inner execution logic, called inside the override_user context."""
        llm = get_langchain_llm()
        native_tools = _instantiate_tools(
            self._agent_type, self._database_id, self._schema_name
        )
        confirmed = is_creation_confirmed(message)
        lc_tools = [
            tool_adapter(
                t,
                order_guard=self._order_guard,
                requires_confirmation=is_side_effect_tool(t.name),
                confirmed=confirmed,
            )
            for t in native_tools
        ]
        memory = self._get_memory()
        prompt = prompt_adapter(self._agent_type, self._schema_name)

        agent = create_react_agent(
            model=llm,
            tools=lc_tools,
            prompt=prompt,
        )

        callback = SafeguardCallbackHandler()
        config: dict[str, Any] = {
            "configurable": {"session_id": self._session_id},
            "callbacks": [callback],
            "recursion_limit": get_max_turns(),
        }

        # Persist user message to shared Redis key
        memory.add_user_message(message)

        try:
            # stream_mode=["messages", "updates"]:
            #   "messages" → (AIMessageChunk, metadata) for text tokens
            #   "updates"  → complete tool call/result snapshots
            # NL2SQL needs a small amount of history for references like
            # "this table", but should not see the full conversation because
            # local models may reuse historical answers instead of calling
            # tools. Keep only recent context and rely on the prompt to force
            # fresh schema/query validation every turn.
            history_limit = 5 if self._agent_type == "nl2sql" else None
            for mode, chunk in agent.stream(
                {
                    "messages": memory.get_messages(
                        include_history=True,
                        max_messages=history_limit,
                    )
                },
                config=config,
                stream_mode=["messages", "updates"],
            ):
                for event in self._translate_event(mode, chunk):
                    yield event

            if callback.stopped:
                yield AgentEvent(
                    type="error",
                    data={"message": "Response stopped by safety guard."},
                )
        except Exception as exc:
            logger.exception("LangChain agent execution failed")
            yield AgentEvent(
                type="error",
                data={"message": f"Agent error: {exc}"},
            )

        # Persist assistant response to shared Redis key
        full_response = "".join(self._content_parts)
        if full_response:
            memory.add_ai_message(full_response)

        yield AgentEvent(type="done", data={})

    def _get_memory(self) -> LangChainMemoryAdapter:
        """Build memory adapter backed by existing Redis keys."""
        return LangChainMemoryAdapter(
            user_id=self._user_id,
            session_id=self._session_id,
        )

    def _translate_event(
        self, mode: str, chunk: Any
    ) -> Iterator[AgentEvent]:
        """Translate LangGraph stream chunks into AgentEvent instances."""
        if mode == "messages":
            result = self._handle_messages(chunk)
        elif mode == "updates":
            result = self._handle_updates(chunk)
        else:
            return
        # Handler methods may return None instead of an iterator when
        # they hit an early return (no yield executed). Guard against
        # that so ``yield from`` doesn't crash on NoneType.
        if result is not None:
            yield from result

    def _handle_messages(self, chunk: Any) -> Iterator[AgentEvent]:  # noqa: C901
        """Handle 'messages' stream mode — text tokens and tool call chunks."""
        if not isinstance(chunk, tuple) or len(chunk) != 2:
            return

        msg, _metadata = chunk

        if isinstance(msg, AIMessageChunk):
            # Text content
            if msg.content:
                self._content_parts.append(msg.content)
                yield AgentEvent(
                    type="text_chunk",
                    data={"content": msg.content},
                )

        elif isinstance(msg, ToolMessage):
            # Tool execution result — advance order guard on success
            tool_name = getattr(msg, "name", "")
            if self._order_guard is not None and tool_name:
                self._order_guard.advance(tool_name)
            result = msg.content
            yield AgentEvent(
                type="tool_result",
                data={
                    "tool": tool_name,
                    "result": result,
                },
            )
            if _is_connection_pool_error(result):
                yield AgentEvent(
                    type="error",
                    data={
                        "message": (
                            "Database connection pool is exhausted. "
                            "Please retry after the worker releases connections."
                        ),
                    },
                )

    def _handle_updates(self, chunk: Any) -> Iterator[AgentEvent]:
        """Handle 'updates' stream mode — complete node outputs."""
        if not isinstance(chunk, dict):
            return

        # Process 'agent' node updates for complete tool calls
        agent_update = chunk.get("agent")
        if agent_update and isinstance(agent_update, dict):
            messages = agent_update.get("messages", [])
            for msg in messages:
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    # Single-tool forcing: if model returned multiple
                    # tool calls (ignoring parallel_tool_calls=False),
                    # only process the first one.
                    if len(msg.tool_calls) > 1:
                        logger.warning(
                            "Model returned %d tool_calls; only "
                            "executing first: %s",
                            len(msg.tool_calls),
                            msg.tool_calls[0]["name"],
                        )

                    for tool_call in msg.tool_calls[:1]:
                        tool_name = tool_call.get("name")
                        args = tool_call.get("args") or {}
                        if not tool_name:
                            continue

                        if self._tool_guard.check(tool_name, args):
                            logger.warning(
                                "Tool '%s' called %d times consecutively, "
                                "injecting correction",
                                tool_name,
                                self._tool_guard._max,
                            )
                            yield AgentEvent(
                                type="error",
                                data={
                                    "message": (
                                        f"Tool '{tool_name}' repeated too "
                                        f"many times, skipping."
                                    ),
                                },
                            )
                            return

                        yield AgentEvent(
                            type="tool_call",
                            data={"tool": tool_name, "args": args},
                        )
                        if (
                            tool_name == "execute_sql"
                            and isinstance(args, dict)
                            and args.get("sql")
                        ):
                            yield AgentEvent(
                                type="sql_generated",
                                data={"sql": args["sql"]},
                            )


def _is_connection_pool_error(content: Any) -> bool:
    """Return whether a tool result indicates DB connection pool exhaustion."""
    text = str(content)
    return "QueuePool limit" in text and "connection timed out" in text
