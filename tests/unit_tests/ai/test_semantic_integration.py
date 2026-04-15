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
"""Tests for dual-path metric resolution (SuperSonic-first, YAML-fallback)."""

from unittest.mock import MagicMock, patch

import pytest

from superset.ai.metric_catalog import (
    find_metrics_for_table,
    match_user_intent_to_metrics,
)
from superset.ai.semantic.model_mapping import clear_cache


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Clear model mapping cache between tests."""
    clear_cache()
    yield
    clear_cache()


class TestYamlFallback:
    """When SUPERSONIC_ENABLED=False, the YAML catalog is used."""

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_yaml_metrics_returned(self, mock_enabled):
        metrics = find_metrics_for_table("orders")
        assert "gmv" in metrics
        assert "gmv" in metrics
        assert "SUM(CASE WHEN" in metrics["gmv"]["sql"]

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_yaml_wildcard_match(self, mock_enabled):
        metrics = find_metrics_for_table("order_detail")
        assert "gmv" in metrics  # matches order_* wildcard

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_yaml_no_match(self, mock_enabled):
        metrics = find_metrics_for_table("nonexistent_table")
        assert metrics == {}

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_intent_matching_yaml(self, mock_enabled):
        matched = match_user_intent_to_metrics("查一下GMV和转化率", "orders")
        assert "gmv" in matched
        assert "conversion_rate" in matched


class TestSuperSonicFirstPath:
    """When SUPERSONIC_ENABLED=True, SuperSonic API is tried first."""

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=True)
    @patch("superset.ai.metric_catalog._get_supersonic_client")
    def test_supersonic_metrics_returned(self, mock_client_fn, mock_enabled):
        mock_client = MagicMock()
        mock_client.find_metrics_for_table.return_value = {
            "revenue": {
                "sql": "SUM(total_amount)",
                "tables": ["s2_pv_uv_statis"],
                "description": "Total Revenue",
                "aliases": ["收入", "营收"],
                "aggregation": "sum",
                "unit": None,
            }
        }
        mock_client_fn.return_value = mock_client

        metrics = find_metrics_for_table("s2_pv_uv_statis")
        assert "revenue" in metrics
        assert metrics["revenue"]["sql"] == "SUM(total_amount)"
        mock_client.find_metrics_for_table.assert_called_once()

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=True)
    @patch("superset.ai.metric_catalog._get_supersonic_client")
    def test_supersonic_empty_falls_to_yaml(self, mock_client_fn, mock_enabled):
        """If SuperSonic returns empty, fall back to YAML."""
        mock_client = MagicMock()
        mock_client.find_metrics_for_table.return_value = {}
        mock_client_fn.return_value = mock_client

        # Query a table that exists in YAML
        metrics = find_metrics_for_table("orders")
        assert "gmv" in metrics  # from YAML fallback

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=True)
    @patch("superset.ai.metric_catalog._get_supersonic_client")
    def test_supersonic_error_falls_to_yaml(self, mock_client_fn, mock_enabled):
        """If SuperSonic raises an exception, fall back to YAML."""
        mock_client = MagicMock()
        mock_client.find_metrics_for_table.side_effect = ConnectionError("refused")
        mock_client_fn.return_value = mock_client

        metrics = find_metrics_for_table("orders")
        assert "gmv" in metrics  # from YAML fallback

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=True)
    @patch("superset.ai.metric_catalog._get_supersonic_client")
    def test_intent_matching_supersonic(self, mock_client_fn, mock_enabled):
        """match_user_intent_to_metrics works with SuperSonic metrics."""
        mock_client = MagicMock()
        mock_client.find_metrics_for_table.return_value = {
            "revenue": {
                "sql": "SUM(total_amount)",
                "tables": ["orders"],
                "description": "Total Revenue",
                "aliases": ["收入"],
                "aggregation": "sum",
                "unit": None,
            }
        }
        mock_client_fn.return_value = mock_client

        matched = match_user_intent_to_metrics("收入多少", "orders")
        assert "revenue" in matched


class TestDualPathIntegration:
    """Integration-style tests for the dual-path resolution."""

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_existing_tests_still_pass(self, mock_enabled):
        """YAML path should produce identical results to before."""
        # Test from original test_metric_catalog.py
        metrics = find_metrics_for_table("birth_names")
        assert "total_births" in metrics
        assert "male_ratio" in metrics

    @patch("superset.ai.metric_catalog._is_supersonic_enabled", return_value=False)
    def test_format_compatibility(self, mock_enabled):
        """MetricDef format must contain expected keys for downstream consumers."""
        metrics = find_metrics_for_table("orders")
        for name, defn in metrics.items():
            assert "sql" in defn, f"Missing 'sql' in metric '{name}'"
            assert "description" in defn, f"Missing 'description' in metric '{name}'"
            assert "aliases" in defn, f"Missing 'aliases' in metric '{name}'"
