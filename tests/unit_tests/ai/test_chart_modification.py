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
"""Unit tests for Phase 14: chart modification nodes."""

from unittest.mock import MagicMock, patch

from superset.utils import json


# ── classify_intent tests ─────────────────────────────────────────


class TestClassifyIntent:
    """P0 classify_intent [Code + optional LLM]"""

    def test_no_history_routes_to_parse_request(self):
        from superset.ai.graph.nodes_parent import classify_intent

        state = {"request": "创建销售趋势图", "previous_charts": []}
        result = classify_intent(state)

        assert result.goto == "parse_request"

    def test_no_modify_keywords_routes_to_parse_request(self):
        from superset.ai.graph.nodes_parent import classify_intent

        state = {
            "request": "帮我做一个趋势图",
            "previous_charts": [{"chart_id": 100}],
        }
        result = classify_intent(state)

        assert result.goto == "parse_request"

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_modify_with_history_routes_to_load(self, mock_llm):
        from superset.ai.graph.nodes_parent import classify_intent

        mock_llm.return_value = {"intent": "modify"}
        state = {
            "request": "改成折线图",
            "previous_charts": [{"chart_id": 847}],
        }
        result = classify_intent(state)

        assert result.goto == "load_existing_chart"
        mock_llm.assert_called_once()

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_llm_says_new_routes_to_parse_request(self, mock_llm):
        from superset.ai.graph.nodes_parent import classify_intent

        mock_llm.return_value = {"intent": "new"}
        state = {
            "request": "改成柱状图",  # has modify keyword but LLM says new
            "previous_charts": [{"chart_id": 847}],
        }
        result = classify_intent(state)

        assert result.goto == "parse_request"

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_llm_failure_falls_back_to_new(self, mock_llm):
        from superset.ai.graph.nodes_parent import classify_intent

        mock_llm.side_effect = ValueError("error")
        state = {
            "request": "改成柱状图",
            "previous_charts": [{"chart_id": 847}],
        }
        result = classify_intent(state)

        assert result.goto == "parse_request"

    def test_chinese_modify_keywords(self):
        """Test that Chinese modify keywords trigger LLM call."""
        from superset.ai.graph.nodes_parent import classify_intent

        with patch("superset.ai.graph.nodes_parent.llm_call_json") as mock_llm:
            mock_llm.return_value = {"intent": "modify"}
            for kw in ["改成", "换成", "修改", "更新", "变成"]:
                state = {
                    "request": f"帮我{kw}折线图",
                    "previous_charts": [{"chart_id": 1}],
                }
                result = classify_intent(state)
                assert result.goto == "load_existing_chart", f"Failed for keyword: {kw}"


# ── load_existing_chart tests ─────────────────────────────────────


class TestLoadExistingChart:
    """P0b load_existing_chart [Code]"""

    def test_no_previous_routes_to_parse_request(self):
        from superset.ai.graph.nodes_parent import load_existing_chart

        state = {"previous_charts": []}
        result = load_existing_chart(state)

        assert result.goto == "parse_request"

    @patch("superset.extensions.security_manager")
    @patch("superset.db")
    def test_loads_chart_from_db(self, mock_db, mock_sm):
        from superset.ai.graph.nodes_parent import load_existing_chart

        mock_slice = MagicMock()
        mock_slice.id = 847
        mock_slice.slice_name = "销售趋势"
        mock_slice.viz_type = "echarts_timeseries_bar"
        mock_slice.params = json.dumps({
            "viz_type": "echarts_timeseries_bar",
            "metrics": ["SUM(num)"],
        })
        mock_slice.datasource_id = 1
        mock_db.session.get.return_value = mock_slice
        mock_sm.can_access.return_value = True

        state = {
            "previous_charts": [{"chart_id": 847}],
        }
        result = load_existing_chart(state)

        assert result.goto == "apply_chart_modification"
        assert result.update["existing_chart"]["chart_id"] == 847
        assert result.update["existing_chart"]["viz_type"] == "echarts_timeseries_bar"

    @patch("superset.db")
    def test_chart_not_found_routes_to_parse_request(self, mock_db):
        from superset.ai.graph.nodes_parent import load_existing_chart

        mock_db.session.get.return_value = None

        state = {
            "previous_charts": [{"chart_id": 999}],
        }
        result = load_existing_chart(state)

        assert result.goto == "parse_request"

    @patch("superset.extensions.security_manager")
    @patch("superset.db")
    def test_reference_chart_id_selects_specific(self, mock_db, mock_sm):
        from superset.ai.graph.nodes_parent import load_existing_chart

        mock_slice = MagicMock()
        mock_slice.id = 850
        mock_slice.slice_name = "性别分布"
        mock_slice.viz_type = "pie"
        mock_slice.params = "{}"
        mock_slice.datasource_id = 1
        mock_db.session.get.return_value = mock_slice
        mock_sm.can_access.return_value = True

        state = {
            "previous_charts": [{"chart_id": 847}, {"chart_id": 850}],
            "reference_chart_id": 850,
        }
        result = load_existing_chart(state)

        assert result.goto == "apply_chart_modification"
        assert result.update["existing_chart"]["chart_id"] == 850


# ── apply_chart_modification tests ────────────────────────────────


class TestApplyChartModification:
    """P0c apply_chart_modification [LLM]"""

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_viz_type_change(self, mock_llm):
        from superset.ai.graph.nodes_parent import apply_chart_modification

        mock_llm.return_value = {
            "viz_type": "echarts_timeseries_line",
            "slice_name": "销售趋势折线图",
            "param_changes": {},
        }
        state = {
            "existing_chart": {
                "chart_id": 847,
                "viz_type": "echarts_timeseries_bar",
                "slice_name": "销售趋势图",
                "form_data": {"viz_type": "echarts_timeseries_bar", "metrics": ["SUM(num)"]},
            },
            "request": "改成折线图",
        }
        result = apply_chart_modification(state)

        assert result.goto == "update_chart"
        mod = result.update["modification"]
        assert mod["new_viz_type"] == "echarts_timeseries_line"
        assert mod["new_form_data"]["viz_type"] == "echarts_timeseries_line"
        assert "SUM(num)" in str(mod["new_form_data"]["metrics"])

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_param_changes_merged(self, mock_llm):
        from superset.ai.graph.nodes_parent import apply_chart_modification

        mock_llm.return_value = {
            "viz_type": "echarts_timeseries_bar",
            "slice_name": "销售趋势图",
            "param_changes": {"groupby": ["state"]},
        }
        state = {
            "existing_chart": {
                "chart_id": 847,
                "viz_type": "echarts_timeseries_bar",
                "slice_name": "销售趋势图",
                "form_data": {"viz_type": "echarts_timeseries_bar", "metrics": ["SUM(num)"]},
            },
            "request": "加上按州分组",
        }
        result = apply_chart_modification(state)

        mod = result.update["modification"]
        assert mod["new_form_data"]["groupby"] == ["state"]
        assert mod["new_form_data"]["metrics"] == ["SUM(num)"]  # preserved

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_llm_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_parent import apply_chart_modification

        mock_llm.side_effect = ValueError("bad JSON")
        state = {
            "existing_chart": {
                "chart_id": 847,
                "viz_type": "bar",
                "form_data": {},
            },
            "request": "改成折线图",
        }
        result = apply_chart_modification(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "modify_parse_error"

    def test_no_existing_chart_routes_to_end(self):
        from superset.ai.graph.nodes_parent import apply_chart_modification

        state = {"request": "改成折线图"}
        result = apply_chart_modification(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_existing_chart"


# ── update_chart tests ────────────────────────────────────────────


class TestUpdateChart:
    """P0d update_chart [Code]"""

    @patch("superset.extensions.security_manager")
    @patch("superset.db")
    def test_success_updates_chart(self, mock_db, mock_sm):
        from superset.ai.graph.nodes_parent import update_chart

        mock_slice = MagicMock()
        mock_slice.id = 847
        mock_db.session.get.return_value = mock_slice
        mock_sm.can_access.return_value = True

        state = {
            "modification": {
                "chart_id": 847,
                "new_viz_type": "echarts_timeseries_line",
                "new_slice_name": "销售趋势折线图",
                "new_form_data": {"viz_type": "echarts_timeseries_line"},
            },
        }
        result = update_chart(state)

        assert result.goto == "__end__"
        chart = result.update["created_chart"]
        assert chart["action"] == "updated"
        assert chart["chart_id"] == 847
        mock_db.session.commit.assert_called_once()

    @patch("superset.db")
    def test_chart_not_found_routes_to_end(self, mock_db):
        from superset.ai.graph.nodes_parent import update_chart

        mock_db.session.get.return_value = None

        state = {
            "modification": {
                "chart_id": 999,
                "new_viz_type": "line",
                "new_slice_name": "test",
                "new_form_data": {},
            },
        }
        result = update_chart(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "chart_not_found"

    def test_no_chart_id_routes_to_end(self):
        from superset.ai.graph.nodes_parent import update_chart

        state = {"modification": {}}
        result = update_chart(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_chart_id"


# ── _extract_previous_charts tests ────────────────────────────────


class TestExtractPreviousCharts:
    """tasks.py helper for extracting previous charts from history."""

    def test_empty_history(self):
        from superset.ai.tasks import _extract_previous_charts

        assert _extract_previous_charts([]) == []

    def test_no_chart_summaries(self):
        from superset.ai.tasks import _extract_previous_charts

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ]
        assert _extract_previous_charts(history) == []

    def test_extracts_chart_summary(self):
        from superset.ai.tasks import _extract_previous_charts

        history = [
            {"role": "user", "content": "create chart"},
            {"role": "assistant", "content": "done"},
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({
                    "chart_id": 847,
                    "slice_name": "销售趋势",
                    "viz_type": "echarts_timeseries_bar",
                }),
            },
        ]
        result = _extract_previous_charts(history)
        assert len(result) == 1
        assert result[0]["chart_id"] == 847

    def test_returns_all_consecutive_charts(self):
        """Dashboard creates multiple charts — all should be extracted."""
        from superset.ai.tasks import _extract_previous_charts

        history = [
            {"role": "user", "content": "create dashboard"},
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({"chart_id": 100, "viz_type": "bar"}),
            },
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({"chart_id": 200, "viz_type": "line"}),
            },
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({"chart_id": 300, "viz_type": "pie"}),
            },
        ]
        result = _extract_previous_charts(history)
        assert len(result) == 3
        assert result[0]["chart_id"] == 100
        assert result[1]["chart_id"] == 200
        assert result[2]["chart_id"] == 300

    def test_stops_at_conversation_boundary(self):
        """Only charts from the most recent batch (before last user msg)."""
        from superset.ai.tasks import _extract_previous_charts

        history = [
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({"chart_id": 50, "viz_type": "bar"}),
            },
            {"role": "assistant", "content": "dashboard created"},
            {
                "role": "tool_summary",
                "tool": "create_chart",
                "content": json.dumps({"chart_id": 100, "viz_type": "line"}),
            },
        ]
        result = _extract_previous_charts(history)
        assert len(result) == 1
        assert result[0]["chart_id"] == 100  # only after the last boundary

    def test_ignores_other_tool_summaries(self):
        from superset.ai.tasks import _extract_previous_charts

        history = [
            {
                "role": "tool_summary",
                "tool": "execute_sql",
                "content": "SQL: SELECT 1",
            },
        ]
        assert _extract_previous_charts(history) == []
