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
"""Tests for AI business metric catalog."""

from unittest.mock import patch


class TestMetricCatalog:
    """Tests for Metric Catalog lookup and intent matching."""

    def test_find_metrics_for_exact_table(self):
        from superset.ai.metric_catalog import find_metrics_for_table

        metrics = find_metrics_for_table("orders")

        assert "gmv" in metrics
        assert "conversion_rate" in metrics

    def test_find_metrics_via_wildcard(self):
        from superset.ai.metric_catalog import find_metrics_for_table

        metrics = find_metrics_for_table("order_detail")

        assert "gmv" in metrics

    def test_birth_names_demo_metrics(self):
        from superset.ai.metric_catalog import find_metrics_for_table

        metrics = find_metrics_for_table("birth_names")

        assert "total_births" in metrics
        assert "male_ratio" in metrics

    def test_match_user_intent_by_alias(self):
        from superset.ai.metric_catalog import match_user_intent_to_metrics

        matched = match_user_intent_to_metrics("查一下GMV和转化率", "orders")

        assert "gmv" in matched
        assert "conversion_rate" in matched

    def test_match_user_intent_chinese_alias(self):
        from superset.ai.metric_catalog import match_user_intent_to_metrics

        matched = match_user_intent_to_metrics("销售额多少", "orders")

        assert "gmv" in matched


class TestPlanQueryWithBusinessMetrics:
    """Tests that business metrics are injected into chart SQL planning."""

    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_business_metric_prompt_and_sql_used(self, mock_llm):
        from superset.ai.graph.nodes_child import plan_query

        mock_llm.return_value = {
            "metric_expr": (
                "SUM(CASE WHEN status IN ('paid') THEN amount ELSE 0 END)"
            ),
            "dimensions": ["region"],
            "time_field": None,
            "limit": 200,
        }
        summary = {
            "datasource_id": 1,
            "table_name": "orders",
            "datetime_cols": ["created_at"],
            "dimension_cols": ["region", "status"],
            "metric_cols": ["amount"],
            "saved_metrics": [],
            "saved_metric_expressions": {},
            "main_dttm_col": "created_at",
            "column_descriptions": {},
            "column_verbose_names": {},
            "business_metrics": {
                "gmv": {
                    "sql": (
                        "SUM(CASE WHEN status IN ('paid') "
                        "THEN amount ELSE 0 END)"
                    ),
                    "description": "GMV",
                    "aliases": ["销售额"],
                    "unit": "元",
                }
            },
        }
        state = {
            "chart_intent": {
                "chart_index": 0,
                "analysis_intent": "comparison",
                "slice_name": "按地区对比销售额",
                "sql_hint": "",
                "preferred_viz": None,
            },
            "schema_summary": summary,
            "last_error": None,
        }

        result = plan_query(state)
        prompt = mock_llm.call_args.args[0]

        assert result.goto == "validate_sql"
        assert "Business metric definitions" in prompt
        assert "gmv: GMV" in prompt
        assert "CASE WHEN" in result.update["sql_plan"]["metric_expr"]
