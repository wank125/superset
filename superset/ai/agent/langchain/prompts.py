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
"""Prompt adapter — builds ChatPromptTemplate from existing agent prompts."""

from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from superset.ai.agent.chart_agent import ChartAgent
from superset.ai.agent.copilot_agent import CopilotAgent
from superset.ai.agent.dashboard_agent import DashboardAgent
from superset.ai.agent.debug_agent import DebugAgent
from superset.ai.agent.nl2sql_agent import NL2SQLAgent

_AGENT_PROMPT_BUILDERS: dict[str, type] = {
    "data_assistant": NL2SQLAgent,
    "nl2sql": NL2SQLAgent,
    "copilot": NL2SQLAgent,
    "chart": ChartAgent,
    "debug": DebugAgent,
    "dashboard": DashboardAgent,
}

_NL2SQL_EXECUTION_RULE = """

Execution rule:
- For every user request that asks for database data, tables, counts, rows,
  metrics, or SQL results, call execute_sql in the current turn.
- Do not answer from previous conversation history, cached results, or memory.
- Even if the same question was answered earlier, execute SQL again before
  giving the answer.
"""


def prompt_adapter(
    agent_type: str,
    schema_name: str | None = None,
) -> ChatPromptTemplate:
    """Build a ChatPromptTemplate using the existing agent's system prompt.

    Reuses the exact same prompt text (including dynamic chart_type_table
    and chart_type_details injection) — zero duplication.

    IMPORTANT: The system prompt text often contains JSON examples with
    curly braces (e.g. ``{"metric": "SUM(col)"}``).  LangChain's prompt
    template treats ``{…}`` as variable placeholders.  To avoid this
    conflict, we build the template with a *static* SystemMessage and a
    MessagesPlaceholder for the conversation history — no variable
    substitution happens on the system text itself.
    """
    agent_cls = _AGENT_PROMPT_BUILDERS.get(agent_type, NL2SQLAgent)

    # Create a lightweight instance to get the system prompt text.
    # We use __new__ to avoid triggering __init__ (which needs a real
    # provider/context), then set only the attributes needed for
    # get_system_prompt().
    agent = agent_cls.__new__(agent_cls)
    agent._schema_name = schema_name

    system_text = agent.get_system_prompt()
    if agent_type in ("nl2sql", "data_assistant", "copilot", "debug"):
        system_text += _NL2SQL_EXECUTION_RULE

    # Use a static SystemMessage (no variable interpolation) + a
    # MessagesPlaceholder for the conversation history.  This avoids
    # curly-brace conflicts with JSON examples in the prompt.
    return ChatPromptTemplate.from_messages([
        SystemMessage(content=system_text),
        MessagesPlaceholder(variable_name="messages"),
    ])
