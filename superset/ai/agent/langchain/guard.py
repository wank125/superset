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
    """Detects consecutive repetitive tool calls that keep failing.

    Only counts calls whose result was an error. Successful calls reset
    the repetition counter for that (tool, normalised_args) pair. This
    prevents false positives when the agent legitimately runs the same
    tool multiple times with different conditions and succeeds.

    For ``execute_sql``, uses SQL-aware normalisation (whitespace and
    case folding) so that trivially reformatted queries are treated as
    duplicates.

    This guard operates at the runner's event translation layer — it
    observes tool_call events, not LLM tokens, so it catches loops
    that SafeguardCallbackHandler (token-level) cannot.
    """

    def __init__(
        self,
        max_consecutive: int = 3,
        tracked_tools: set[str] | None = None,
    ) -> None:
        self._history: list[tuple[str, str, bool]] = []
        self._pending_call: tuple[str, str] | None = None
        self._max = max_consecutive
        self._tracked_tools = tracked_tools

    def check(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        """Record a tool call and return True if error repetition limit exceeded.

        Stores the call as pending until ``mark_result()`` is called with
        the actual result. Only consecutive *errors* with the same
        normalised arguments count toward the limit.
        """
        if self._tracked_tools is not None and tool_name not in self._tracked_tools:
            return False

        normalised = self._normalize_arguments(arguments)
        self._pending_call = (tool_name, normalised)

        # Count consecutive errors with same (tool, args).
        # Include the current call in the count: we need max_consecutive - 1
        # prior errors in history plus this call to trigger.
        needed = self._max - 1
        if len(self._history) >= needed and needed > 0:
            tail = self._history[-needed:]
            if all(
                tool == tool_name
                and args == normalised
                and error
                for tool, args, error in tail
            ):
                return True
        elif self._max <= 1:
            return True
        return False

    def mark_result(self, error: bool) -> None:
        """Mark the pending call's result (error=True / success=False).

        Must be called after ``check()`` once the tool result is known.
        If no call is pending (e.g. untracked tool), this is a no-op.
        """
        if self._pending_call is not None:
            tool_name, normalised = self._pending_call
            self._history.append((tool_name, normalised, error))
            self._pending_call = None

    def reset(self) -> None:
        """Clear history (called at the start of each agent run)."""
        self._history.clear()
        self._pending_call = None

    @staticmethod
    def _normalize_arguments(arguments: dict[str, Any] | None) -> str:
        """Return a stable representation for tool-call arguments."""
        if not arguments:
            return "{}"

        # SQL-aware normalisation: fold whitespace and case so that
        # "SELECT  id  FROM  t" and "select id from t" match.
        sql = arguments.get("sql", "")
        if sql:
            import re as _re

            normalised_sql = _re.sub(r"\s+", " ", sql.strip()).lower()
            arguments = {**arguments, "sql": normalised_sql}

        try:
            return json.dumps(arguments, sort_keys=True, default=str)
        except TypeError:
            logger.debug("Failed to JSON-normalize tool arguments", exc_info=True)
            return str(arguments)


# Ordered phases for dashboard agent tool calls.
_DASHBOARD_PHASES: list[str] = [
    "search_datasets",
    "analyze_data",
    "create_chart",
    "create_dashboard",
]

# Read-only tools that are always allowed regardless of phase.
_READ_TOOLS: set[str] = {
    "execute_sql",
    "get_schema",
}


class ToolOrderGuard:
    """Enforces sequential tool-calling order for a given agent type.

    Used by both LegacyAgentRunner (via DashboardAgent) and
    LangChainAgentRunner.  Each call to ``check`` records the tool and
    returns True if the tool is allowed at the current phase, False if
    it violates the order.
    """

    def __init__(self, phases: list[str] | None = None) -> None:
        self._phases = phases or []
        self._phase_idx = 0

    def check(self, tool_name: str) -> bool:
        """Return True if *tool_name* is allowed at the current phase.

        Read-only tools always pass. Ordered tools may be called when
        they are the current phase or an already-completed phase. After
        an ordered tool executes the caller should call ``advance(tool_name)``.
        """
        if not self._phases:
            return True  # no ordering enforced
        if tool_name in _READ_TOOLS:
            return True
        phase = self._phase_of(tool_name)
        if phase < 0:
            return True  # unknown tool — don't block
        return phase <= self._phase_idx

    def advance(self, tool_name: str) -> None:
        """Move to the next phase after *tool_name* is executed."""
        phase = self._phase_of(tool_name)
        if phase >= 0 and phase == self._phase_idx:
            self._phase_idx = min(self._phase_idx + 1, len(self._phases))

    @property
    def phase_idx(self) -> int:
        """Current phase index (for testing)."""
        return self._phase_idx

    @property
    def allowed_tools(self) -> set[str]:
        """Set of tool names allowed at the current phase."""
        if not self._phases:
            return set()  # no ordering enforced
        allowed: set[str] = set(_READ_TOOLS)
        end_idx = min(self._phase_idx + 1, len(self._phases))
        allowed.update(self._phases[:end_idx])
        return allowed

    def reset(self) -> None:
        """Reset to initial phase."""
        self._phase_idx = 0

    def _phase_of(self, tool_name: str) -> int:
        """Return the phase index for a tool, or -1 if untracked."""
        try:
            return self._phases.index(tool_name)
        except ValueError:
            return -1


def create_order_guard(agent_type: str) -> ToolOrderGuard | None:
    """Factory: return a ToolOrderGuard for the given agent type, or None."""
    if agent_type == "dashboard":
        return ToolOrderGuard(phases=_DASHBOARD_PHASES)
    return None
