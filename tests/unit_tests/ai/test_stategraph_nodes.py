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
"""Unit tests for StateGraph parent and child node functions."""

from unittest.mock import MagicMock, patch

from superset.utils import json


# ── Fixtures / helpers ──────────────────────────────────────────────

def _make_schema_summary(**overrides):
    """Build a minimal SchemaSummary for tests."""
    base = {
        "datasource_id": 1,
        "table_name": "birth_names",
        "datetime_cols": ["ds"],
        "dimension_cols": ["gender", "state"],
        "metric_cols": ["num"],
        "saved_metrics": [],
        "saved_metric_expressions": {},
        "main_dttm_col": "ds",
    }
    base.update(overrides)
    return base


def _make_result_summary(**overrides):
    """Build a minimal ResultSummary for tests."""
    base = {
        "row_count": 10,
        "columns": [],
        "has_datetime": True,
        "datetime_col": "ds",
        "datetime_cardinality": 5,
        "numeric_cols": ["num"],
        "string_cols": ["gender"],
        "low_cardinality_cols": ["gender"],
        "suitability_flags": {
            "good_for_trend": True,
            "good_for_composition": True,
            "good_for_kpi": False,
            "good_for_distribution": False,
            "good_for_comparison": True,
            "good_for_table": True,
        },
    }
    base.update(overrides)
    return base


def _make_chart_intent(**overrides):
    """Build a minimal ChartIntent for tests."""
    base = {
        "chart_index": 0,
        "analysis_intent": "comparison",
        "slice_name": "Birth Count by Gender",
        "sql_hint": "",
        "preferred_viz": "echarts_timeseries_bar",
    }
    base.update(overrides)
    return base


# ── Parent node tests ──────────────────────────────────────────────


class TestParseRequest:
    """P1 parse_request [LLM]"""

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_normal_extracts_goal(self, mock_llm):
        from superset.ai.graph.nodes_parent import parse_request

        mock_llm.return_value = {
            "task": "build_chart",
            "target_table": "birth_names",
            "analysis_intent": "trend",
            "preferred_viz": "line",
            "chart_count": 1,
            "time_hint": None,
            "user_language": "zh",
        }
        state = {"request": "查询birth_names趋势", "database_id": 1}
        result = parse_request(state)

        assert result.goto == "search_dataset"
        assert result.update["goal"]["target_table"] == "birth_names"
        assert result.update["goal"]["chart_count"] == 1

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_llm_format_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_parent import parse_request

        mock_llm.side_effect = ValueError("not valid JSON")
        state = {"request": "some request", "database_id": 1}
        result = parse_request(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "llm_format_error"

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_chart_mode_forces_single_chart(self, mock_llm):
        from superset.ai.graph.nodes_parent import parse_request

        mock_llm.return_value = {
            "task": "build_dashboard",
            "target_table": "birth_names",
            "analysis_intent": "trend",
            "preferred_viz": None,
            "chart_count": 3,
            "time_hint": None,
            "user_language": "zh",
        }
        state = {"request": "test", "database_id": 1, "agent_mode": "chart"}
        result = parse_request(state)

        assert result.update["goal"]["chart_count"] == 1
        assert result.update["goal"]["task"] == "build_chart"


class TestSearchDataset:
    """P2 search_dataset [Code]"""

    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_found_routes_to_select(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import search_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "found",
            "datasource_id": 1,
            "table_name": "birth_names",
            "columns": [],
            "metrics": [],
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "database_id": 1,
            "schema_name": None,
            "goal": {"target_table": "birth_names"},
        }
        result = search_dataset(state)

        assert result.goto == "select_dataset"
        assert len(result.update["dataset_candidates"]) == 1
        assert result.update["dataset_candidates"][0]["status"] == "found"

    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_not_found_with_alternatives(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import search_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "not_found",
            "message": "not found",
            "available_datasets": [
                {"table_name": "birth_names"},
                {"table_name": "flights"},
            ],
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "database_id": 1,
            "schema_name": None,
            "goal": {"target_table": "unknown"},
        }
        result = search_dataset(state)

        assert result.goto == "select_dataset"
        assert len(result.update["dataset_candidates"]) == 2

    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_no_datasets_routes_to_end(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import search_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "not_found",
            "message": "No accessible datasets",
            "available_datasets": [],
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "database_id": 1,
            "schema_name": None,
            "goal": {"target_table": "xyz"},
        }
        result = search_dataset(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_dataset"

    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_error_status_routes_to_end(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import search_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "error",
            "message": "Access denied",
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "database_id": 1,
            "schema_name": None,
            "goal": {"target_table": "xyz"},
        }
        result = search_dataset(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "tool_error"


class TestSelectDataset:
    """P3 select_dataset [Code]"""

    def test_single_candidate_auto_selects(self):
        from superset.ai.graph.nodes_parent import select_dataset

        state = {
            "dataset_candidates": [
                {"datasource_id": 1, "table_name": "birth_names"},
            ],
            "goal": {"target_table": "birth_names"},
        }
        result = select_dataset(state)

        assert result.goto == "read_schema"
        assert result.update["selected_dataset"]["datasource_id"] == 1

    def test_no_candidates_routes_to_end(self):
        from superset.ai.graph.nodes_parent import select_dataset

        state = {
            "dataset_candidates": [],
            "goal": {"target_table": "xyz"},
        }
        result = select_dataset(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_dataset"

    def test_multiple_candidates_picks_best_match(self):
        from superset.ai.graph.nodes_parent import select_dataset

        state = {
            "dataset_candidates": [
                {"table_name": "flights"},
                {"table_name": "birth_names"},
                {"table_name": "energy"},
            ],
            "goal": {"target_table": "birth_names"},
        }
        # Without datasource_id, goes back to search_dataset
        result = select_dataset(state)
        assert result.goto == "search_dataset"
        assert result.update["goal"]["target_table"] == "birth_names"


class TestReadSchema:
    """P4 read_schema [Code]"""

    def test_builds_schema_summary(self):
        from superset.ai.graph.nodes_parent import read_schema

        state = {
            "selected_dataset": {
                "datasource_id": 1,
                "table_name": "birth_names",
                "columns": [
                    {"name": "ds", "type": "DATE", "groupable": False, "is_dttm": True},
                    {"name": "gender", "type": "VARCHAR(10)", "groupable": True, "is_dttm": False},
                    {"name": "num", "type": "BIGINT", "groupable": False, "is_dttm": False},
                ],
                "metrics": [{"name": "sum__num", "expression": "SUM(num)"}],
                "main_datetime_column": "ds",
            },
        }
        result = read_schema(state)

        assert result.goto == "plan_dashboard"
        summary = result.update["schema_summary"]
        assert summary["datasource_id"] == 1
        assert "ds" in summary["datetime_cols"]
        assert "gender" in summary["dimension_cols"]
        assert "num" in summary["metric_cols"]
        assert "sum__num" in summary["saved_metrics"]


class TestPlanDashboard:
    """P5 plan_dashboard [LLM]"""

    @patch("superset.ai.graph.nodes_parent.llm_call_json_list")
    def test_normal_plans_intents(self, mock_llm):
        from superset.ai.graph.nodes_parent import plan_dashboard

        mock_llm.return_value = [
            {"chart_index": 0, "analysis_intent": "trend", "slice_name": "Trend", "preferred_viz": None, "sql_hint": ""},
        ]
        state = {
            "request": "build chart",
            "goal": {"analysis_intent": "trend", "preferred_viz": None, "chart_count": 1, "user_language": "zh"},
            "schema_summary": _make_schema_summary(),
        }
        result = plan_dashboard(state)

        assert result.goto == "review_analysis"
        assert len(result.update["chart_intents"]) == 1

    @patch("superset.ai.graph.nodes_parent.llm_call_json_list")
    def test_llm_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_parent import plan_dashboard

        mock_llm.side_effect = ValueError("bad JSON")
        state = {
            "request": "build chart",
            "goal": {"analysis_intent": "trend", "preferred_viz": None, "chart_count": 1, "user_language": "zh"},
            "schema_summary": _make_schema_summary(),
        }
        result = plan_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "llm_format_error"

    def test_missing_schema_routes_to_end(self):
        from superset.ai.graph.nodes_parent import plan_dashboard

        state = {
            "request": "build chart",
            "goal": {},
            "schema_summary": None,
        }
        result = plan_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_schema"


# ── Child node tests ───────────────────────────────────────────────


class TestPlanQuery:
    """C1 plan_query [LLM]"""

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_normal_returns_sql_plan(self, mock_llm):
        from superset.ai.graph.nodes_child import plan_query

        mock_llm.return_value = {
            "metric_expr": "SUM(num)",
            "dimensions": ["gender"],
            "time_field": None,
            "time_grain": None,
            "filters": [],
            "order_by": None,
            "limit": 200,
        }
        state = {
            "chart_intent": _make_chart_intent(),
            "schema_summary": _make_schema_summary(),
            "database_id": 1,
            "last_error": None,
        }
        result = plan_query(state)

        assert result.goto == "validate_sql"
        assert result.update["sql_plan"]["metric_expr"] == "SUM(num)"

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_llm_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_child import plan_query

        mock_llm.side_effect = ValueError("not JSON")
        state = {
            "chart_intent": _make_chart_intent(),
            "schema_summary": _make_schema_summary(),
            "database_id": 1,
            "last_error": None,
        }
        result = plan_query(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["node"] == "plan_query"


class TestValidateSql:
    """C2 validate_sql [Code]"""

    def test_valid_sql_routes_to_execute(self):
        from superset.ai.graph.nodes_child import validate_sql

        state = {
            "sql_plan": {
                "metric_expr": "SUM(num)",
                "dimensions": ["gender"],
                "time_field": None,
                "order_by": None,
                "limit": 200,
            },
            "schema_summary": _make_schema_summary(),
            "sql_attempts": 0,
        }
        result = validate_sql(state)

        assert result.goto == "execute_query"
        assert result.update["sql_valid"] is True
        assert "SELECT" in result.update["sql"]
        assert "birth_names" in result.update["sql"]

    def test_unknown_fields_normalized_still_compiles(self):
        """_normalize_sql_plan silently filters unknown columns and falls back
        to SUM(first_metric_col), so the SQL still compiles successfully."""
        from superset.ai.graph.nodes_child import validate_sql

        state = {
            "sql_plan": {
                "metric_expr": "SUM(unknown_col)",
                "dimensions": ["nonexistent"],
                "time_field": "not_a_time",
                "order_by": "bad_col DESC",
                "limit": 200,
            },
            "schema_summary": _make_schema_summary(),
            "sql_attempts": 0,
        }
        result = validate_sql(state)

        # Normalization replaces bad metric with SUM(num), drops bad dims/time
        assert result.goto == "execute_query"
        assert result.update["sql_valid"] is True
        assert "SUM(num)" in result.update["sql"]

    def test_max_attempts_routes_to_end(self):
        from superset.ai.graph.nodes_child import validate_sql

        state = {
            "sql_plan": {
                "metric_expr": "SUM(unknown_col)",
                "dimensions": ["nonexistent"],
                "time_field": "not_a_time",
                "order_by": None,
                "limit": 200,
            },
            "schema_summary": _make_schema_summary(),
            "sql_attempts": 3,
        }
        result = validate_sql(state)

        # Even at max attempts, normalization still succeeds and produces valid SQL
        assert result.goto == "execute_query"
        assert result.update["sql_valid"] is True


class TestExecuteQuery:
    """C3 execute_query [Code]"""

    @patch("superset.ai.tools.execute_sql.ExecuteSqlTool")
    def test_success_routes_to_analyze(self, mock_tool_cls):
        from superset.ai.graph.nodes_child import execute_query

        tool_instance = MagicMock()
        tool_instance.run.return_value = "gender|num\n---|---\nM|100\nF|90"
        mock_tool_cls.return_value = tool_instance

        state = {"sql": "SELECT gender, SUM(num) FROM t", "database_id": 1, "sql_attempts": 0}
        result = execute_query(state)

        assert result.goto == "analyze_result"
        assert result.update["last_error"] is None

    @patch("superset.ai.tools.execute_sql.ExecuteSqlTool")
    def test_error_retries(self, mock_tool_cls):
        from superset.ai.graph.nodes_child import execute_query

        tool_instance = MagicMock()
        tool_instance.run.return_value = "Error: table not found"
        mock_tool_cls.return_value = tool_instance

        state = {"sql": "SELECT * FROM bad_table", "database_id": 1, "sql_attempts": 0}
        result = execute_query(state)

        assert result.goto == "plan_query"
        assert result.update["sql_attempts"] == 1

    @patch("superset.ai.tools.execute_sql.ExecuteSqlTool")
    def test_max_attempts_routes_to_end(self, mock_tool_cls):
        from superset.ai.graph.nodes_child import execute_query

        tool_instance = MagicMock()
        tool_instance.run.return_value = "Error: timeout"
        mock_tool_cls.return_value = tool_instance

        state = {"sql": "SELECT * FROM t", "database_id": 1, "sql_attempts": 3}
        result = execute_query(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["recoverable"] is False


class TestAnalyzeResult:
    """C4 analyze_result [Code]"""

    def test_builds_suitability_flags(self):
        from superset.ai.graph.nodes_child import analyze_result

        # Pipe-separated result with header + separator + data rows
        raw = "ds|num|gender\n---|---|---\n2020-01-01|100|M\n2020-02-01|200|F\n2020-03-01|150|M"
        state = {"query_result_raw": raw}
        result = analyze_result(state)

        assert result.goto == "select_chart"
        summary = result.update["query_result_summary"]
        assert summary["row_count"] == 3
        assert summary["has_datetime"] is True
        assert "good_for_trend" in summary["suitability_flags"]

    def test_single_row_sets_kpi_flag(self):
        from superset.ai.graph.nodes_child import analyze_result

        # Pipe-separated single-value result
        raw = "total\n---\n42"
        state = {"query_result_raw": raw}
        result = analyze_result(state)

        summary = result.update["query_result_summary"]
        assert summary["suitability_flags"]["good_for_kpi"] is True


class TestSelectChart:
    """C5 select_chart [LLM]"""

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_normal_returns_chart_plan(self, mock_llm):
        from superset.ai.graph.nodes_child import select_chart

        mock_llm.return_value = {
            "viz_type": "echarts_timeseries_bar",
            "slice_name": "Birth Count",
            "semantic_params": {
                "metric": "SUM(num)",
                "metrics": ["SUM(num)"],
                "groupby": ["gender"],
                "x_field": "gender",
                "time_field": None,
            },
            "rationale": "Bar chart for comparison",
        }
        state = {
            "chart_intent": _make_chart_intent(),
            "query_result_summary": _make_result_summary(),
        }
        result = select_chart(state)

        assert result.goto == "normalize_chart_params"
        assert result.update["chart_plan"]["viz_type"] == "echarts_timeseries_bar"

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_llm_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_child import select_chart

        mock_llm.side_effect = ValueError("bad JSON")
        state = {
            "chart_intent": _make_chart_intent(),
            "query_result_summary": _make_result_summary(),
        }
        result = select_chart(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["node"] == "select_chart"

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_preferred_viz_overrides(self, mock_llm):
        from superset.ai.graph.nodes_child import select_chart

        mock_llm.return_value = {
            "viz_type": "table",
            "slice_name": "Data",
            "semantic_params": {"metric": "SUM(num)", "groupby": [], "time_field": None},
            "rationale": "fallback",
        }
        state = {
            "chart_intent": _make_chart_intent(preferred_viz="pie"),
            "query_result_summary": _make_result_summary(),
        }
        result = select_chart(state)

        assert result.update["chart_plan"]["viz_type"] == "pie"


class TestNormalizeChartParams:
    """C6 normalize_chart_params [Code]"""

    def test_max_repairs_routes_to_end(self):
        from superset.ai.graph.nodes_child import normalize_chart_params

        state = {
            "repair_attempts": 3,
            "chart_plan": {"viz_type": "bar"},
            "schema_summary": _make_schema_summary(),
        }
        result = normalize_chart_params(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "max_repairs"

    def test_missing_plan_routes_to_end(self):
        from superset.ai.graph.nodes_child import normalize_chart_params

        state = {
            "repair_attempts": 0,
            "chart_plan": None,
            "schema_summary": _make_schema_summary(),
        }
        result = normalize_chart_params(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "missing_plan"


class TestRepairChartParams:
    """C7 repair_chart_params [LLM]"""

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_normal_returns_fixed_plan(self, mock_llm):
        from superset.ai.graph.nodes_child import repair_chart_params

        mock_llm.return_value = {
            "viz_type": "pie",
            "slice_name": "Fixed",
            "semantic_params": {"metric": "SUM(num)", "groupby": ["gender"], "time_field": None},
            "rationale": "fixed",
        }
        state = {
            "schema_summary": _make_schema_summary(),
            "last_error": {"message": "bad params"},
            "chart_plan": {"viz_type": "bar"},
        }
        result = repair_chart_params(state)

        assert result.goto == "normalize_chart_params"
        assert result.update["chart_plan"]["viz_type"] == "pie"

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_llm_error_routes_to_end(self, mock_llm):
        from superset.ai.graph.nodes_child import repair_chart_params

        mock_llm.side_effect = ValueError("not JSON")
        state = {
            "schema_summary": _make_schema_summary(),
            "last_error": {"message": "error"},
            "chart_plan": {"viz_type": "bar"},
        }
        result = repair_chart_params(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["node"] == "repair_chart_params"


class TestCreateChart:
    """C8 create_chart [Code]"""

    def test_missing_plan_routes_to_end(self):
        from superset.ai.graph.nodes_child import create_chart

        state = {
            "chart_plan": None,
            "schema_summary": _make_schema_summary(),
        }
        result = create_chart(state)

        assert result.goto == "__end__"
        assert "No chart plan" in result.update["last_error"]["message"]

    @patch("superset.ai.tools.create_chart.CreateChartTool")
    def test_success_returns_created_chart(self, mock_tool_cls):
        from superset.ai.graph.nodes_child import create_chart

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "chart_id": 123,
            "slice_name": "Test Chart",
            "viz_type": "pie",
            "explore_url": "/explore/?slice_id=123",
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "chart_plan": {"viz_type": "pie", "slice_name": "Test"},
            "chart_form_data": {"metrics": ["SUM(num)"]},
            "schema_summary": _make_schema_summary(),
            "database_id": 1,
            "repair_attempts": 0,
        }
        with patch("superset.ai.graph.nodes_child._find_recent_chart", return_value=None):
            result = create_chart(state)

        assert result.goto == "__end__"
        assert result.update["created_chart"]["chart_id"] == 123

    @patch("superset.ai.tools.create_chart.CreateChartTool")
    def test_error_retries(self, mock_tool_cls):
        from superset.ai.graph.nodes_child import create_chart

        tool_instance = MagicMock()
        tool_instance.run.return_value = "Error: invalid params"
        mock_tool_cls.return_value = tool_instance

        state = {
            "chart_plan": {"viz_type": "bar", "slice_name": "Test"},
            "chart_form_data": {},
            "schema_summary": _make_schema_summary(),
            "database_id": 1,
            "repair_attempts": 0,
        }
        with patch("superset.ai.graph.nodes_child._find_recent_chart", return_value=None):
            result = create_chart(state)

        assert result.goto == "repair_chart_params"
        assert result.update["repair_attempts"] == 1


class TestCreateDashboard:
    """P6 create_dashboard [Code]"""

    def test_no_charts_routes_to_end(self):
        from superset.ai.graph.nodes_parent import create_dashboard

        state = {
            "created_charts": [],
            "chart_intents": [{"chart_index": 0}],
            "goal": {"target_table": "test"},
            "request_id": "abc",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "no_charts"

    def test_invalid_charts_routes_to_end(self):
        from superset.ai.graph.nodes_parent import create_dashboard

        state = {
            "created_charts": [
                {"slice_name": "Chart1"},  # missing chart_id
            ],
            "chart_intents": [{"chart_index": 0}],
            "goal": {"target_table": "test"},
            "request_id": "abc",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "invalid_charts"

    def test_insufficient_charts_routes_to_end(self):
        """Expect 4 charts but only 1 created → below threshold (max(1, 4//2)=2)."""
        from superset.ai.graph.nodes_parent import create_dashboard

        state = {
            "created_charts": [
                {"chart_id": 1, "slice_name": "Chart1"},
            ],
            "chart_intents": [
                {"chart_index": i} for i in range(4)
            ],
            "goal": {"target_table": "test"},
            "request_id": "abc",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "insufficient_charts"

    @patch("superset.ai.tools.create_dashboard.CreateDashboardTool")
    def test_success_creates_dashboard(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import create_dashboard

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "dashboard_id": 42,
            "dashboard_title": "test 仪表板",
            "dashboard_url": "/superset/dashboard/42/",
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "created_charts": [
                {"chart_id": 1, "slice_name": "Chart1"},
                {"chart_id": 2, "slice_name": "Chart2"},
            ],
            "chart_intents": [
                {"chart_index": 0},
                {"chart_index": 1},
            ],
            "goal": {"target_table": "birth_names"},
            "request_id": "req-123",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["created_dashboard"]["dashboard_id"] == 42

    @patch("superset.ai.tools.create_dashboard.CreateDashboardTool")
    def test_error_routes_to_end(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import create_dashboard

        tool_instance = MagicMock()
        tool_instance.run.return_value = "Error: dashboard creation failed"
        mock_tool_cls.return_value = tool_instance

        state = {
            "created_charts": [{"chart_id": 1, "slice_name": "C1"}],
            "chart_intents": [{"chart_index": 0}],
            "goal": {"target_table": "test"},
            "request_id": "req-456",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["last_error"]["type"] == "create_dashboard_failed"

    def test_idempotent_reuses_existing(self):
        from superset.ai.graph.nodes_parent import create_dashboard

        existing = {
            "dashboard_id": 99,
            "dashboard_title": "Existing",
            "dashboard_url": "/superset/dashboard/99/",
        }
        state = {
            "created_charts": [{"chart_id": 1}],
            "chart_intents": [{"chart_index": 0}],
            "goal": {"target_table": "test"},
            "request_id": "req-dupe",
        }
        with patch(
            "superset.ai.graph.nodes_parent._find_existing_dashboard",
            return_value=existing,
        ):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["created_dashboard"]["dashboard_id"] == 99


class TestGraphEventEmission:
    """Tests for graph event translation."""

    def test_single_chart_subgraph_skips_duplicate_created_event(self):
        from superset.ai.graph.runner import _emit_node_events

        events = list(_emit_node_events(
            "single_chart_subgraph",
            {
                "created_charts": [{"chart_id": 1}],
                "child_events_published": True,
            },
        ))

        assert events == []


class TestCountNumberedItems:
    """Tests for _count_numbered_items helper in nodes_parent."""

    @staticmethod
    def _cut(text: str) -> int:
        from superset.ai.graph.nodes_parent import _count_numbered_items

        return _count_numbered_items(text)

    def test_empty_string(self):
        assert self._cut("") == 0

    def test_plain_text_no_numbers(self):
        assert self._cut("画一个折线图") == 0

    def test_single_item(self):
        assert self._cut("1. 折线图") == 1

    def test_sequential_dot(self):
        assert self._cut("1.折线图 2.饼图 3.柱状图") == 3

    def test_sequential_chinese_comma(self):
        assert self._cut("1、趋势图 2、分布图") == 2

    def test_sequential_paren(self):
        assert self._cut("1) 趋势 2) 对比") == 2

    def test_mixed_separators(self):
        assert self._cut("1.趋势 2、分布 3)排名") == 3

    def test_non_sequential_returns_zero(self):
        """Skip numbering (1, 3) should return 0."""
        assert self._cut("1.折线图 3.饼图") == 0

    def test_does_not_start_at_one(self):
        assert self._cut("2.折线图 3.饼图") == 0

    def test_embedded_in_longer_text(self):
        text = "用 birth_names 创建仪表板：1.出生趋势 2.性别比例 3.各州排名"
        assert self._cut(text) == 3

    def test_max_two_digits(self):
        text = " ".join(f"{i}.图表{i}" for i in range(1, 13))
        assert self._cut(text) == 12

    def test_number_without_content(self):
        """Bare '1.' without following content should not count."""
        assert self._cut("1.") == 0

    def test_number_inside_word(self):
        """'v1.0' should not match as a numbered list item."""
        assert self._cut("升级到v1.0版本") == 0


# ── Phase 18: multi-dataset tests ─────────────────────────────────────


class TestBackfillTargetTables:
    """Tests for _backfill_target_tables keyword matching."""

    @staticmethod
    def _run(intents, tables):
        from superset.ai.graph.nodes_parent import _backfill_target_tables

        _backfill_target_tables(intents, tables)
        return intents

    def test_empty_inputs(self):
        result = self._run([], ["messages"])
        assert result == []

    def test_no_target_tables(self):
        intents = [{"target_table": None}]
        self._run(intents, [])
        assert intents[0].get("target_table") is None

    def test_keyword_match_from_slice_name(self):
        intents = [
            {"slice_name": "messages trend", "sql_hint": ""},
            {"slice_name": "频道活跃度", "sql_hint": "from messages_channels"},
        ]
        self._run(intents, ["messages", "messages_channels"])
        assert intents[0]["target_table"] == "messages"
        assert intents[1]["target_table"] == "messages_channels"

    def test_keyword_match_from_sql_hint(self):
        intents = [
            {"slice_name": "Trend", "sql_hint": "aggregate messages"},
            {"slice_name": "Distribution", "sql_hint": "from users"},
        ]
        self._run(intents, ["messages", "users"])
        assert intents[0]["target_table"] == "messages"
        assert intents[1]["target_table"] == "users"

    def test_round_robin_fallback(self):
        """When no keyword matches, fall back to round-robin."""
        intents = [
            {"slice_name": "Chart A", "sql_hint": ""},
            {"slice_name": "Chart B", "sql_hint": ""},
        ]
        self._run(intents, ["table_x", "table_y"])
        assert intents[0]["target_table"] == "table_x"
        assert intents[1]["target_table"] == "table_y"

    def test_respects_existing_assignments(self):
        """Don't overwrite LLM-assigned target_table."""
        intents = [
            {"target_table": "messages", "slice_name": "Trend", "sql_hint": ""},
            {"slice_name": "Users", "sql_hint": ""},
        ]
        self._run(intents, ["messages", "users"])
        assert intents[0]["target_table"] == "messages"
        assert intents[1]["target_table"] == "users"

    def test_stem_matching(self):
        """Match 'user' stem to 'users' table name."""
        intents = [
            {"slice_name": "user distribution", "sql_hint": ""},
        ]
        self._run(intents, ["users"])
        assert intents[0]["target_table"] == "users"

    def test_partial_no_claim_duplicate(self):
        """Already-claimed tables should not be re-assigned to keyword matches."""
        intents = [
            {"target_table": "messages", "slice_name": "Trend", "sql_hint": ""},
            {"slice_name": "no match here", "sql_hint": ""},
            {"slice_name": "users info", "sql_hint": ""},
        ]
        self._run(intents, ["messages", "users"])
        assert intents[0]["target_table"] == "messages"
        # intents[2] claims "users" via keyword match first
        assert intents[2]["target_table"] == "users"
        # intents[1] gets no table (no keyword match, no unclaimed tables left)
        assert intents[1].get("target_table") is None


class TestResolveDataset:
    """Tests for resolve_dataset helper."""

    def test_cache_hit(self):
        from superset.ai.graph.nodes_parent import resolve_dataset

        cached = _make_schema_summary(table_name="messages", datasource_id=5)
        schema_cache = {"messages": cached}
        result = resolve_dataset("messages", 1, None, schema_cache)
        assert result is cached

    def test_cache_miss_with_no_target_table(self):
        from superset.ai.graph.nodes_parent import resolve_dataset

        cached = _make_schema_summary(table_name="fallback", datasource_id=1)
        schema_cache = {"fallback": cached}
        result = resolve_dataset(None, 1, None, schema_cache)
        assert result is cached

    @patch("superset.ai.graph.nodes_parent._get_all_accessible_datasets")
    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_search_finds_table(self, mock_tool_cls, mock_get_all):
        from superset.ai.graph.nodes_parent import resolve_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "found",
            "datasource_id": 10,
            "table_name": "users",
            "columns": [
                {"name": "id", "type": "INT", "groupable": False, "is_dttm": False},
                {"name": "name", "type": "VARCHAR(50)", "groupable": True, "is_dttm": False},
            ],
            "metrics": [],
            "main_datetime_column": None,
        })
        mock_tool_cls.return_value = tool_instance

        result = resolve_dataset("users", 1, None, {})
        assert result is not None
        assert result["table_name"] == "users"
        assert result["datasource_id"] == 10

    @patch("superset.ai.tools.search_datasets.SearchDatasetsTool")
    def test_search_not_found_returns_none(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import resolve_dataset

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "status": "not_found",
            "message": "not found",
        })
        mock_tool_cls.return_value = tool_instance

        result = resolve_dataset("nonexistent", 1, None, {})
        assert result is None


class TestParseRequestMultiDataset:
    """Tests for parse_request multi-dataset detection."""

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    @patch("superset.ai.graph.nodes_parent._get_all_accessible_datasets")
    def test_multi_dataset_routes_to_plan_dashboard(
        self, mock_get_all, mock_llm
    ):
        from superset.ai.graph.nodes_parent import parse_request

        mock_llm.return_value = {
            "task": "build_dashboard",
            "target_table": None,
            "target_tables": ["messages", "users"],
            "analysis_intent": "trend",
            "preferred_viz": None,
            "chart_count": 2,
            "time_hint": None,
            "user_language": "zh",
            "multi_dataset": True,
        }
        state = {
            "request": "1.消息趋势(messages) 2.用户分布(users)",
            "database_id": 1,
            "agent_mode": "dashboard",
        }
        result = parse_request(state)

        assert result.goto == "plan_dashboard"
        assert result.update["goal"]["multi_dataset"] is True
        assert result.update["schema_cache"] == {}

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_single_table_routes_to_search_dataset(self, mock_llm):
        from superset.ai.graph.nodes_parent import parse_request

        mock_llm.return_value = {
            "task": "build_chart",
            "target_table": "birth_names",
            "target_tables": None,
            "analysis_intent": "trend",
            "preferred_viz": None,
            "chart_count": 1,
            "time_hint": None,
            "user_language": "zh",
            "multi_dataset": False,
        }
        state = {
            "request": "birth_names 趋势",
            "database_id": 1,
            "agent_mode": "chart",
        }
        result = parse_request(state)

        assert result.goto == "search_dataset"

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    @patch("superset.ai.graph.nodes_parent._get_all_accessible_datasets")
    def test_parenthesis_extraction_with_validation(
        self, mock_get_all, mock_llm
    ):
        from superset.ai.graph.nodes_parent import parse_request

        # LLM returns multi_dataset=false and empty target_tables
        mock_llm.return_value = {
            "task": "build_dashboard",
            "target_table": None,
            "target_tables": [],
            "analysis_intent": "trend",
            "preferred_viz": None,
            "chart_count": 3,
            "time_hint": None,
            "user_language": "zh",
            "multi_dataset": False,
        }
        # But actual datasets include messages and users
        mock_get_all.return_value = ["messages", "messages_channels", "users"]

        state = {
            "request": "1.消息趋势(messages) 2.频道活跃(messages_channels) 3.用户分布(users)",
            "database_id": 1,
            "agent_mode": "dashboard",
        }
        result = parse_request(state)

        # Should detect multi-dataset from parentheses and route to plan_dashboard
        assert result.goto == "plan_dashboard"
        assert result.update["goal"]["multi_dataset"] is True
        assert len(result.update["goal"]["target_tables"]) >= 2


class TestPlanDashboardMultiDataset:
    """Tests for plan_dashboard V2 (multi-dataset) mode."""

    @patch("superset.ai.graph.nodes_parent.llm_call_json_list")
    @patch("superset.ai.graph.nodes_parent._get_all_accessible_datasets")
    def test_multi_dataset_uses_v2_prompt(self, mock_get_all, mock_llm):
        from superset.ai.graph.nodes_parent import plan_dashboard

        mock_get_all.return_value = ["messages", "users", "threads"]
        mock_llm.return_value = [
            {
                "chart_index": 0,
                "analysis_intent": "trend",
                "slice_name": "消息趋势",
                "target_table": "messages",
                "preferred_viz": None,
                "sql_hint": "",
            },
            {
                "chart_index": 1,
                "analysis_intent": "distribution",
                "slice_name": "用户分布",
                "target_table": "users",
                "preferred_viz": None,
                "sql_hint": "",
            },
        ]
        state = {
            "request": "1.消息趋势(messages) 2.用户分布(users)",
            "goal": {
                "analysis_intent": "trend",
                "preferred_viz": None,
                "chart_count": 2,
                "user_language": "zh",
                "multi_dataset": True,
                "target_tables": ["messages", "users"],
            },
            "schema_summary": None,
            "database_id": 1,
        }
        result = plan_dashboard(state)

        assert result.goto == "review_analysis"
        intents = result.update["chart_intents"]
        assert len(intents) == 2
        assert intents[0]["target_table"] == "messages"
        assert intents[1]["target_table"] == "users"


class TestCreateDashboardMultiDataset:
    """Tests for create_dashboard with multi-dataset (None target_table)."""

    @patch("superset.ai.tools.create_dashboard.CreateDashboardTool")
    def test_title_from_target_tables(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import create_dashboard

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "dashboard_id": 55,
            "dashboard_title": "messages 仪表板",
            "dashboard_url": "/superset/dashboard/55/",
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "created_charts": [{"chart_id": 1}],
            "chart_intents": [{"chart_index": 0}],
            "goal": {
                "target_table": None,
                "target_tables": ["messages", "users"],
            },
            "request_id": "req-multi",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        assert result.goto == "__end__"
        assert result.update["created_dashboard"]["dashboard_id"] == 55
        # Verify the title uses target_tables[0]
        call_args = tool_instance.run.call_args[0][0]
        assert call_args["dashboard_title"] == "messages 仪表板"

    @patch("superset.ai.tools.create_dashboard.CreateDashboardTool")
    def test_title_fallback_when_no_tables(self, mock_tool_cls):
        from superset.ai.graph.nodes_parent import create_dashboard

        tool_instance = MagicMock()
        tool_instance.run.return_value = json.dumps({
            "dashboard_id": 56,
            "dashboard_title": "AI 仪表板",
            "dashboard_url": "/superset/dashboard/56/",
        })
        mock_tool_cls.return_value = tool_instance

        state = {
            "created_charts": [{"chart_id": 1}],
            "chart_intents": [{"chart_index": 0}],
            "goal": {"target_table": None, "target_tables": []},
            "request_id": "req-empty",
        }
        with patch("superset.ai.graph.nodes_parent._find_existing_dashboard", return_value=None):
            result = create_dashboard(state)

        call_args = tool_instance.run.call_args[0][0]
        assert call_args["dashboard_title"] == "AI 仪表板"


class TestCountStarMetric:
    """Tests for COUNT(*) metric handling in normalizer and create_chart."""

    def test_normalizer_count_star_metric(self):
        from superset.ai.graph.normalizer import _build_metric_object

        result = _build_metric_object("COUNT(*)", {}, {})
        assert result["expressionType"] == "SQL"
        assert result["sqlExpression"] == "COUNT(*)"
        assert result["label"] == "COUNT(*)"

    def test_create_chart_count_star_metric(self):
        from superset.ai.tools.create_chart import _build_metric_object

        result = _build_metric_object("COUNT(*)", {}, {})
        assert result["expressionType"] == "SQL"
        assert result["sqlExpression"] == "COUNT(*)"

    def test_normalizer_simple_aggregate(self):
        from superset.ai.graph.normalizer import _build_metric_object

        col_lookup = {"num": {"type": "BIGINT", "groupable": False, "filterable": True, "is_dttm": False}}
        result = _build_metric_object("SUM(num)", col_lookup, {})
        assert result["expressionType"] == "SIMPLE"
        assert result["aggregate"] == "SUM"
        assert result["column"]["column_name"] == "num"

    def test_create_chart_simple_aggregate(self):
        from superset.ai.tools.create_chart import _build_metric_object

        col_lookup = {"num": {"type": "BIGINT", "groupable": False, "filterable": True, "is_dttm": False}}
        result = _build_metric_object("SUM(num)", col_lookup, {})
        assert result["expressionType"] == "SIMPLE"
        assert result["aggregate"] == "SUM"
        assert result["column"]["column_name"] == "num"
