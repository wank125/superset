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

logger = logging.getLogger(__name__)


class ToolCallRepetitionGuard:
    """Detects consecutive repetitive tool calls.

    If the same tool is called consecutively more than *max_consecutive*
    times, returns True to signal that a correction should be injected.

    This guard operates at the runner's event translation layer — it
    observes tool_call events, not LLM tokens, so it catches loops
    that SafeguardCallbackHandler (token-level) cannot.
    """

    def __init__(self, max_consecutive: int = 3) -> None:
        self._history: list[str] = []
        self._max = max_consecutive

    def check(self, tool_name: str) -> bool:
        """Record a tool call and return True if repetition limit exceeded."""
        self._history.append(tool_name)
        if len(self._history) >= self._max:
            tail = self._history[-self._max:]
            if len(set(tail)) == 1:  # all same tool
                return True
        return False

    def reset(self) -> None:
        """Clear history (called at the start of each agent run)."""
        self._history.clear()
