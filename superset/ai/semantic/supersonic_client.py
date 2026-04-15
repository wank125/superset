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
"""HTTP client for SuperSonic Headless BI semantic layer APIs."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class SuperSonicClient:
    """HTTP client for SuperSonic semantic layer.

    All methods return empty data on error — callers never crash.
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 5,
        auth_enabled: bool = False,
        app_key: str = "",
        app_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if auth_enabled and app_key:
            self._session.headers["App-Key"] = app_key
        if auth_enabled and app_secret:
            self._session.headers["Authorization"] = f"Bearer {app_secret}"

    # ── Low-level helpers ──────────────────────────────────────────────

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._session.get(
                f"{self._base_url}{path}",
                timeout=self._timeout,
                **kwargs,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") in ("200", 200):
                return data.get("data", {})
            logger.debug("SuperSonic API returned code=%s", data.get("code"))
            return {}
        except Exception:
            logger.debug("SuperSonic GET %s failed", path, exc_info=True)
            return {}

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._session.post(
                f"{self._base_url}{path}",
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") in ("200", 200):
                return data.get("data", {})
            logger.debug("SuperSonic API returned code=%s", data.get("code"))
            return {}
        except Exception:
            logger.debug("SuperSonic POST %s failed", path, exc_info=True)
            return {}

    # ── Health ─────────────────────────────────────────────────────────

    def health(self) -> bool:
        """Check if SuperSonic service is reachable."""
        try:
            resp = self._session.get(
                f"{self._base_url}/actuator/health",
                timeout=self._timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── Semantic APIs ──────────────────────────────────────────────────

    def get_models(self, domain_id: int | None = None) -> list[dict[str, Any]]:
        """GET /api/semantic/model/getModelList/{domainId}"""
        if domain_id is None:
            return []
        data = self._get(f"/api/semantic/model/getModelList/{domain_id}")
        return data.get("list", []) if isinstance(data, dict) else []

    def get_metrics(self, model_id: int) -> list[dict[str, Any]]:
        """GET /api/semantic/metric/getMetricList/{modelId}"""
        data = self._get(f"/api/semantic/metric/getMetricList/{model_id}")
        return data.get("list", []) if isinstance(data, dict) else []

    def get_dimensions(self, model_id: int) -> list[dict[str, Any]]:
        """GET /api/semantic/dimension/getDimensionList/{modelId}"""
        data = self._get(f"/api/semantic/dimension/getDimensionList/{model_id}")
        return data.get("list", []) if isinstance(data, dict) else []

    # ── Chat APIs ──────────────────────────────────────────────────────

    def parse_query(self, query_text: str, model_id: int) -> dict[str, Any]:
        """POST /api/chat/query/parse — parse NL query to structured intent."""
        return self._post(
            "/api/chat/query/parse",
            {"queryText": query_text, "chatContext": {"modelId": model_id}},
        )

    # ── High-level metric resolution ───────────────────────────────────

    def find_metrics_for_table(
        self,
        table_name: str,
        domain_id: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Resolve table_name → metrics via model mapping + API.

        Returns dict in MetricDef-compatible format:
            {"gmv": {"sql": ..., "tables": [...], "description": ..., ...}}
        """
        from superset.ai.semantic.model_mapping import get_model_id_for_table

        model_id = get_model_id_for_table(table_name, domain_id=domain_id, client=self)
        if model_id is None:
            return {}

        raw_metrics = self.get_metrics(model_id)
        if not raw_metrics:
            return {}

        # Get model info for table name in the MetricDef
        models = self.get_models(domain_id) if domain_id else []
        source_tables = _extract_source_tables(models, model_id)

        return _translate_metrics(raw_metrics, source_tables)


def _extract_source_tables(
    models: list[dict[str, Any]],
    model_id: int,
) -> list[str]:
    """Extract source table names for a given model ID."""
    for model in models:
        if model.get("id") == model_id:
            name = model.get("name", "")
            return [name] if name else []
    return []


def _translate_metrics(
    raw_metrics: list[dict[str, Any]],
    source_tables: list[str],
) -> dict[str, dict[str, Any]]:
    """Translate SuperSonic metric API response to MetricDef format."""
    result: dict[str, dict[str, Any]] = {}
    for metric in raw_metrics:
        name = metric.get("bizName") or metric.get("name", "")
        if not name:
            continue

        sql_expr = _build_sql_expr(metric)
        aliases_str = metric.get("alias", "")
        aliases = [a.strip() for a in aliases_str.split(",") if a.strip()] if aliases_str else []

        result[name] = {
            "sql": sql_expr,
            "tables": source_tables,
            "description": metric.get("description", ""),
            "aliases": aliases,
            "aggregation": _infer_aggregation(sql_expr),
            "unit": None,
        }
    return result


def _build_sql_expr(metric: dict[str, Any]) -> str:
    """Build a SQL expression from SuperSonic metric definition."""
    define_params = metric.get("metricDefineParams", {})

    # Measure-based metrics
    measures = define_params.get("measures", [])
    if measures:
        parts = []
        for m in measures:
            agg = m.get("agg", "SUM").upper()
            biz_name = m.get("bizName", "")
            if biz_name:
                parts.append(f"{agg}({biz_name})")
        return ", ".join(parts) if parts else "COUNT(*)"

    # Expression-based metrics
    expr = define_params.get("expr", "")
    if expr:
        return expr

    # Fallback
    metric_type = metric.get("metricDefineType", "")
    name = metric.get("bizName") or metric.get("name", "unknown")
    if metric_type == "MEASURE":
        return f"SUM({name})"

    return f"COUNT(*)"


def _infer_aggregation(sql_expr: str) -> str:
    """Infer aggregation type from SQL expression."""
    upper = sql_expr.upper()
    for agg in ("SUM", "AVG", "COUNT", "MAX", "MIN"):
        if upper.startswith(agg):
            return agg.lower()
    return "custom"
