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
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
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
from superset.ai.agent.structured_context import (
    build_dataset_context,
    build_query_context,
    extract_table_from_sql,
)
from superset.ai.config import get_max_turns
from superset.ai.errors import format_user_facing_error
from superset.ai.runner import AgentRunner
from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.tools.base import BaseTool
from superset.ai.tools.create_chart import CreateChartTool
from superset.ai.tools.create_dashboard import CreateDashboardTool
from superset.ai.tools.data_analysis import DataAnalysisTool
from superset.ai.tools.embed_dashboard import EmbedDashboardTool
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
from superset.utils import json as superset_json


@contextmanager
def _nullcontext() -> Iterator[None]:
    """Minimal no-op context manager for when no user override is needed."""
    yield None


logger = logging.getLogger(__name__)

# Transient errors worth retrying (LLM API connection issues)
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    ConnectionError,
    TimeoutError,
)

# Map agent_type strings to their BaseTool classes and constructor kwargs
# Unified data assistant tools — merged from nl2sql + copilot
_DATA_ASSISTANT_TOOLS: list[tuple[type[BaseTool], list[str]]] = [
    # Database query tools
    (GetSchemaTool, ["database_id", "default_schema"]),
    (ExecuteSqlTool, ["database_id"]),
    (DataAnalysisTool, ["database_id"]),
    (SearchDatasetsTool, ["database_id", "schema_name"]),
    # Superset asset tools
    (ListDatabasesTool, []),
    (GetDatasetDetailTool, []),
    (ListChartsTool, []),
    (ListDashboardsTool, []),
    (GetChartDetailTool, []),
    (GetDashboardDetailTool, []),
    # User & platform tools
    (WhoAmITool, []),
    (QueryHistoryTool, []),
    (SavedQueryTool, []),
    (ReportStatusTool, []),
    (EmbedDashboardTool, []),
]

_TOOL_MAP: dict[str, list[tuple[type[BaseTool], list[str]]]] = {
    # Unified data assistant (replaces nl2sql + copilot)
    "data_assistant": _DATA_ASSISTANT_TOOLS,
    "nl2sql": _DATA_ASSISTANT_TOOLS,
    "copilot": _DATA_ASSISTANT_TOOLS,
    "debug": _DATA_ASSISTANT_TOOLS,
    # Chart/dashboard use StateGraph pipeline, these are only for LangChain fallback
    "chart": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
        (AnalyzeDataTool, ["database_id"]),
        (SearchDatasetsTool, ["database_id", "schema_name"]),
        (CreateChartTool, []),
    ],
    "dashboard": [
        (GetSchemaTool, ["database_id", "default_schema"]),
        (ExecuteSqlTool, ["database_id"]),
        (AnalyzeDataTool, ["database_id"]),
        (SearchDatasetsTool, ["database_id", "schema_name"]),
        (CreateChartTool, []),
        (CreateDashboardTool, []),
    ],
}


def _instantiate_tools(
    agent_type: str,
    database_id: int | None,
    schema_name: str | None,
) -> tuple[list[BaseTool], int | None]:
    """Create BaseTool instances for the given agent type.

    Returns (tools, effective_database_id).  When database_id is None,
    auto-selects if only one database exists.
    """
    # Auto-select database when not provided
    if database_id is None and any(
        "database_id" in keys for _, keys in _TOOL_MAP.get(agent_type, [])
    ):
        from superset.daos.database import DatabaseDAO

        databases = DatabaseDAO.find_all()
        if len(databases) == 1:
            database_id = databases[0].id
            logger.info("auto_selected_database id=%d", database_id)
        elif not databases:
            logger.warning("no databases available for agent %s", agent_type)
            return [], None
        # Multiple databases: tools requiring database_id are skipped below

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
    return tools, database_id


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
            tracked_tools={"create_chart", "create_dashboard", "execute_sql"},
        )
        self._order_guard: ToolOrderGuard | None = create_order_guard(
            agent_type
        )
        self._content_parts: list[str] = []
        self._sql_error_count: int = 0
        self._loop_detected: bool = False
        self._stream_repeat_detected: bool = False
        self._tool_args_by_id: dict[str, dict[str, Any]] = {}
        self._latest_tool_args_by_name: dict[str, dict[str, Any]] = {}

    def run(self, message: str) -> Iterator[AgentEvent]:
        """Execute the agent and yield AgentEvent instances."""
        from superset.utils.core import override_user

        self._tool_guard.reset()
        self._content_parts = []
        self._sql_error_count = 0
        self._loop_detected = False
        self._stream_repeat_detected = False
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
        native_tools, effective_db = _instantiate_tools(
            self._agent_type, self._database_id, self._schema_name
        )

        if not native_tools:
            yield AgentEvent(
                type="text_chunk",
                data={"content": "没有可用的数据库连接，请先选择一个数据库。"},
            )
            yield AgentEvent(type="done", data={})
            return

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

        # Persist user message to shared Redis key (best-effort)
        try:
            memory.add_user_message(message)
        except Exception:
            logger.warning("Failed to persist user message to Redis", exc_info=True)

        # Collect SQL tool results for post-execution consistency check
        self._sql_results: list[str] = []

        # Retry loop for transient LLM API failures (connection drops, timeouts).
        # Events are buffered per-attempt so that a transient error discards
        # partial results instead of sending duplicate events to the SSE stream.
        max_retries = 2
        last_exc: Exception | None = None
        buffered_events: list[AgentEvent] = []

        for attempt in range(1, max_retries + 1):
            try:
                # Reset per-attempt state
                self._content_parts = []
                self._sql_results = []
                self._sql_error_count = 0
                self._tool_args_by_id = {}
                self._latest_tool_args_by_name = {}
                buffered_events = []
                callback._turn_chars = 0
                callback._turn_text = ""
                callback._stopped = False

                # stream_mode=["messages", "updates"]:
                #   "messages" → (AIMessageChunk, metadata) for text tokens
                #   "updates"  → complete tool call/result snapshots
                # data_assistant/nl2sql/copilot need some history for references
                # like "this table", but should not see the full conversation
                # because local models may reuse historical answers instead of
                # calling tools.  Keep recent context (20 messages ≈ 10 rounds)
                # and rely on the prompt to force fresh SQL execution every turn.
                # chart/dashboard use StateGraph (no limit) or full history for
                # multi-step reasoning.
                _LIMITED_TYPES = {"data_assistant", "nl2sql", "copilot", "debug"}
                history_limit = 20 if self._agent_type in _LIMITED_TYPES else None
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
                    if self._loop_detected or callback.stopped:
                        logger.warning(
                            "Safety stop detected, breaking agent stream early"
                        )
                        break
                    for event in self._translate_event(mode, chunk):
                        buffered_events.append(event)
                    if self._loop_detected or callback.stopped:
                        logger.warning(
                            "Safety stop detected after event translation, "
                            "breaking agent stream early"
                        )
                        break

                if self._loop_detected:
                    # Loop guard already emitted an error event;
                    # just break the retry loop without retrying.
                    break

                if callback.stopped:
                    buffered_events.append(AgentEvent(
                        type="error",
                        data={"message": "回复内容超出安全限制，已自动截断。"},
                    ))
                last_exc = None
                break  # success — exit retry loop

            except _RETRYABLE_ERRORS as exc:
                last_exc = exc
                # Discard partial events from failed attempt
                logger.warning(
                    "LLM API transient error (attempt %d/%d): %s — "
                    "discarding %d partial events",
                    attempt, max_retries, exc, len(buffered_events),
                )
                buffered_events = []
                continue

            except GraphRecursionError:
                logger.warning(
                    "Agent hit recursion_limit=%d, returning partial result",
                    get_max_turns(),
                )
                buffered_events.append(AgentEvent(
                    type="warning",
                    data={"message": "问题较复杂，已用完处理步数。请尝试简化问题或分步提问。"},
                ))
                last_exc = None  # handled
                break

            except Exception as exc:
                logger.exception("LangChain agent execution failed")
                buffered_events.append(AgentEvent(
                    type="error",
                    data={"message": f"处理出错：{format_user_facing_error(exc)}"},
                ))
                last_exc = None  # non-retryable, already handled
                break

        # Emit retry warning before successful events so user sees context
        if last_exc is not None:
            logger.error(
                "LLM API failed after %d retries: %s", max_retries, last_exc,
            )
            yield AgentEvent(
                type="error",
                data={"message": "AI 服务暂时不可用，请稍后重试。"},
            )
        else:
            # Yield buffered events from the successful (or partial) attempt
            yield from buffered_events

        # Persist assistant response to shared Redis key.
        # Guard against Redis failures — a lost message is acceptable,
        # but an exception here would prevent the done event from being
        # emitted, leaving the client hanging.
        full_response = _deduplicate_content(self._content_parts)
        if full_response:
            try:
                memory.add_ai_message(full_response)
            except Exception:
                logger.warning(
                    "Failed to persist assistant response to Redis",
                    exc_info=True,
                )

        # Post-execution: cross-check numeric claims against SQL results
        if self._sql_results and full_response:
            warning = _check_response_consistency(full_response, self._sql_results)
            if warning:
                yield AgentEvent(type="warning", data={"message": warning})

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
            # Debug: log unexpected chunk types
            logger.debug(
                "handle_messages: unexpected chunk type=%s",
                type(chunk).__name__,
            )
            return

        msg, _metadata = chunk

        if isinstance(msg, AIMessageChunk):
            # Text content — with streaming repetition guard
            if msg.content:
                self._content_parts.append(msg.content)
                if not self._stream_repeat_detected:
                    full = "".join(self._content_parts)
                    if len(full) > 600:
                        mid = len(full) // 2
                        second_half = full[mid:]
                        first_half = full[:mid]
                        # Use a 150-char probe for stability (shorter
                        # fingerprints false-positive on structured text).
                        probe_len = min(150, len(second_half))
                        if len(second_half) >= probe_len:
                            probe = second_half[:probe_len]
                            if probe in first_half:
                                self._stream_repeat_detected = True
                                self._loop_detected = True
                                logger.warning(
                                    "Stream repetition detected at %d chars, "
                                    "stopping agent stream early",
                                    len(full),
                                )
                if not self._stream_repeat_detected:
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
            # Collect execute_sql results for consistency check
            if tool_name == "execute_sql" and result:
                self._sql_results.append(str(result))
                self._persist_query_context(tool_name, msg, str(result))
            elif tool_name == "analyze_data" and result:
                self._persist_query_context(tool_name, msg, str(result))
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
                        "message": "数据库连接池已耗尽，请稍后重试。",
                    },
                )

            # Emit structured data analysis events for the frontend
            if (
                tool_name == "analyze_data"
                and isinstance(result, str)
                and not result.startswith("Error")
            ):
                try:
                    parsed = superset_json.loads(result)
                    if isinstance(parsed, dict) and "row_count" in parsed:
                        event_data: dict[str, Any] = {
                            "row_count": parsed.get("row_count"),
                        }
                        if parsed.get("columns"):
                            event_data["columns"] = parsed["columns"]
                        if parsed.get("rows"):
                            event_data["rows"] = parsed["rows"]
                        if parsed.get("suitability"):
                            event_data["suitability"] = parsed["suitability"]
                        if parsed.get("statistics"):
                            event_data["statistics"] = parsed["statistics"]
                        if parsed.get("col_stats"):
                            event_data["col_stats"] = parsed["col_stats"]
                        if parsed.get("trend"):
                            event_data["trend"] = parsed["trend"]
                        if parsed.get("suggest_questions"):
                            event_data["suggest_questions"] = parsed[
                                "suggest_questions"
                            ]
                        insight = parsed.get("insight")
                        if insight:
                            event_data["insight"] = insight
                        yield AgentEvent(type="data_analyzed", data=event_data)
                        if insight:
                            yield AgentEvent(
                                type="insight_generated",
                                data={"insight": insight},
                            )
                except (ValueError, KeyError):
                    pass

            # Self-repair: detect SQL errors and inject a repair hint
            # so the LLM can self-correct in the next ReAct step.
            if (
                tool_name == "execute_sql"
                and isinstance(result, str)
                and _is_sql_error(result)
                and self._sql_error_count < 3
            ):
                self._sql_error_count += 1
                hint = _build_sql_repair_hint(result, self._sql_error_count)
                if hint:
                    yield AgentEvent(
                        type="tool_repair",
                        data={
                            "tool": tool_name,
                            "attempt": self._sql_error_count,
                            "hint": hint,
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
                    if len(msg.tool_calls) > 1:
                        names = [tc.get("name", "?") for tc in msg.tool_calls]
                        logger.info(
                            "Model returned %d parallel tool_calls: %s",
                            len(msg.tool_calls),
                            names,
                        )

                    for tool_call in msg.tool_calls:
                        tool_name = tool_call.get("name")
                        args = tool_call.get("args") or {}
                        if not tool_name:
                            continue

                        if self._tool_guard.check(tool_name, args):
                            logger.warning(
                                "Tool '%s' called %d times consecutively, "
                                "injecting correction. Last args: %s",
                                tool_name,
                                self._tool_guard._max,
                                str(args)[:200],
                            )
                            self._loop_detected = True
                            yield AgentEvent(
                                type="error",
                                data={
                                    "message": (
                                        f"工具 '{tool_name}' 重复调用过多，已跳过。"
                                        "请尝试换一种方式描述你的问题。"
                                    ),
                                },
                            )
                            return

                        yield AgentEvent(
                            type="tool_call",
                            data={"tool": tool_name, "args": args},
                        )
                        tool_call_id = tool_call.get("id")
                        if tool_call_id and isinstance(args, dict):
                            self._tool_args_by_id[tool_call_id] = args
                        if isinstance(args, dict):
                            self._latest_tool_args_by_name[tool_name] = args
                        if (
                            tool_name == "execute_sql"
                            and isinstance(args, dict)
                            and args.get("sql")
                        ):
                            yield AgentEvent(
                                type="sql_generated",
                                data={"sql": args["sql"]},
                            )
                elif isinstance(msg, AIMessage) and msg.content:
                    # Final text response (no tool_calls) — local LLMs
                    # may deliver the answer here instead of streaming
                    # tokens via AIMessageChunk.
                    # Skip if the same content was already streamed via
                    # _handle_messages (detected by checking if content
                    # is a suffix of accumulated content_parts).
                    if self._content_parts:
                        joined = "".join(self._content_parts)
                        if msg.content in joined:
                            continue  # already streamed
                    self._content_parts.append(msg.content)
                    yield AgentEvent(
                        type="text_chunk",
                        data={"content": msg.content},
                    )

    def _persist_query_context(
        self,
        tool_name: str,
        msg: ToolMessage,
        result: str,
    ) -> None:
        """Persist structured query/dataset context for later agent modes."""
        tool_call_id = getattr(msg, "tool_call_id", "")
        args = self._tool_args_by_id.get(
            tool_call_id,
            self._latest_tool_args_by_name.get(tool_name, {}),
        )
        sql = str(args.get("sql", "")).strip()
        if not sql:
            return

        table_name = extract_table_from_sql(sql)
        memory = self._get_memory()
        try:
            parsed = (
                superset_json.loads(result)
                if tool_name == "analyze_data" and result.strip().startswith("{")
                else None
            )
            columns = parsed.get("columns") if isinstance(parsed, dict) else None
            row_count = parsed.get("row_count") if isinstance(parsed, dict) else None
            memory.add_structured_context(
                "query_context",
                build_query_context(
                    sql=sql,
                    result_preview=result,
                    database_id=self._database_id,
                    schema_name=self._schema_name,
                    table_name=table_name,
                    columns=columns if isinstance(columns, list) else None,
                    row_count=row_count if isinstance(row_count, int) else None,
                ),
            )
            if table_name:
                memory.add_structured_context(
                    "dataset_context",
                    build_dataset_context(
                        table_name=table_name,
                        sql=sql,
                        database_id=self._database_id,
                        schema_name=self._schema_name,
                    ),
                )
        except Exception:
            logger.warning(
                "Failed to persist structured context for %s",
                tool_name,
                exc_info=True,
            )


def _deduplicate_content(parts: list[str]) -> str:
    """Remove repeated blocks from LLM output.

    Local models sometimes repeat the same answer 2-4 times verbatim.
    Strategy:
    1. Detect whole-block repetition (suffix appears earlier in text).
    2. Fall back to consecutive paragraph-level dedup.
    """
    if not parts:
        return ""
    full = "".join(parts)
    text = full.strip()
    n = len(text)
    if n < 100:
        return text

    # --- Strategy 1: whole-block repetition ---
    # Probe: take a fingerprint from the last portion and search for it
    # in the first 60% of the text.  If found with a long enough match
    # (>150 chars), the text from that point is a repeat.
    probe = text[-min(200, n // 3):]
    search_limit = n * 3 // 5
    fp = probe[:150]
    if len(fp) < 100:
        fp = probe[:80]  # fallback for very short text
    idx = text.find(fp, 0, search_limit)
    if idx >= 0:
        # The repeat boundary is somewhere around idx + len(probe).
        # Walk backward to find a natural break (blank line or newline).
        boundary = idx
        # Try to snap to a nearby double-newline or newline
        for offset in range(min(20, idx)):
            pos = idx - offset
            if pos > 0 and text[pos - 1:pos + 1] == "\n\n":
                boundary = pos
                break
            if pos > 0 and text[pos - 1] == "\n":
                boundary = pos
                break
        candidate = text[:boundary].rstrip()
        if len(candidate) > 150:
            logger.info(
                "Block dedup: %d → %d chars", n, len(candidate),
            )
            return candidate

    # --- Strategy 2: consecutive paragraph dedup ---
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    deduped: list[str] = []
    for para in paragraphs:
        if deduped and para.strip() == deduped[-1].strip():
            continue
        deduped.append(para)
    result = "\n\n".join(deduped)
    if len(result) < n:
        logger.info("Paragraph dedup: %d → %d chars", n, len(result))
    return result


def _is_connection_pool_error(content: Any) -> bool:
    """Return whether a tool result indicates DB connection pool exhaustion."""
    text = str(content)
    return "QueuePool limit" in text and "connection timed out" in text


# Patterns that indicate a recoverable SQL error (not a permission or
# mutation-rejection error, which the LLM cannot fix by rewriting SQL).
_SQL_ERROR_PATTERNS: list[tuple[str, str]] = [
    # (regex, error_category)
    (r"SQL Error|Error executing SQL|Could not parse SQL", "sql_execution"),
    (r"no such column|column .* does not exist|unknown column", "bad_column"),
    (r"no such table|relation .* does not exist|table .* not found", "bad_table"),
    (r"syntax error|Unexpected.*token", "syntax"),
    (r"division by zero", "logic"),
    (r"grouping error|not a GROUP BY expression|must appear in the GROUP BY", "group_by"),
    (r"aggregate functions? are not allowed", "aggregate"),
    (r"function .* does not exist|No matching function", "bad_function"),
]

# Non-recoverable errors — the LLM should not retry these.
_NON_RECOVERABLE_PATTERNS: list[str] = [
    r"Only SELECT queries are allowed",
    r"Access denied",
    r"No SQL provided",
]


def _is_sql_error(result: str) -> bool:
    """Return True if the execute_sql result is a recoverable SQL error."""
    if not (result.startswith("Error") or result.startswith("SQL Error")):
        return False
    for pattern in _NON_RECOVERABLE_PATTERNS:
        if re.search(pattern, result, re.IGNORECASE):
            return False
    for pattern, _ in _SQL_ERROR_PATTERNS:
        if re.search(pattern, result, re.IGNORECASE):
            return True
    # Generic "Error executing SQL" — treat as recoverable
    return "Error executing SQL" in result


def _build_sql_repair_hint(error_msg: str, attempt: int) -> str | None:
    """Build a concise repair hint for the LLM based on the SQL error.

    The hint is intentionally short so it guides the LLM without consuming
    too much context.  Returns None if no actionable hint can be derived.
    """
    parts: list[str] = []

    # Categorise the error
    for pattern, category in _SQL_ERROR_PATTERNS:
        if re.search(pattern, error_msg, re.IGNORECASE):
            if category == "bad_column":
                parts.append(
                    "列名可能不正确。请先使用 get_schema 工具查看表结构，"
                    "确认可用列名后再重写 SQL。"
                )
            elif category == "bad_table":
                parts.append(
                    "表名可能不正确。请先使用 search_datasets 或 get_schema "
                    "工具查看可用的表。"
                )
            elif category == "syntax":
                parts.append(
                    "SQL 语法有误。请检查关键字拼写、引号匹配和子查询结构。"
                )
            elif category == "group_by":
                parts.append(
                    "GROUP BY 错误。SELECT 中的非聚合列必须出现在 GROUP BY 中。"
                )
            elif category == "bad_function":
                parts.append(
                    "函数不存在。请使用 get_schema 确认可用的数据库函数。"
                )
            elif category in ("logic", "aggregate"):
                parts.append("请检查查询逻辑并修正。")
            break

    # Extract the Suggestion line from extract_errors output
    suggestion_match = re.search(r"Suggestion:\s*(.+)", error_msg)
    if suggestion_match:
        parts.append(f"数据库建议: {suggestion_match.group(1)}")

    if not parts:
        return None

    prefix = f"[SQL 自修复 {attempt}/3]"
    return f"{prefix} {' '.join(parts)}"


def _check_response_consistency(
    response: str,
    sql_results: list[str],
) -> str | None:
    """Cross-check numeric claims in the LLM response against SQL results.

    Returns a warning string if the response contains numbers that don't
    appear in any SQL result.  Returns None if everything looks consistent.
    """
    # Extract all integers >= 2 from the LLM's final response
    response_numbers = set(int(m) for m in re.findall(r"\b(\d+)\b", response) if int(m) >= 2)

    # Extract all numbers from SQL results (including table cells)
    sql_numbers: set[int] = set()
    for result in sql_results:
        for m in re.findall(r"\b(\d+)\b", result):
            num = int(m)
            if num >= 2:
                sql_numbers.add(num)

    if not response_numbers or not sql_numbers:
        return None

    # Find numbers in the response that don't appear in any SQL result
    # Allow small tolerance: skip if SQL has many numbers (large result set)
    # since the LLM may be summarizing/computing derived values
    if len(sql_numbers) > 20:
        return None

    unmatched = response_numbers - sql_numbers
    if unmatched and len(unmatched) <= 3:
        nums = ", ".join(str(n) for n in sorted(unmatched))
        return f"回答中的数字 ({nums}) 未在查询结果中找到，请以查询结果为准。"

    return None
