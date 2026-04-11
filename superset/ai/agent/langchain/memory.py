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
"""Memory adapter — bridges Superset ConversationContext to LangChain messages."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from superset.ai.agent.context import ConversationContext

logger = logging.getLogger(__name__)


class LangChainMemoryAdapter:
    """Provides LangChain message lists from existing Redis-backed context.

    Reuses the same Redis keys as ConversationContext:
        ``ai:ctx:{user_id}:{session_id}``

    This ensures the LangChain path reads the same conversation history
    as the legacy path, allowing seamless switching via feature flag.
    """

    def __init__(self, user_id: int, session_id: str) -> None:
        self._ctx = ConversationContext(user_id=user_id, session_id=session_id)

    def get_messages(self) -> list[BaseMessage]:
        """Load history from Redis and convert to LangChain messages."""
        history = self._ctx.get_history()
        messages: list[BaseMessage] = []
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system":
                messages.append(SystemMessage(content=content))
            elif role == "tool":
                messages.append(ToolMessage(content=content, tool_call_id=""))
        return messages

    def add_user_message(self, content: str) -> None:
        """Persist a user message to the shared Redis key."""
        self._ctx.add_message("user", content)

    def add_ai_message(self, content: str) -> None:
        """Persist an assistant message to the shared Redis key."""
        self._ctx.add_message("assistant", content)

    def clear(self) -> None:
        """Clear conversation history."""
        self._ctx.clear()
