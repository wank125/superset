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
"""Business Metric Catalog — maps business KPI names to SQL expressions."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, TypedDict

import yaml

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent / "metric_catalog.yaml"


class MetricDef(TypedDict, total=False):
    """A single business metric definition."""

    sql: str                     # SQL expression (no table name)
    tables: list[str]            # Applicable table names (supports * wildcard)
    description: str             # Human-readable explanation
    aliases: list[str]           # Synonyms / abbreviations
    aggregation: str             # sum | avg | count | ratio | custom
    unit: str | None             # Display unit


@functools.lru_cache(maxsize=1)
def load_metric_catalog() -> dict[str, MetricDef]:
    """Load metric catalog from YAML file (cached for worker lifetime).

    Returns an empty dict on failure so callers never crash.
    """
    if not _CATALOG_PATH.exists():
        logger.warning("metric_catalog.yaml not found at %s", _CATALOG_PATH)
        return {}
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
        logger.warning("metric_catalog.yaml did not produce a dict")
        return {}
    except Exception:
        logger.exception("Failed to load metric_catalog.yaml")
        return {}


def find_metrics_for_table(table_name: str) -> dict[str, MetricDef]:
    """Return metrics applicable to the given table name.

    Supports ``*`` suffix wildcards in the ``tables`` list:
    ``order_*`` matches ``order_detail``, ``order_items``, etc.
    """
    catalog = load_metric_catalog()
    result: dict[str, MetricDef] = {}
    for name, defn in catalog.items():
        tables = defn.get("tables", [])
        if any(
            (t == table_name)
            or (t.endswith("*") and table_name.startswith(t[:-1]))
            for t in tables
        ):
            result[name] = defn
    return result


def match_user_intent_to_metrics(
    user_request: str,
    table_name: str,
) -> dict[str, MetricDef]:
    """Match user request keywords to applicable metrics.

    Checks the metric name, aliases, and description against the
    lowercased user request text.
    """
    applicable = find_metrics_for_table(table_name)
    request_lower = user_request.lower()
    matched: dict[str, MetricDef] = {}
    for name, defn in applicable.items():
        all_names = [name] + defn.get("aliases", []) + [defn.get("description", "")]
        if any(alias.lower() in request_lower for alias in all_names if alias):
            matched[name] = defn
    return matched
