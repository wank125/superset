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
from typing import Any

from superset import db
from superset.ai.tools.base import BaseTool
from superset.connectors.sqla.models import SqlaTable
from superset.extensions import security_manager


def _can_access(table: SqlaTable) -> bool:
    """Check datasource access, returning False on any failure."""
    try:
        return security_manager.can_access_datasource(table)
    except Exception:
        return False


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
                "description": "Exact table name to search for",
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
            return "Error: table_name is required"

        # Query SqlaTable by database_id + table_name
        query = db.session.query(SqlaTable).filter(
            SqlaTable.database_id == self._database_id,
            SqlaTable.table_name == table_name,
        )
        if self._schema_name:
            query = query.filter(SqlaTable.schema == self._schema_name)
        table = query.first()

        if not table:
            # Return only datasets the user can access
            all_tables = (
                db.session.query(SqlaTable)
                .filter(SqlaTable.database_id == self._database_id)
                .limit(30)
                .all()
            )
            accessible = sorted(
                t.table_name
                for t in all_tables
                if _can_access(t)
            )
            if not accessible:
                return (
                    f"No accessible datasets found for database_id "
                    f"{self._database_id}."
                )
            return (
                f"Dataset '{table_name}' not found. "
                f"Available datasets: {', '.join(accessible)}"
            )

        # Permission check: only return details for accessible datasources
        try:
            if not security_manager.can_access_datasource(table):
                return f"Error: You do not have access to dataset '{table_name}'."
        except Exception:
            # If permission check fails (e.g. no request context),
            # deny access rather than leak data
            return f"Error: Unable to verify access to dataset '{table_name}'."

        # Build column metadata
        columns = [
            {
                "name": col.column_name,
                "type": str(col.type),
                "groupable": col.groupby,
                "filterable": col.filterable,
                "is_dttm": col.is_dttm,
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
                "datasource_id": table.id,
                "datasource_type": "table",
                "table_name": table.table_name,
                "schema": table.schema,
                "main_datetime_column": table.main_dttm_col or None,
                "columns": columns[:30],
                "metrics": metrics[:20],
            },
            ensure_ascii=False,
            indent=2,
        )
