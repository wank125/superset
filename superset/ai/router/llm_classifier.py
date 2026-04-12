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
"""LLM-based intent classifier for ambiguous user messages."""

from __future__ import annotations

import logging

from superset.ai.graph.llm_helpers import llm_call_json

logger = logging.getLogger(__name__)

_CLASSIFIER_PROMPT = """\
Classify the user request to ONE agent type. Return ONLY valid JSON.

Agent types:
- "nl2sql":    User wants to query data from a database using SQL.
               Examples: "查销售额", "count users", "统计各地区数据"
- "chart":     User wants to create or visualize a chart.
               Examples: "画折线图", "做一个柱状图", "visualize trend"
- "dashboard": User wants to create a dashboard with multiple charts.
               Examples: "做一个仪表板", "create overview dashboard"
- "copilot":   User wants info about the Superset platform itself
               (not about the data IN the database, but about Superset's
               assets, permissions, report status, query history, etc.)
               Examples: "有哪些失败的报告", "我有什么权限", "查一下图表列表"
- "debug":     User wants to fix a broken SQL query.
               Examples: "这个 SQL 报错了", "fix: column not found"

Context:
  session_last_agent: {last_agent}
  session_last_message_preview: {last_message}

User message: {message}

Response format:
{{
  "agent": "nl2sql|chart|dashboard|copilot|debug",
  "confidence": 0.0-1.0,
  "reason": "one short sentence"
}}
"""


def llm_classify(
    message: str,
    last_agent: str | None,
    last_message: str | None,
) -> tuple[str, float, str]:
    """Classify a user message using a single LLM call.

    Returns ``(agent_type, confidence, reason)``.
    On any failure, returns ``("nl2sql", 0.5, "fallback")``.
    """
    prompt = _CLASSIFIER_PROMPT.format(
        message=message[:400],
        last_agent=last_agent or "none",
        last_message=(last_message or "")[:100],
    )
    try:
        result = llm_call_json(prompt)
        agent = result.get("agent", "nl2sql")
        confidence = float(result.get("confidence", 0.5))
        reason = result.get("reason", "")
        if agent not in ("nl2sql", "chart", "dashboard", "copilot", "debug"):
            agent = "nl2sql"
        return agent, confidence, reason
    except Exception as exc:
        logger.warning("LLM intent classifier failed: %s", exc)
        return "nl2sql", 0.5, f"fallback due to error: {exc}"
