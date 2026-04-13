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
"""Celery tasks for AI Agent execution."""

from __future__ import annotations

import logging
from typing import Any

from superset.ai.agent.confirmation import is_creation_confirmed
from superset.ai.agent.events import AgentEvent
from superset.ai.config import get_agent_timeout
from superset.ai.errors import format_user_facing_error
from superset.extensions import celery_app
from superset.utils.core import override_user

logger = logging.getLogger(__name__)


def _cleanup_db_session(*, dispose_engine: bool = False) -> None:
    """Rollback and remove the current Celery process DB session."""
    from superset import db

    try:
        db.session.rollback()
    except Exception:
        logger.debug("Failed to rollback AI task DB session", exc_info=True)
    finally:
        db.session.remove()

    if dispose_engine:
        try:
            db.engine.dispose()
        except Exception:
            logger.debug("Failed to dispose AI task DB engine", exc_info=True)


@celery_app.task(soft_time_limit=get_agent_timeout())
def run_agent_task(kwargs: dict[str, Any]) -> str:
    """Run an AI agent in a Celery worker.

    Parameters (passed as a single dict for Celery serialisation):
        channel_id: Redis stream channel ID
        user_id: User ID for permission context
        message: User's natural language message
        database_id: Target database ID
        agent_type: Agent type string (e.g. "nl2sql")
        session_id: Conversation session ID
    """
    from superset.ai.config import use_stategraph
    from superset.ai.runner import create_agent_runner
    from superset.ai.streaming.manager import AiStreamManager

    channel_id = kwargs["channel_id"]
    user_id = kwargs["user_id"]
    message = kwargs["message"]
    database_id = kwargs.get("database_id")
    schema_name = kwargs.get("schema_name")
    agent_type = kwargs.get("agent_type", "auto")
    session_id = kwargs.get("session_id", channel_id)

    stream = AiStreamManager()

    logger.info(
        "agent_task_started request_id=%s agent_type=%s user_id=%s "
        "session_id=%s database_id=%s channel_id=%s",
        channel_id, agent_type, user_id, session_id, database_id, channel_id,
    )

    try:
        from superset.extensions import security_manager

        _cleanup_db_session(dispose_engine=True)
        user = security_manager.get_user_by_id(user_id) if user_id else None
        with override_user(user):
            # ── Phase 16: Intent routing ──────────────────────────────
            if agent_type == "auto":
                from superset import is_feature_enabled

                if is_feature_enabled("AI_AGENT_AUTO_ROUTE"):
                    from superset.ai.agent.context import ConversationContext
                    from superset.ai.router.router import IntentRouter
                    from superset.ai.router.types import RouterContext

                    ctx = ConversationContext(
                        user_id=user_id, session_id=session_id
                    )
                    history = ctx.get_history()
                    last_meta = next(
                        (
                            h
                            for h in reversed(history)
                            if h.get("role") == "router_meta"
                        ),
                        None,
                    )
                    router_ctx = RouterContext(
                        last_agent=last_meta.get("agent") if last_meta else None,
                        last_message=(
                            last_meta.get("message") if last_meta else None
                        ),
                        session_id=session_id,
                        user_id=user_id,
                    )

                    decision = IntentRouter().route(
                        message=message, context=router_ctx
                    )
                    agent_type = decision.agent

                    # P1 fix: re-check routed agent against its feature flag
                    _GATED: dict[str, str] = {
                        "chart": "AI_AGENT_CHART",
                        "debug": "AI_AGENT_DEBUG",
                        "dashboard": "AI_AGENT_DASHBOARD",
                        "copilot": "AI_AGENT_COPILOT",
                    }
                    if agent_type in _GATED and not is_feature_enabled(
                        _GATED[agent_type]
                    ):
                        logger.warning(
                            "Routed to %s but flag %s is off, falling back to nl2sql",
                            agent_type,
                            _GATED[agent_type],
                        )
                        agent_type = "nl2sql"

                    # Persist routing decision for next-turn context.
                    ctx.add_router_meta(
                        agent=decision.agent,
                        confidence=decision.confidence,
                        method=decision.method,
                        message=message,
                    )

                    # Notify frontend of the routing decision.
                    stream.publish_event(
                        channel_id,
                        AgentEvent(
                            type="intent_routed",
                            data={
                                "agent": decision.agent,
                                "confidence": round(decision.confidence, 2),
                                "method": decision.method,
                            },
                        ),
                    )

                    logger.info(
                        "intent_routed agent=%s confidence=%.2f method=%s "
                        "session=%s",
                        decision.agent,
                        decision.confidence,
                        decision.method,
                        session_id,
                    )
                else:
                    # Feature flag off — safe downgrade.
                    agent_type = "nl2sql"
            # ── End Phase 16 ─────────────────────────────────────────

            # Path selection (priority order):
            # 1. StateGraph pipeline (use_stategraph=True + chart/dashboard)
            #    → superset.ai.graph.runner.run_graph
            #    → LangGraph StateGraph with parent/child node pipeline
            # 2. LangChain runner (use_langchain=True, any agent_type)
            #    → superset.ai.agent.langchain.runner.LangChainAgentRunner
            # 3. Legacy runner (default fallback)
            #    → superset.ai.commands.chat.LegacyAgentRunner
            if use_stategraph() and agent_type in {"chart", "dashboard"}:
                from superset.ai.agent.context import ConversationContext
                from superset.ai.graph.runner import run_graph

                # Read conversation history for multi-turn context
                ctx = ConversationContext(user_id=user_id, session_id=session_id)
                ctx.add_message("user", message)

                if not is_creation_confirmed(message):
                    target = "图表" if agent_type == "chart" else "仪表板"
                    confirmation_message = (
                        f"我可以帮你创建{target}，但需要你先确认。"
                        f"我还没有执行任何创建操作。请回复“确认创建{target}”后我再继续。"
                    )
                    stream.publish_event(
                        channel_id,
                        AgentEvent(
                            type="text_chunk",
                            data={"content": confirmation_message},
                        ),
                    )
                    stream.publish_event(
                        channel_id,
                        AgentEvent(type="done", data={}),
                    )
                    ctx.add_message("assistant", confirmation_message)
                    logger.info(
                        "creation_confirmation_required request_id=%s "
                        "agent_type=%s session_id=%s",
                        channel_id,
                        agent_type,
                        session_id,
                    )
                    return channel_id

                events = run_graph(
                    agent_mode=agent_type,
                    user_id=user_id,
                    session_id=session_id,
                    database_id=database_id,
                    schema_name=schema_name,
                    message=message,
                    channel_id=channel_id,
                    conversation_history=ctx.get_history(),
                )

                # Collect the done event to extract conversation summary
                assistant_summary = ""
                sql_executed = ""
                created_chart_summaries: list[str] = []
                done_data: dict[str, Any] = {}
                for event in events:
                    stream.publish_event(channel_id, event)
                    # Fallback: collect from iterator if child events are yielded
                    if event.type == "sql_generated" and event.data.get("sql"):
                        sql_executed = event.data["sql"]
                    if event.type == "chart_created" and event.data:
                        created_chart_summaries.append(
                            f"chart_id={event.data.get('chart_id')}, "
                            f"slice_name={event.data.get('slice_name')}, "
                            f"viz_type={event.data.get('viz_type')}"
                        )
                    if event.type == "done":
                        assistant_summary = event.data.get("summary", "")
                        done_data = event.data

                # Extract from done event (reliable even when child events
                # are suppressed by child_events_published flag in runner)
                if done_data.get("sql") and not sql_executed:
                    sql_executed = done_data["sql"]
                if done_data.get("created_charts") and not created_chart_summaries:
                    for chart in done_data["created_charts"]:
                        created_chart_summaries.append(
                            f"chart_id={chart.get('chart_id')}, "
                            f"slice_name={chart.get('slice_name')}, "
                            f"viz_type={chart.get('viz_type')}"
                        )

                # Write assistant response back to conversation history
                if assistant_summary:
                    ctx.add_message("assistant", assistant_summary)

                # Phase 11: persist SQL as tool summary for next-turn context
                if sql_executed:
                    ctx.add_tool_summary(
                        "execute_sql",
                        f"SQL: {sql_executed[:500]}",
                    )

                # Phase 11: persist created charts for "modify this chart" context
                for chart_summary in created_chart_summaries:
                    ctx.add_tool_summary("create_chart", chart_summary)
            else:
                runner = create_agent_runner(
                    agent_type=agent_type,
                    database_id=database_id,
                    schema_name=schema_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                # The runner may need the User object for permission checks
                # inside tools (get_schema, create_chart, etc.).  Store it
                # on the runner so it can set up override_user internally.
                if hasattr(runner, "set_user"):
                    runner.set_user(user)
                events = runner.run(message)

                for event in events:
                    stream.publish_event(channel_id, event)
        logger.info(
            "agent_task_done request_id=%s agent_type=%s channel_id=%s",
            channel_id, agent_type, channel_id,
        )
    except Exception as exc:
        logger.exception(
            "agent_task_failed request_id=%s agent_type=%s channel_id=%s",
            channel_id, agent_type, channel_id,
        )
        stream.publish_event(
            channel_id,
            AgentEvent(
                type="error",
                data={"message": format_user_facing_error(exc)},
            ),
        )
    finally:
        _cleanup_db_session(dispose_engine=True)

    return channel_id
