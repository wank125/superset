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
"""Unit tests for Phase 16 intent router."""

from unittest.mock import patch

import pytest

from superset.ai.router.rules import is_continuation, keyword_route
from superset.ai.router.router import IntentRouter
from superset.ai.router.types import RouterContext


class TestKeywordRoute:
    """Test keyword_route() fast-path matching."""

    def test_chart_high_confidence(self):
        agent, conf = keyword_route("帮我做一个折线图")
        assert agent == "chart"
        assert conf >= 0.90

    def test_dashboard_explicit(self):
        agent, conf = keyword_route("创建一个仪表板，包含3个图表")
        assert agent == "dashboard"
        assert conf >= 0.90

    def test_copilot_report_failure(self):
        agent, conf = keyword_route("有没有失败的报告？")
        assert agent == "copilot"
        assert conf >= 0.90

    def test_debug_sql_error(self):
        agent, conf = keyword_route("这个 SQL 报错了：column not found")
        assert agent == "debug"
        assert conf >= 0.90

    def test_nl2sql_no_match(self):
        agent, conf = keyword_route("帮我了解一下情况")
        assert agent == "nl2sql"
        assert conf == 0.0  # No match, needs LLM

    def test_ambiguous_trend(self):
        """Single low-certainty keyword should be below threshold."""
        _, conf = keyword_route("看看销售趋势")
        assert conf < 0.75

    def test_multiple_high_hits(self):
        agent, conf = keyword_route("做一个折线图，再用柱状图对比")
        assert agent == "chart"
        assert conf >= 0.90


class TestIsContinuation:
    """Test continuation keyword detection."""

    def test_continuation_cn(self):
        assert is_continuation("改成柱状图")

    def test_continuation_en(self):
        assert is_continuation("also show the trend")

    def test_not_continuation(self):
        assert not is_continuation("统计销售额")


class TestContextContinuation:
    """Test context-based agent reuse."""

    def test_continuation_reuses_last_agent(self):
        ctx = RouterContext(
            last_agent="chart",
            last_message="做个折线图",
            session_id="s1",
            user_id=1,
        )
        decision = IntentRouter().route("改成柱状图", ctx)
        assert decision.agent == "chart"
        assert decision.method == "context"

    def test_no_continuation_without_keywords(self):
        ctx = RouterContext(
            last_agent="chart",
            last_message="做个折线图",
            session_id="s1",
            user_id=1,
        )
        decision = IntentRouter().route("统计一下销售额", ctx)
        assert decision.method != "context"

    def test_no_continuation_without_last_agent(self):
        ctx = RouterContext(
            last_agent=None,
            last_message=None,
            session_id="s1",
            user_id=1,
        )
        decision = IntentRouter().route("改成柱状图", ctx)
        assert decision.method != "context"


class TestRouterFallback:
    """Test fallback behaviour when LLM fails or is unsure."""

    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_failure_falls_back_to_nl2sql(self, mock_llm):
        mock_llm.side_effect = ValueError("timeout")
        decision = IntentRouter().route(
            "帮我分析一下这个情况",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "nl2sql"
        assert decision.method == "fallback"

    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_low_confidence_falls_back(self, mock_llm):
        mock_llm.return_value = {
            "agent": "copilot",
            "confidence": 0.3,
            "reason": "unsure",
        }
        decision = IntentRouter().route(
            "看一下",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "nl2sql"
        assert decision.method == "fallback"

    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_high_confidence_routes_correctly(self, mock_llm):
        mock_llm.return_value = {
            "agent": "chart",
            "confidence": 0.85,
            "reason": "user wants a visualization",
        }
        decision = IntentRouter().route(
            "展示一下数据分布",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "chart"
        assert decision.method == "llm"

    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_invalid_agent_falls_back(self, mock_llm):
        mock_llm.return_value = {
            "agent": "unknown_type",
            "confidence": 0.9,
            "reason": "hallucinated",
        }
        decision = IntentRouter().route(
            "random message",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "nl2sql"
