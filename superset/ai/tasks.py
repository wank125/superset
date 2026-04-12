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

from superset.ai.agent.events import AgentEvent
from superset.ai.config import get_agent_timeout
from superset.extensions import celery_app
from superset.utils.core import override_user

logger = logging.getLogger(__name__)


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
    database_id = kwargs["database_id"]
    schema_name = kwargs.get("schema_name")
    agent_type = kwargs.get("agent_type", "nl2sql")
    session_id = kwargs.get("session_id", channel_id)

    stream = AiStreamManager()

    logger.info(
        "agent_task_started request_id=%s agent_type=%s user_id=%s "
        "session_id=%s database_id=%s channel_id=%s",
        channel_id, agent_type, user_id, session_id, database_id, channel_id,
    )

    try:
        from superset.extensions import security_manager

        user = security_manager.get_user_by_id(user_id) if user_id else None
        with override_user(user):
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
                for event in events:
                    stream.publish_event(channel_id, event)
                    if event.type == "done" and event.data.get("summary"):
                        assistant_summary = event.data["summary"]

                # Write assistant response back to conversation history
                if assistant_summary:
                    ctx.add_message("assistant", assistant_summary)
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
            AgentEvent(type="error", data={"message": str(exc)}),
        )
    finally:
        from superset import db

        db.session.remove()

    return channel_id
