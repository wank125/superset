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
"""Tool to retrieve database schema metadata."""

from __future__ import annotations

import logging
from typing import Any

from superset.ai.tools.base import BaseTool
from superset.daos.database import DatabaseDAO
from superset.extensions import security_manager
from superset.utils.core import override_user

logger = logging.getLogger(__name__)

_MAX_TABLES = 50
_MAX_COLUMNS_PER_TABLE = 20


class GetSchemaTool(BaseTool):
    """Return table and column metadata from a database for the LLM."""

    name = "get_schema"
    description = (
        "Get database schema metadata including tables, columns, and data types. "
        "Use this to understand the database structure before writing SQL."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "schema_name": {
                "type": "string",
                "description": "Schema name to inspect (optional, defaults to the default schema)",
            },
            "table_name": {
                "type": "string",
                "description": "Specific table name to inspect (optional, returns all tables if omitted)",
            },
        },
        "required": [],
    }

    def __init__(
        self, database_id: int, default_schema: str | None = None
    ) -> None:
        self._database_id = database_id
        self._default_schema = default_schema

    def run(self, arguments: dict[str, Any]) -> str:
        schema_name = arguments.get("schema_name") or self._default_schema
        table_name = arguments.get("table_name")
        database = DatabaseDAO.find_by_id(self._database_id)
        if database is None:
            return f"Error: Database with id={self._database_id} not found."

        # Enforce database-level access control.
        # get_schema returns metadata (no specific table/SQL to check),
        # so can_access_database is the appropriate check.
        if not security_manager.can_access_database(database):
            return f"Error: Access denied for database '{database.database_name}'"

        try:
            return self._fetch_schema(database, schema_name, table_name)
        except Exception as exc:
            logger.exception("Failed to fetch schema")
            return f"Error fetching schema: {exc}"

    def _fetch_schema(
        self,
        database: Any,
        schema_name: str | None,
        table_name: str | None,
    ) -> str:
        lines: list[str] = []
        catalog = None

        # Determine schema
        if schema_name is None:
            with database.get_inspector(catalog=catalog) as inspector:
                schemas = inspector.get_schema_names() if hasattr(inspector, "get_schema_names") else [None]
            # Prefer 'public' over system schemas (information_schema, pg_catalog, etc.)
            if schemas:
                schema_name = next(
                    (s for s in schemas if s == "public"),
                    schemas[0],
                )
            else:
                schema_name = None

        # Get table names
        try:
            with database.get_inspector(catalog=catalog, schema=schema_name) as inspector:
                all_table_names = list(inspector.get_table_names(schema=schema_name))
        except Exception:
            with database.get_inspector(catalog=catalog, schema=schema_name) as inspector:
                all_table_names = list(inspector.get_table_names(schema=schema_name))

        if table_name:
            # Check if specified table exists (case-insensitive)
            matched = [t for t in all_table_names if t.lower() == table_name.lower()]
            if not matched:
                available = ", ".join(sorted(all_table_names)[:30])
                return (
                    f"Table '{table_name}' not found in schema '{schema_name}'. "
                    f"Available tables: {available}"
                )
            table_names = matched
        else:
            table_names = all_table_names

        if not table_names:
            return f"No tables found in schema '{schema_name}'."

        # Truncate if too many tables
        truncated = len(table_names) > _MAX_TABLES
        table_names = table_names[:_MAX_TABLES]

        # Fetch column info for each table
        with database.get_inspector(catalog=catalog, schema=schema_name) as inspector:
            for tbl in table_names:
                try:
                    columns = inspector.get_columns(tbl, schema=schema_name)
                except Exception:
                    lines.append(f"TABLE: {tbl} (unable to fetch columns)")
                    continue

                col_lines = []
                for col in columns[:_MAX_COLUMNS_PER_TABLE]:
                    col_name = col.get("name", "?")
                    col_type = str(col.get("type", "?"))
                    nullable = "NULL" if col.get("nullable", True) else "NOT NULL"
                    col_lines.append(f"  - {col_name} {col_type} {nullable}")
                if len(columns) > _MAX_COLUMNS_PER_TABLE:
                    col_lines.append(f"  ... and {len(columns) - _MAX_COLUMNS_PER_TABLE} more columns")

                lines.append(f"TABLE: {tbl}")
                lines.extend(col_lines)
                lines.append("")

        if truncated:
            lines.append(f"... and more tables (showing first {_MAX_TABLES})")

        header = f"Database: {database.database_name}"
        if schema_name:
            header += f", Schema: {schema_name}"
        return header + "\n\n" + "\n".join(lines)
