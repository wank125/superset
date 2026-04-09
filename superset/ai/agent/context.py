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
from superset.ai.config import get_max_context_rounds

logger = logging.getLogger(__name__)

_CONTEXT_TTL = 3600  # 1 hour


class ConversationContext:
    """Manage conversation history per user+session in Redis.

    The key format is ``ai:ctx:{user_id}:{session_id}`` and stores a
    JSON-serialised list of message dicts.  Only the most recent
    ``AI_AGENT_MAX_CONTEXT_ROUNDS`` message pairs are retained.
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
        # Keep only recent rounds (each round = 2 messages: user + assistant)
        max_messages = self._max_rounds * 2
        if len(history) > max_messages:
            history = history[-max_messages:]
        self._cache().set(self._key, json.dumps(history), timeout=_CONTEXT_TTL)

    def clear(self) -> None:
        self._cache().delete(self._key)
