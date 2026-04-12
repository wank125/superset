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
"""Tool to search for existing datasets (SqlaTable) in Superset."""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from typing import Any

from superset import db
from superset.ai.tools.base import BaseTool
from superset.connectors.sqla.models import SqlaTable
from superset.extensions import security_manager

logger = logging.getLogger(__name__)


def _can_access(table: SqlaTable) -> bool:
    """Check datasource access, returning False on any failure."""
    try:
        return security_manager.can_access_datasource(table)
    except Exception:
        return False


def _fuzzy_search(
    table_name: str,
    accessible: list[SqlaTable],
) -> list[dict[str, Any]]:
    """Four-level fuzzy search returning ranked candidates.

    Level 1: Exact match (case-insensitive)
    Level 2: Description / verbose_name keyword match
    Level 3: Substring match (table_name contains query)
    Level 4: difflib similarity >= 0.4
    """
    query = table_name.lower().strip()

    # Level 1: Exact match
    for t in accessible:
        if t.table_name.lower() == query:
            return [{"table_name": t.table_name, "match_score": 1.0}]

    # Level 2: Description / verbose_name contains query keyword
    by_desc: list[dict[str, Any]] = []
    for t in accessible:
        desc = (t.description or "").lower()
        verbose = (t.verbose_name or "").lower()
        if query in desc or query in verbose:
            by_desc.append({
                "table_name": t.table_name,
                "match_score": 0.8,
                "description": t.description or "",
            })
    if by_desc:
        return by_desc[:5]

    # Level 3: Substring match
    substring: list[dict[str, Any]] = []
    for t in accessible:
        name_lower = t.table_name.lower()
        # Check both directions: query in name, or name in query
        if query in name_lower or name_lower in query:
            substring.append({
                "table_name": t.table_name,
                "match_score": 0.6,
            })
    if substring:
        # Deduplicate and sort by name length (shorter = more relevant)
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for s in substring:
            if s["table_name"] not in seen:
                seen.add(s["table_name"])
                unique.append(s)
        unique.sort(key=lambda x: len(x["table_name"]))
        return unique[:5]

    # Level 4: difflib similarity >= 0.4
    scored: list[tuple[float, dict[str, Any]]] = []
    for t in accessible:
        ratio = SequenceMatcher(
            None, query, t.table_name.lower(),
        ).ratio()
        if ratio >= 0.4:
            scored.append((ratio, {
                "table_name": t.table_name,
                "match_score": round(ratio, 2),
            }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:5]]


class SearchDatasetsTool(BaseTool):
    """Search for Superset datasets to obtain datasource_id for chart creation."""

    name = "search_datasets"
    description = (
        "Search for existing datasets (tables) in Superset. "
        "Returns the datasource_id and column/metric metadata needed to "
        "create charts. If the table is not registered as a dataset, "
        "returns a list of available datasets."
    )

    parameters_schema = {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Table name or keyword to search for",
            },
        },
        "required": ["table_name"],
    }

    def __init__(
        self, database_id: int, schema_name: str | None = None
    ) -> None:
        self._database_id = database_id
        self._schema_name = schema_name

    def run(self, arguments: dict[str, Any]) -> str:
        table_name = arguments.get("table_name", "")
        if not table_name:
            return json.dumps(
                {"status": "error", "message": "table_name is required"}
            )

        # Query all accessible tables for this database
        query = db.session.query(SqlaTable).filter(
            SqlaTable.database_id == self._database_id,
        )
        if self._schema_name:
            query = query.filter(SqlaTable.schema == self._schema_name)
        all_tables = query.limit(50).all()
        accessible = [t for t in all_tables if _can_access(t)]

        if not accessible:
            return json.dumps(
                {
                    "status": "not_found",
                    "message": (
                        f"No accessible datasets found for database_id "
                        f"{self._database_id}."
                    ),
                    "available_datasets": [],
                },
                ensure_ascii=False,
            )

        # Try exact match first (fast path — no fuzzy overhead)
        exact_match = next(
            (t for t in accessible if t.table_name == table_name),
            None,
        )
        if exact_match:
            return self._build_found_result(exact_match)

        # Fuzzy search for best candidates
        candidates = _fuzzy_search(table_name, accessible)

        if not candidates:
            # No match at any level — return all accessible as fallback
            return json.dumps(
                {
                    "status": "not_found",
                    "message": f"Dataset '{table_name}' not found.",
                    "available_datasets": [
                        {"table_name": t.table_name}
                        for t in sorted(accessible, key=lambda x: x.table_name)
                    ],
                },
                ensure_ascii=False,
            )

        # If best candidate has high score, do a re-search with exact name
        best = candidates[0]
        if best.get("match_score", 0) >= 0.8:
            exact_retry = next(
                (t for t in accessible if t.table_name == best["table_name"]),
                None,
            )
            if exact_retry:
                return self._build_found_result(exact_retry)

        # Return ranked candidates for select_dataset node
        return json.dumps(
            {
                "status": "not_found",
                "message": (
                    f"未找到精确匹配，以下是最相近的 {len(candidates)} 个数据集"
                ),
                "available_datasets": candidates,
            },
            ensure_ascii=False,
        )

    def _build_found_result(self, table: SqlaTable) -> str:
        """Build the found-dataset JSON response with full metadata."""
        # Permission check
        try:
            if not security_manager.can_access_datasource(table):
                return json.dumps(
                    {
                        "status": "error",
                        "message": f"No access to dataset '{table.table_name}'.",
                    }
                )
        except Exception:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Unable to verify access to dataset '{table.table_name}'."
                    ),
                }
            )

        # Build column metadata (Phase 12: include description + verbose_name)
        columns = [
            {
                "name": col.column_name,
                "type": str(col.type),
                "groupable": col.groupby,
                "filterable": col.filterable,
                "is_dttm": col.is_dttm,
                "description": col.description or None,
                "verbose_name": col.verbose_name or None,
            }
            for col in table.columns
        ]

        # Build metric metadata
        metrics = [
            {"name": m.metric_name, "expression": m.expression}
            for m in table.metrics
        ]

        return json.dumps(
            {
                "status": "found",
                "datasource_id": table.id,
                "datasource_type": "table",
                "table_name": table.table_name,
                "schema": table.schema,
                "main_datetime_column": table.main_dttm_col or None,
                "columns": columns[:30],
                "metrics": metrics[:20],
            },
            ensure_ascii=False,
        )
