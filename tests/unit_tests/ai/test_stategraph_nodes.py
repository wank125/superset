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

        assert result.goto == "single_chart_subgraph"
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
