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
"""Tests for SuperSonic semantic layer client."""

from unittest.mock import MagicMock, patch

import pytest

from superset.ai.semantic.supersonic_client import (
    SuperSonicClient,
    _build_sql_expr,
    _infer_aggregation,
    _translate_metrics,
)


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


@pytest.fixture
def client() -> SuperSonicClient:
    return SuperSonicClient(
        base_url="http://localhost:9080",
        timeout=3,
        auth_enabled=False,
    )


@pytest.fixture
def auth_client() -> SuperSonicClient:
    return SuperSonicClient(
        base_url="http://localhost:9080",
        timeout=3,
        auth_enabled=True,
        app_key="test-key",
        app_secret="test-secret",
    )


class TestHealth:
    def test_health_success(self, client):
        client._session = MagicMock()
        client._session.get.return_value = _mock_response({"status": "UP"})

        assert client.health() is True
        client._session.get.assert_called_once()

    def test_health_failure(self, client):
        client._session = MagicMock()
        client._session.get.side_effect = ConnectionError("refused")

        assert client.health() is False


class TestGetMetrics:
    @patch.object(SuperSonicClient, "_get")
    def test_get_metrics_success(self, mock_get, client):
        mock_get.return_value = {
            "list": [
                {"id": 1, "name": "gmv", "bizName": "gmv"},
                {"id": 2, "name": "dau", "bizName": "dau"},
            ]
        }

        metrics = client.get_metrics(1)
        assert len(metrics) == 2
        assert metrics[0]["bizName"] == "gmv"
        mock_get.assert_called_once_with("/api/semantic/metric/getMetricList/1")

    @patch.object(SuperSonicClient, "_get")
    def test_get_metrics_empty(self, mock_get, client):
        mock_get.return_value = {}

        metrics = client.get_metrics(1)
        assert metrics == []

    @patch.object(SuperSonicClient, "_get")
    def test_get_metrics_api_error(self, mock_get, client):
        mock_get.return_value = {}

        metrics = client.get_metrics(999)
        assert metrics == []


class TestGetDimensions:
    @patch.object(SuperSonicClient, "_get")
    def test_get_dimensions(self, mock_get, client):
        mock_get.return_value = {
            "list": [{"id": 1, "name": "region"}]
        }

        dims = client.get_dimensions(1)
        assert len(dims) == 1
        mock_get.assert_called_once_with("/api/semantic/dimension/getDimensionList/1")


class TestGetModels:
    @patch.object(SuperSonicClient, "_get")
    def test_get_models_with_domain(self, mock_get, client):
        mock_get.return_value = {
            "list": [{"id": 1, "name": "orders"}]
        }

        models = client.get_models(domain_id=1)
        assert len(models) == 1

    def test_get_models_no_domain(self, client):
        models = client.get_models(domain_id=None)
        assert models == []


class TestAuthHeaders:
    def test_auth_headers_set(self):
        client = SuperSonicClient(
            base_url="http://localhost:9080",
            auth_enabled=True,
            app_key="my-key",
            app_secret="my-secret",
        )
        assert client._session.headers["App-Key"] == "my-key"
        assert client._session.headers["Authorization"] == "Bearer my-secret"

    def test_no_auth_headers(self):
        client = SuperSonicClient(
            base_url="http://localhost:9080",
            auth_enabled=False,
        )
        assert "App-Key" not in client._session.headers
        assert "Authorization" not in client._session.headers


class TestBuildSqlExpr:
    def test_measure_based(self):
        metric = {
            "metricDefineParams": {
                "measures": [{"agg": "SUM", "bizName": "amount"}]
            }
        }
        assert _build_sql_expr(metric) == "SUM(amount)"

    def test_multiple_measures(self):
        metric = {
            "metricDefineParams": {
                "measures": [
                    {"agg": "SUM", "bizName": "revenue"},
                    {"agg": "COUNT", "bizName": "order_id"},
                ]
            }
        }
        assert _build_sql_expr(metric) == "SUM(revenue), COUNT(order_id)"

    def test_expression_based(self):
        metric = {
            "metricDefineParams": {"expr": "SUM(a) / NULLIF(COUNT(b), 0)"}
        }
        assert _build_sql_expr(metric) == "SUM(a) / NULLIF(COUNT(b), 0)"

    def test_fallback_measure(self):
        metric = {
            "bizName": "gmv",
            "metricDefineType": "MEASURE",
            "metricDefineParams": {},
        }
        assert _build_sql_expr(metric) == "SUM(gmv)"

    def test_fallback_count(self):
        metric = {"metricDefineParams": {}}
        assert _build_sql_expr(metric) == "COUNT(*)"


class TestTranslateMetrics:
    def test_basic_translation(self):
        raw = [
            {
                "id": 1,
                "name": "gmv",
                "bizName": "gmv",
                "description": "Gross Merchandise Volume",
                "metricDefineParams": {
                    "measures": [{"agg": "SUM", "bizName": "amount"}]
                },
                "alias": "商品交易总额,成交总额",
            }
        ]

        result = _translate_metrics(raw, source_tables=["orders"])
        assert "gmv" in result
        assert result["gmv"]["sql"] == "SUM(amount)"
        assert result["gmv"]["tables"] == ["orders"]
        assert result["gmv"]["description"] == "Gross Merchandise Volume"
        assert result["gmv"]["aliases"] == ["商品交易总额", "成交总额"]
        assert result["gmv"]["aggregation"] == "sum"
        assert result["gmv"]["unit"] is None

    def test_empty_alias(self):
        raw = [
            {
                "id": 2,
                "name": "dau",
                "bizName": "dau",
                "description": "Daily Active Users",
                "metricDefineParams": {
                    "measures": [{"agg": "COUNT", "bizName": "user_id"}]
                },
                "alias": "",
            }
        ]

        result = _translate_metrics(raw, source_tables=["user_events"])
        assert result["dau"]["aliases"] == []
        assert result["dau"]["aggregation"] == "count"

    def test_skip_empty_name(self):
        raw = [{"id": 3, "name": "", "bizName": ""}]
        result = _translate_metrics(raw, source_tables=[])
        assert result == {}


class TestInferAggregation:
    def test_sum(self):
        assert _infer_aggregation("SUM(amount)") == "sum"

    def test_avg(self):
        assert _infer_aggregation("AVG(price)") == "avg"

    def test_count(self):
        assert _infer_aggregation("COUNT(*)") == "count"

    def test_custom(self):
        assert _infer_aggregation("SUM(a) / NULLIF(COUNT(b), 0)") == "sum"

    def test_unknown(self):
        assert _infer_aggregation("CASE WHEN x > 0 THEN 1 ELSE 0 END") == "custom"
