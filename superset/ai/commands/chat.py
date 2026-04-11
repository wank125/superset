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
"""AiChatCommand – entry point for AI chat interactions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from superset.ai.agent.context import ConversationContext
from superset.ai.agent.chart_agent import ChartAgent
from superset.ai.agent.nl2sql_agent import NL2SQLAgent
from superset.ai.config import get_agent_timeout
from superset.ai.llm.registry import get_provider
from superset.ai.streaming.manager import AiStreamManager
from superset.commands.base import BaseCommand
from superset.utils.core import override_user

logger = logging.getLogger(__name__)

# Map agent_type strings to agent classes
_AGENT_MAP: dict[str, type] = {
    "nl2sql": NL2SQLAgent,
    "chart": ChartAgent,
}


class AiChatCommand(BaseCommand):
    """Command that kicks off an AI agent conversation.

    This is the main entry point called from the API layer.  It validates
    input, creates the agent, and either runs it synchronously (for simple
    cases) or delegates to a Celery task (the default).
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._channel_id = str(uuid.uuid4())

    def run(self) -> dict[str, str]:
        """Execute the chat command synchronously (used by Celery task)."""
        self.validate()
        message = self._data["message"]
        database_id = self._data["database_id"]
        schema_name = self._data.get("schema_name")
        agent_type = self._data.get("agent_type", "nl2sql")
        session_id = self._data.get("session_id") or self._channel_id
        user_id = self._data.get("user_id")

        agent_cls = _AGENT_MAP.get(agent_type)
        if agent_cls is None:
            raise ValueError(f"Unknown agent type: {agent_type}")

        with override_user(user_id):
            provider = get_provider()
            context = ConversationContext(
                user_id=user_id,
                session_id=session_id,
            )
            agent = agent_cls(
                provider=provider,
                context=context,
                database_id=database_id,
                schema_name=schema_name,
            )
            stream = AiStreamManager()
            for event in agent.run(message):
                stream.publish_event(self._channel_id, event)

        return {"channel_id": self._channel_id}

    def validate(self) -> None:
        if not self._data.get("message"):
            raise ValueError("message is required")
        if not self._data.get("database_id"):
            raise ValueError("database_id is required")

    @property
    def channel_id(self) -> str:
        return self._channel_id
