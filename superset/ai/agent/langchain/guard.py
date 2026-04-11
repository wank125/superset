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
"""Tool call repetition guard — prevents infinite tool loops."""

from __future__ import annotations

import logging
from typing import Any

from superset.utils import json

logger = logging.getLogger(__name__)


class ToolCallRepetitionGuard:
    """Detects consecutive repetitive tool calls.

    If the same tool is called consecutively with the same arguments more
    than *max_consecutive* times, returns True to signal that a correction
    should be injected.

    This guard operates at the runner's event translation layer — it
    observes tool_call events, not LLM tokens, so it catches loops
    that SafeguardCallbackHandler (token-level) cannot.
    """

    def __init__(
        self,
        max_consecutive: int = 3,
        tracked_tools: set[str] | None = None,
    ) -> None:
        self._history: list[tuple[str, str]] = []
        self._max = max_consecutive
        self._tracked_tools = tracked_tools

    def check(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        """Record a tool call and return True if repetition limit exceeded."""
        if self._tracked_tools is not None and tool_name not in self._tracked_tools:
            return False

        self._history.append((tool_name, self._normalize_arguments(arguments)))
        if len(self._history) >= self._max:
            tail = self._history[-self._max:]
            if len(set(tail)) == 1:  # all same tool and same arguments
                return True
        return False

    def reset(self) -> None:
        """Clear history (called at the start of each agent run)."""
        self._history.clear()

    @staticmethod
    def _normalize_arguments(arguments: dict[str, Any] | None) -> str:
        """Return a stable representation for tool-call arguments."""
        if not arguments:
            return "{}"

        try:
            return json.dumps(arguments, sort_keys=True, default=str)
        except TypeError:
            logger.debug("Failed to JSON-normalize tool arguments", exc_info=True)
            return str(arguments)
