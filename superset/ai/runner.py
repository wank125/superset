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
"""Unified agent runner — dispatches to legacy or LangChain path."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from superset.ai.agent.events import AgentEvent
from superset.ai.config import use_langchain

if TYPE_CHECKING:
    pass


class AgentRunner:
    """Abstract base — both paths implement this interface."""

    def run(self, message: str) -> Iterator[AgentEvent]:
        raise NotImplementedError

    def set_user(self, user: Any) -> None:
        """Store the Flask-AppBuilder User object for permission checks.

        Called by tasks.py after creating the runner.  The runner wraps
        ``run()`` with ``override_user(user)`` so tools like get_schema,
        create_chart, etc. can access ``g.user`` correctly.
        """
        self._user = user


def create_agent_runner(
    agent_type: str,
    database_id: int,
    schema_name: str | None,
    user_id: int,
    session_id: str,
) -> AgentRunner:
    """Factory: return the appropriate runner based on feature flag.

    Returns an object with a ``run(message: str) -> Iterator[AgentEvent]``
    method — callers don't need to know which implementation is active.
    """
    if use_langchain():
        from superset.ai.agent.langchain.runner import LangChainAgentRunner

        return LangChainAgentRunner(
            agent_type=agent_type,
            database_id=database_id,
            schema_name=schema_name,
            user_id=user_id,
            session_id=session_id,
        )
    else:
        from superset.ai.commands.chat import LegacyAgentRunner

        return LegacyAgentRunner(
            agent_type=agent_type,
            database_id=database_id,
            schema_name=schema_name,
            user_id=user_id,
            session_id=session_id,
        )
