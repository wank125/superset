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
- "data_assistant": User wants to query data, explore schemas, search datasets,
               list charts/dashboards, check permissions, or any general data question.
               This is the default for most data-related requests.
               Examples: "查销售额", "有多少个数据集", "我有哪些图表", "count users",
               "birth_names 有多少行", "我有什么权限"
- "chart":     User explicitly wants to CREATE a chart visualization.
               Examples: "画折线图", "做一个柱状图", "visualize trend as chart"
- "dashboard": User explicitly wants to CREATE a dashboard with multiple charts.
               Examples: "做一个仪表板", "create overview dashboard"

Context:
  session_last_agent: {last_agent}
  session_last_message_preview: {last_message}

User message: {message}

Response format:
{{
  "agent": "data_assistant|chart|dashboard",
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
        if agent not in ("data_assistant", "nl2sql", "copilot", "chart", "dashboard"):
            agent = "data_assistant"
        return agent, confidence, reason
    except Exception as exc:
        logger.warning("LLM intent classifier failed: %s", exc)
        return "data_assistant", 0.5, f"fallback due to error: {exc}"
