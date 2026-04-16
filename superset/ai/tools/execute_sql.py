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
"""Tool to safely execute SQL queries."""

from __future__ import annotations

import logging
import re
from typing import Any

from superset.ai.tools.base import BaseTool
from superset.sql.parse import SQLScript

logger = logging.getLogger(__name__)

_MAX_ROWS = 100
_PREVIEW_ROWS = 10


class ExecuteSqlTool(BaseTool):
    """Execute a read-only SQL query and return results."""

    name = "execute_sql"
    description = (
        "Execute a SQL query on the database and return the results. "
        "Only SELECT queries are allowed. Results are limited to 100 rows."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The SQL query to execute",
            },
        },
        "required": ["sql"],
    }

    def __init__(self, database_id: int) -> None:
        self._database_id = database_id

    def run(self, arguments: dict[str, Any]) -> str:  # noqa: C901
        sql = arguments.get("sql", "").strip()
        if not sql:
            return "Error: No SQL provided."

        from superset import db
        from superset.extensions import security_manager
        from superset.models.core import Database

        database = db.session.query(Database).filter_by(id=self._database_id).first()
        if database is None:
            return f"Error: Database with id={self._database_id} not found."

        # Enforce RBAC: pass the SQL so raise_for_access can check table-level
        # permissions.  Without a table/query argument the call is a no-op.
        try:
            security_manager.raise_for_access(database=database, sql=sql)
        except Exception as exc:
            return (
                f"Error: Access denied for database "
                f"'{database.database_name}': {exc}"
            )

        # Security: reject mutating statements (use target DB dialect)
        try:
            script = SQLScript(sql, engine=database.backend)
        except Exception:
            try:
                script = SQLScript(sql, engine="sqlite")
            except Exception as exc:
                logger.warning(
                    "SQLScript parse failed for SQL (backend=%s): %s",
                    database.backend,
                    sql[:200],
                )
                return f"Error: Could not parse SQL: {exc}"

        if script.has_mutation():
            return (
                "Error: Only SELECT queries are allowed. "
                "DDL/DML statements (INSERT, UPDATE, DELETE, DROP, ALTER, etc.) "
                "are prohibited."
            )

        # Ensure LIMIT clause to prevent unbounded queries
        sql_upper = sql.upper()
        if "LIMIT" not in sql_upper:
            sql += " LIMIT 100"
        else:
            limit_match = re.search(r"LIMIT\s+(\d+)", sql, re.IGNORECASE)
            if limit_match and int(limit_match.group(1)) > 500:
                sql = re.sub(r"LIMIT\s+\d+", "LIMIT 500", sql, flags=re.IGNORECASE)

        try:
            # Execute the query
            try:
                with database.get_sqla_engine() as engine:
                    with engine.connect() as conn:
                        result = conn.execution_options(max_rows=_MAX_ROWS).execute(
                            __import__("sqlalchemy").text(sql)
                        )
                        columns = list(result.keys())
                        rows = result.fetchmany(_MAX_ROWS)
                        total_fetched = len(rows)

                if not rows:
                    return "Query executed successfully. No rows returned."

                # Format as text table
                lines = []
                lines.append(" | ".join(columns))
                lines.append("-" * len(lines[0]))

                preview_rows = rows[:_PREVIEW_ROWS]
                for row in preview_rows:
                    lines.append(" | ".join(str(v) for v in row))

                summary = f"\n\nShowing {len(preview_rows)} of {total_fetched} rows."
                if total_fetched > _PREVIEW_ROWS:
                    summary += f" ({total_fetched - _PREVIEW_ROWS} more rows not shown)"

                return "\n".join(lines) + summary

            except Exception as exc:
                logger.exception("SQL execution failed")
                error_msg = str(exc)
                # Enrich the error with structured information from the DB engine spec
                try:
                    errors = database.db_engine_spec.extract_errors(exc)
                    if errors:
                        err = errors[0]
                        error_type = str(err.error_type)
                        parts = [f"SQL Error [{error_type}]: {err.message}"]
                        if err.extra and "issue_codes" in err.extra:
                            codes = err.extra["issue_codes"]
                            if (
                                codes
                                and isinstance(codes, list)
                                and codes[0].get("message")
                            ):
                                parts.append(f"Suggestion: {codes[0]['message']}")
                        return "\n".join(parts)
                except Exception:
                    logger.debug("Failed to extract SQL errors", exc_info=True)
                return f"Error executing SQL: {error_msg}"
        finally:
            db.session.rollback()
