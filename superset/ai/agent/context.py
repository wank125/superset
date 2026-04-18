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
"""Conversation context backed by Redis."""

from __future__ import annotations

import json
import logging
from typing import Any

from superset import cache_manager
from superset.ai.agent.structured_context import (
    ContextKind,
    dump_context,
    StructuredContext,
)
from superset.ai.config import get_max_context_rounds

logger = logging.getLogger(__name__)

_CONTEXT_TTL = 3600  # 1 hour
_MAX_TOOL_SUMMARIES = 8


class ConversationContext:
    """Manage conversation history per user+session in Redis.

    The key format is ``ai:ctx:{user_id}:{session_id}`` and stores a
    JSON-serialised list of message dicts.  Only the most recent
    ``AI_AGENT_MAX_CONTEXT_ROUNDS`` message pairs are retained.

    Tool summaries (``add_tool_summary``) are stored as entries with
    ``role="tool_summary"`` and are trimmed independently to at most
    ``_MAX_TOOL_SUMMARIES`` entries.
    """

    def __init__(self, user_id: int, session_id: str) -> None:
        self._key = f"ai:ctx:{user_id}:{session_id}"
        self._max_rounds = get_max_context_rounds()

    def _cache(self) -> Any:
        return cache_manager.cache

    def get_history(self) -> list[dict[str, Any]]:
        raw = self._cache().get(self._key)
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return []
        return raw  # type: ignore[return-value]

    def add_message(self, role: str, content: str) -> None:
        history = self.get_history()
        history.append({"role": role, "content": content})
        # Trim: only count user/assistant entries toward the rounds budget.
        # tool_summary and router_meta are preserved (trimmed independently).
        max_messages = self._max_rounds * 2
        context_roles = {"user", "assistant"}
        context_entries = [h for h in history if h.get("role") in context_roles]
        if len(context_entries) > max_messages:
            excess = len(context_entries) - max_messages
            new_history: list[dict[str, Any]] = []
            for entry in history:
                if entry.get("role") in context_roles and excess > 0:
                    excess -= 1
                    continue
                new_history.append(entry)
            history = new_history
        self._cache().set(self._key, json.dumps(history), timeout=_CONTEXT_TTL)

    def clear(self) -> None:
        self._cache().delete(self._key)

    def add_router_meta(
        self,
        agent: str,
        confidence: float,
        method: str,
        message: str,
    ) -> None:
        """Store routing decision for next-turn context awareness.

        Stored with ``role="router_meta"``, excluded from LLM message list.
        Only the most recent entry is kept.
        """
        history = self.get_history()
        history = [h for h in history if h.get("role") != "router_meta"]
        history.append({
            "role": "router_meta",
            "agent": agent,
            "confidence": confidence,
            "method": method,
            "message": message[:200],
        })
        self._cache().set(self._key, json.dumps(history), timeout=_CONTEXT_TTL)

    def add_tool_summary(self, tool_name: str, content: str) -> None:
        """Record a key tool execution result for next-turn context.

        Entries are stored with ``role="tool_summary"`` alongside regular
        user/assistant messages.  When the history is loaded, callers should
        convert these to appropriate LLM message roles (e.g. ``system``).
        """
        history = self.get_history()
        history.append({
            "role": "tool_summary",
            "tool": tool_name,
            "content": content,
        })
        # Trim only tool_summary entries to _MAX_TOOL_SUMMARIES
        tool_summaries = [h for h in history if h.get("role") == "tool_summary"]
        if len(tool_summaries) > _MAX_TOOL_SUMMARIES:
            # Remove oldest tool_summary entries
            to_remove = len(tool_summaries) - _MAX_TOOL_SUMMARIES
            new_history: list[dict[str, Any]] = []
            removed = 0
            for entry in history:
                if entry.get("role") == "tool_summary" and removed < to_remove:
                    removed += 1
                    continue
                new_history.append(entry)
            history = new_history
        self._cache().set(
            self._key, json.dumps(history), timeout=_CONTEXT_TTL,
        )

    def add_structured_context(
        self,
        kind: ContextKind,
        context: StructuredContext,
    ) -> None:
        """Record a versioned structured context payload."""
        self.add_tool_summary(kind, dump_context({**context, "kind": kind}))
