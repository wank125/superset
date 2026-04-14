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
"""Unit tests for Phase 19: plan analysis confirmation."""

from unittest.mock import MagicMock, patch


# ── _compute_confidence tests ──────────────────────────────────────


class TestComputeConfidence:
    """P19 _compute_confidence [Code]"""

    def test_high_confidence_no_risks(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {"dataset_match_score": 100},
            "chart_intents": [
                {"analysis_intent": "trend"},
            ],
            "agent_mode": "chart",
            "schema_summary": {
                "datetime_cols": ["ds"],
                "business_metrics": {},
            },
        }
        confidence = _compute_confidence(state)
        assert confidence >= 0.9

    def test_signal1_dataset_uncertainty(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {"dataset_match_score": 30},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {"datetime_cols": ["ds"], "business_metrics": {}},
        }
        confidence = _compute_confidence(state)
        # 30 risk points → confidence = 1.0 - 0.30 = 0.7
        assert confidence == 0.7

    def test_signal2_multi_topic(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {},
            "chart_intents": [
                {"analysis_intent": "trend"},
                {"analysis_intent": "composition"},
                {"analysis_intent": "kpi"},
            ],
            "agent_mode": "chart",
            "schema_summary": {"datetime_cols": ["ds"], "business_metrics": {}},
        }
        confidence = _compute_confidence(state)
        # 20 risk points → confidence = 0.8
        assert confidence == 0.8

    def test_signal3_dashboard_many_charts(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {},
            "chart_intents": [
                {"analysis_intent": "trend"},
                {"analysis_intent": "trend"},
                {"analysis_intent": "trend"},
            ],
            "agent_mode": "dashboard",
            "schema_summary": {"datetime_cols": ["ds"], "business_metrics": {}},
        }
        confidence = _compute_confidence(state)
        # 20 risk points (dashboard + 3 charts) → confidence = 0.8
        assert confidence == 0.8

    def test_signal4_derived_metrics(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {
                "datetime_cols": ["ds"],
                "business_metrics": {"收缴率": {"sql": "a/b"}},
            },
        }
        confidence = _compute_confidence(state)
        # 15 risk points → confidence = 0.85
        assert confidence == 0.85

    def test_signal5_no_time_column(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {"datetime_cols": [], "business_metrics": {}},
        }
        confidence = _compute_confidence(state)
        # 10 risk points → confidence = 0.9
        assert confidence == 0.9

    def test_signal6_multi_dataset(self):
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {"multi_dataset": True},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {"datetime_cols": ["ds"], "business_metrics": {}},
        }
        confidence = _compute_confidence(state)
        # 10 risk points → confidence = 0.9
        assert confidence == 0.9

    def test_multiple_signals_stacking(self):
        """Multiple signals should stack: uncertainty(30) + multi_topic(20) = 50."""
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {"dataset_match_score": 20},
            "chart_intents": [
                {"analysis_intent": "trend"},
                {"analysis_intent": "composition"},
                {"analysis_intent": "kpi"},
            ],
            "agent_mode": "chart",
            "schema_summary": {
                "datetime_cols": ["ds"],
                "business_metrics": {},
            },
        }
        confidence = _compute_confidence(state)
        # 30 + 20 = 50 → confidence = 0.5
        assert confidence == 0.5

    def test_confidence_minimum_zero(self):
        """Confidence should never go below 0.0."""
        from superset.ai.graph.nodes_parent import _compute_confidence

        state = {
            "goal": {"dataset_match_score": 10, "multi_dataset": True},
            "chart_intents": [
                {"analysis_intent": "trend"},
                {"analysis_intent": "composition"},
                {"analysis_intent": "kpi"},
            ],
            "agent_mode": "dashboard",
            "schema_summary": {
                "datetime_cols": [],
                "business_metrics": {"m1": {"sql": "a/b"}},
            },
        }
        confidence = _compute_confidence(state)
        # 30+20+20+15+10+10 = 105 → clamped to 0.0
        assert confidence == 0.0

    def test_missing_state_fields_no_crash(self):
        """Empty state should not crash — all defaults are safe."""
        from superset.ai.graph.nodes_parent import _compute_confidence

        confidence = _compute_confidence({})
        # Only signal5 triggers (no schema_summary → no datetime_cols → +10)
        # confidence = 1.0 - 0.10 = 0.9
        assert confidence == 0.9


# ── review_analysis tests ──────────────────────────────────────────


class TestReviewAnalysis:
    """P19 review_analysis [Code]"""

    def test_direct_mode_skip(self):
        """When execution_mode is already 'direct', skip to single_chart_subgraph."""
        from superset.ai.graph.nodes_parent import review_analysis

        state = {"execution_mode": "direct"}
        result = review_analysis(state)
        assert result.goto == "single_chart_subgraph"

    @patch("superset.ai.graph.nodes_parent._publish_plan_event")
    def test_plan_mode_publishes_and_ends(self, mock_publish):
        """Low confidence → plan mode: publish event, goto __end__."""
        from superset.ai.graph.nodes_parent import review_analysis

        # Trigger signal 1 (dataset_match_score < 50) for low confidence
        state = {
            "goal": {"dataset_match_score": 10},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {"datetime_cols": [], "business_metrics": {}},
            "channel_id": "test-channel",
        }
        result = review_analysis(state)
        assert result.goto == "__end__"
        assert result.update["execution_mode"] == "plan"
        assert result.update["analysis_plan"]["confidence"] < 0.7
        mock_publish.assert_called_once()

    def test_high_confidence_direct_execution(self):
        """High confidence → direct mode: continue to single_chart_subgraph."""
        from superset.ai.graph.nodes_parent import review_analysis

        state = {
            "goal": {"dataset_match_score": 100, "target_table": "birth_names"},
            "chart_intents": [{"analysis_intent": "trend"}],
            "agent_mode": "chart",
            "schema_summary": {
                "table_name": "birth_names",
                "datetime_cols": ["ds"],
                "dimension_cols": ["gender"],
                "metric_cols": ["num"],
                "business_metrics": {},
            },
        }
        result = review_analysis(state)
        assert result.goto == "single_chart_subgraph"
        assert result.update["execution_mode"] == "direct"
        assert result.update["analysis_plan"] is not None

    def test_explicit_plan_mode_override(self):
        """execution_mode='plan' forces plan mode even with high confidence."""
        from superset.ai.graph.nodes_parent import review_analysis

        with patch("superset.ai.graph.nodes_parent._publish_plan_event") as mock_pub:
            state = {
                "execution_mode": "plan",
                "goal": {"dataset_match_score": 100},
                "chart_intents": [{"analysis_intent": "trend"}],
                "agent_mode": "chart",
                "schema_summary": {
                    "table_name": "t",
                    "datetime_cols": ["ds"],
                    "metric_cols": [],
                    "dimension_cols": [],
                    "business_metrics": {},
                },
                "channel_id": "ch",
            }
            result = review_analysis(state)
            assert result.goto == "__end__"
            assert result.update["execution_mode"] == "plan"
            mock_pub.assert_called_once()


# ── _build_analysis_plan tests ─────────────────────────────────────


class TestBuildAnalysisPlan:
    """P19 _build_analysis_plan [Code]"""

    def test_complete_plan_structure(self):
        from superset.ai.graph.nodes_parent import _build_analysis_plan

        state = {
            "goal": {"target_table": "birth_names", "dataset_match_score": 100},
            "schema_summary": {
                "table_name": "birth_names",
                "datetime_cols": ["ds"],
                "dimension_cols": ["gender", "state"],
                "metric_cols": ["num"],
                "business_metrics": {},
            },
            "chart_intents": [
                {
                    "chart_index": 0,
                    "slice_name": "出生趋势图",
                    "analysis_intent": "trend",
                    "preferred_viz": "echarts_timeseries_line",
                },
                {
                    "chart_index": 1,
                    "slice_name": "性别分布",
                    "analysis_intent": "composition",
                    "preferred_viz": "pie",
                },
            ],
        }
        plan = _build_analysis_plan(state, 0.85)

        assert plan["dataset"] == "birth_names"
        assert plan["confidence"] == 0.85
        assert len(plan["charts"]) == 2
        assert plan["charts"][0]["title"] == "出生趋势图"
        assert plan["charts"][1]["title"] == "性别分布"
        assert "metrics_dimensions" in plan
        assert plan["metrics_dimensions"]["metrics"] == ["num"]
        assert plan["metrics_dimensions"]["dimensions"] == ["gender", "state"]

    def test_empty_schema_no_crash(self):
        from superset.ai.graph.nodes_parent import _build_analysis_plan

        plan = _build_analysis_plan({}, 0.5)
        assert plan["dataset"] == ""
        assert plan["charts"] == []
        assert plan["confidence"] == 0.5

    def test_multi_dataset_in_assumptions(self):
        from superset.ai.graph.nodes_parent import _build_analysis_plan

        state = {
            "goal": {
                "multi_dataset": True,
                "target_tables": ["orders", "users"],
            },
            "schema_summary": {
                "table_name": "orders",
                "datetime_cols": ["ds"],
                "dimension_cols": [],
                "metric_cols": ["amount"],
                "business_metrics": {},
            },
            "chart_intents": [],
        }
        plan = _build_analysis_plan(state, 0.9)
        assert any("orders" in a for a in plan["assumptions_risks"])


# ── _format_plan_text tests ────────────────────────────────────────


class TestFormatPlanText:
    """P19 _format_plan_text [Code]"""

    def test_basic_formatting(self):
        from superset.ai.graph.nodes_parent import _format_plan_text

        plan = {
            "dataset": "birth_names",
            "dataset_reason": "精确匹配",
            "metrics_dimensions": {
                "metrics": ["num"],
                "dimensions": ["gender"],
            },
            "time_range": "可用时间列: ds（未指定范围，默认全量数据）",
            "charts": [
                {"index": 0, "title": "趋势图", "intent": "trend", "viz": "折线图"},
            ],
            "assumptions_risks": ["假设 num 为主要指标"],
            "confidence": 0.85,
        }
        text = _format_plan_text(plan)
        assert "birth_names" in text
        assert "趋势图" in text
        assert "85%" in text
        assert "确认执行" in text

    def test_empty_plan_no_crash(self):
        from superset.ai.graph.nodes_parent import _format_plan_text

        text = _format_plan_text({})
        assert "分析计划" in text
        assert "确认执行" in text

    def test_no_charts_no_error(self):
        from superset.ai.graph.nodes_parent import _format_plan_text

        plan = {
            "dataset": "test_table",
            "dataset_reason": "auto",
            "metrics_dimensions": {"metrics": [], "dimensions": []},
            "time_range": "未找到时间列",
            "charts": [],
            "assumptions_risks": [],
            "confidence": 0.9,
        }
        text = _format_plan_text(plan)
        assert "test_table" in text
        assert "图表" not in text


# ── _publish_plan_event tests ──────────────────────────────────────


class TestPublishPlanEvent:
    """P19 _publish_plan_event [Code]"""

    @patch("superset.ai.streaming.manager.AiStreamManager")
    def test_publishes_two_events(self, mock_stream_cls):
        from superset.ai.graph.nodes_parent import _publish_plan_event

        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        state = {"channel_id": "test-ch"}
        plan = {"dataset": "t", "confidence": 0.5}

        _publish_plan_event(state, plan)

        # Should publish analysis_plan + text_chunk = 2 calls
        assert mock_stream.publish_event.call_count == 2

    def test_no_channel_id_skips(self):
        """If no channel_id, should not crash."""
        from superset.ai.graph.nodes_parent import _publish_plan_event

        # Should not raise
        _publish_plan_event({}, {"dataset": "t"})


# ── _get_dataset_reason tests ──────────────────────────────────────


class TestGetDatasetReason:
    """P19 _get_dataset_reason [Code]"""

    def test_exact_match(self):
        from superset.ai.graph.nodes_parent import _get_dataset_reason

        reason = _get_dataset_reason(
            {"target_table": "birth_names"},
            {"table_name": "birth_names"},
        )
        assert "精确匹配" in reason

    def test_partial_match(self):
        from superset.ai.graph.nodes_parent import _get_dataset_reason

        reason = _get_dataset_reason(
            {"target_table": "birth"},
            {"table_name": "birth_names"},
        )
        assert "部分匹配" in reason

    def test_no_target(self):
        from superset.ai.graph.nodes_parent import _get_dataset_reason

        reason = _get_dataset_reason({}, {"table_name": "birth_names"})
        assert "自动选择" in reason


# ── _describe_time_range tests ─────────────────────────────────────


class TestDescribeTimeRange:
    """P19 _describe_time_range [Code]"""

    def test_main_dttm_col(self):
        from superset.ai.graph.nodes_parent import _describe_time_range

        text = _describe_time_range({
            "datetime_cols": ["ds"],
            "main_dttm_col": "ds",
        })
        assert "ds" in text

    def test_no_time_cols(self):
        from superset.ai.graph.nodes_parent import _describe_time_range

        text = _describe_time_range({"datetime_cols": []})
        assert "未找到时间列" in text

    def test_fallback_to_datetime_cols(self):
        from superset.ai.graph.nodes_parent import _describe_time_range

        text = _describe_time_range({
            "datetime_cols": ["created_at", "updated_at"],
            "main_dttm_col": None,
        })
        assert "created_at" in text


# ── _extract_assumptions tests ─────────────────────────────────────


class TestExtractAssumptions:
    """P19 _extract_assumptions [Code]"""

    def test_metric_assumption(self):
        from superset.ai.graph.nodes_parent import _extract_assumptions

        assumptions = _extract_assumptions(
            {"time_hint": None},
            {"metric_cols": ["amount"], "datetime_cols": ["ds"]},
            [],
        )
        assert any("amount" in a for a in assumptions)

    def test_no_time_hint_with_datetime(self):
        from superset.ai.graph.nodes_parent import _extract_assumptions

        assumptions = _extract_assumptions(
            {},
            {"metric_cols": [], "datetime_cols": ["ds"]},
            [],
        )
        assert any("时间范围" in a for a in assumptions)

    def test_low_match_score_risk(self):
        from superset.ai.graph.nodes_parent import _extract_assumptions

        assumptions = _extract_assumptions(
            {"dataset_match_score": 30},
            {"metric_cols": [], "datetime_cols": []},
            [],
        )
        assert any("置信度" in a for a in assumptions)

    def test_multi_dataset_risk(self):
        from superset.ai.graph.nodes_parent import _extract_assumptions

        assumptions = _extract_assumptions(
            {"multi_dataset": True, "target_tables": ["orders", "users"]},
            {"metric_cols": [], "datetime_cols": []},
            [],
        )
        assert any("orders" in a for a in assumptions)

    def test_capped_at_five(self):
        from superset.ai.graph.nodes_parent import _extract_assumptions

        # All signals triggered should still cap at 5
        assumptions = _extract_assumptions(
            {
                "multi_dataset": True,
                "target_tables": ["a", "b"],
                "dataset_match_score": 20,
                "time_hint": None,
            },
            {"metric_cols": ["m1"], "datetime_cols": ["ds"]},
            [],
        )
        assert len(assumptions) <= 5
