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
    from superset.ai.agent.context import ConversationContext
    from superset.ai.agent.nl2sql_agent import NL2SQLAgent
    from superset.ai.commands.chat import _AGENT_MAP
    from superset.ai.llm.registry import get_provider
    from superset.ai.streaming.manager import AiStreamManager

    channel_id = kwargs["channel_id"]
    user_id = kwargs["user_id"]
    message = kwargs["message"]
    database_id = kwargs["database_id"]
    schema_name = kwargs.get("schema_name")
    agent_type = kwargs.get("agent_type", "nl2sql")
    session_id = kwargs.get("session_id", channel_id)

    agent_cls = _AGENT_MAP.get(agent_type, NL2SQLAgent)
    stream = AiStreamManager()

    try:
        from superset.extensions import security_manager

        user = security_manager.get_user_by_id(user_id) if user_id else None
        with override_user(user):
            provider = get_provider()
            context = ConversationContext(user_id=user_id, session_id=session_id)
            agent = agent_cls(
                provider=provider,
                context=context,
                database_id=database_id,
                schema_name=schema_name,
            )
            for event in agent.run(message):
                stream.publish_event(channel_id, event)
    except Exception as exc:
        logger.exception("AI agent task failed")
        stream.publish_event(
            channel_id,
            AgentEvent(type="error", data={"message": str(exc)}),
        )

    return channel_id
