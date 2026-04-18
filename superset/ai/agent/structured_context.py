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
"""Structured cross-agent context protocol.

The AI subsystem stores compact tool summaries in conversation history so later
turns can reuse prior work.  This module defines the JSON payloads for those
summaries instead of relying on free-form strings.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

from superset.utils import json

STRUCTURED_CONTEXT_VERSION = 1

ContextKind = Literal[
    "dataset_context",
    "query_context",
    "chart_context",
    "analysis_plan",
]


class StructuredContext(TypedDict, total=False):
    """Shared JSON envelope stored as ``tool_summary.content``."""

    version: int
    kind: ContextKind
    table_name: str
    schema_name: str | None
    database_id: int | None
    datasource_id: int | None
    sql: str
    result_preview: str
    row_count: int | None
    columns: list[dict[str, Any]]
    chart_id: int
    slice_name: str
    viz_type: str
    explore_url: str
    payload: dict[str, Any]


def dump_context(context: StructuredContext) -> str:
    """Serialize a structured context with the required protocol envelope."""
    payload: StructuredContext = {
        "version": STRUCTURED_CONTEXT_VERSION,
        **context,
    }
    return json.dumps(payload)


def load_context(
    content: Any,
    *,
    expected_kind: ContextKind | None = None,
) -> StructuredContext | None:
    """Parse a structured context payload, returning ``None`` on mismatch."""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return None
    elif isinstance(content, dict):
        parsed = content
    else:
        return None

    if not isinstance(parsed, dict):
        return None
    if parsed.get("version") != STRUCTURED_CONTEXT_VERSION:
        return None
    if expected_kind and parsed.get("kind") != expected_kind:
        return None
    return parsed


def read_latest_context(
    history: list[dict[str, Any]],
    kind: ContextKind,
    *,
    limit: int = 10,
) -> StructuredContext | None:
    """Read the newest structured context of ``kind`` from conversation history."""
    for entry in reversed(history[-limit:]):
        if entry.get("role") != "tool_summary" or entry.get("tool") != kind:
            continue
        parsed = load_context(entry.get("content"), expected_kind=kind)
        if parsed is not None:
            return parsed
    return None


def build_dataset_context(
    *,
    table_name: str,
    sql: str,
    database_id: int | None,
    schema_name: str | None,
    datasource_id: int | None = None,
) -> StructuredContext:
    """Build a context payload that identifies the dataset used by a query."""
    return {
        "kind": "dataset_context",
        "table_name": table_name,
        "schema_name": schema_name,
        "database_id": database_id,
        "datasource_id": datasource_id,
        "sql": sql[:1000],
    }


def build_query_context(
    *,
    sql: str,
    result_preview: str,
    database_id: int | None,
    schema_name: str | None,
    table_name: str | None = None,
    columns: list[dict[str, Any]] | None = None,
    row_count: int | None = None,
) -> StructuredContext:
    """Build a context payload that summarizes the last executed query."""
    context: StructuredContext = {
        "kind": "query_context",
        "sql": sql[:1000],
        "result_preview": result_preview[:1000],
        "database_id": database_id,
        "schema_name": schema_name,
        "row_count": row_count,
    }
    if table_name:
        context["table_name"] = table_name
    if columns:
        context["columns"] = columns[:20]
    return context


def build_chart_context(
    *,
    chart_id: int,
    slice_name: str,
    viz_type: str,
    explore_url: str,
    datasource_id: int | None = None,
) -> StructuredContext:
    """Build a context payload that identifies a generated chart."""
    context: StructuredContext = {
        "kind": "chart_context",
        "chart_id": chart_id,
        "slice_name": slice_name,
        "viz_type": viz_type,
        "explore_url": explore_url,
        "datasource_id": datasource_id,
    }
    return context


def extract_table_from_sql(sql: str) -> str | None:
    """Extract the primary table name from a SELECT SQL statement.

    This intentionally returns only a best-effort table name.  Callers must
    still resolve the table through Superset dataset search before using it.
    """
    cleaned = _strip_cte_prefix(sql)
    match = re.search(
        r'\bFROM\s+(?:"[^"]+"\.)?"?([A-Za-z_][\w]*)"?',
        cleaned,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _strip_cte_prefix(sql: str) -> str:
    """Remove a simple WITH prefix so top-level FROM extraction is less noisy."""
    if not re.match(r"^\s*WITH\b", sql, re.IGNORECASE):
        return sql

    depth = 0
    for index, char in enumerate(sql):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif depth == 0 and re.match(r"\s*SELECT\b", sql[index:], re.IGNORECASE):
            return sql[index:]
    return sql
