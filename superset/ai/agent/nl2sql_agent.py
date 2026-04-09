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
"""NL2SQL Agent – translates natural language to SQL."""

from __future__ import annotations

from typing import Any

from superset.ai.agent.base import BaseAgent
from superset.ai.agent.context import ConversationContext
from superset.ai.llm.base import BaseLLMProvider
from superset.ai.prompts.nl2sql import NL2SQL_SYSTEM_PROMPT
from superset.ai.tools.base import BaseTool
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.ai.tools.get_schema import GetSchemaTool


class NL2SQLAgent(BaseAgent):
    """Agent that converts natural language questions into SQL queries."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        context: ConversationContext,
        database_id: int,
        schema_name: str | None = None,
    ) -> None:
        tools: list[BaseTool] = [
            GetSchemaTool(database_id=database_id),
            ExecuteSqlTool(database_id=database_id),
        ]
        super().__init__(provider, context, tools)
        self._database_id = database_id
        self._schema_name = schema_name

    def get_system_prompt(self) -> str:
        prompt = NL2SQL_SYSTEM_PROMPT
        if self._schema_name:
            prompt += f"\n\nThe user is working in schema: {self._schema_name}"
        return prompt
