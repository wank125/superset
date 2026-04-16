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
"""API endpoint for AI-powered alert rule generation."""

from __future__ import annotations

import logging
import re
from typing import Any

from flask import request
from flask_appbuilder.api import expose, protect
from marshmallow import Schema, fields, validate

from superset.ai.alert.prompts import ALERT_GENERATION_PROMPT
from superset.ai.graph.llm_helpers import _get_llm_response, _extract_json
from superset.utils import json
from superset.views.base_api import BaseSupersetApi, safe, statsd_metrics

logger = logging.getLogger(__name__)


class AiAlertGenerateSchema(Schema):
    """Request schema for alert generation."""

    message = fields.String(required=True, validate=validate.Length(min=1, max=2000))
    database_id = fields.Integer(required=True)
    schema_name = fields.String(load_default=None)


class AiAlertRestApi(BaseSupersetApi):
    """API endpoints for AI alert rule generation."""

    resource_name = "ai/alert"
    class_permission_name = "AI Agent"
    openapi_spec_tag = "AI Alert"

    @expose("/generate/", methods=["POST"])
    @protect(allow_browser_login=True)
    @safe
    @statsd_metrics
    def generate(self) -> Any:
        """Generate an alert configuration from natural language.
        ---
        post:
          summary: Generate alert rule from natural language
          requestBody:
            required: true
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    message:
                      type: string
                      description: Natural language alert description
                    database_id:
                      type: integer
                      description: Target database ID
                    schema_name:
                      type: string
                      description: Optional schema name
          responses:
            200:
              description: Generated alert configuration
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      name:
                        type: string
                      sql:
                        type: string
                      validator_type:
                        type: string
                      validator_config_json:
                        type: object
                      crontab:
                        type: string
                      description:
                        type: string
                      database_id:
                        type: integer
            400:
              description: Invalid request
            500:
              description: LLM generation failed
        """
        body = request.get_json(silent=True) or {}
        schema = AiAlertGenerateSchema()
        try:
            data = schema.load(body)
        except Exception as ex:
            return self.response_400(message=str(ex))

        message = data["message"]
        database_id = data["database_id"]
        schema_name = data.get("schema_name")

        # Fetch database schema for context
        schema_text = _get_database_schema(database_id, schema_name)
        if not schema_text:
            return self.response_400(
                message="Could not load database schema. "
                        "Ensure the database exists and has tables."
            )

        # Build prompt
        prompt = ALERT_GENERATION_PROMPT.format(
            database_name=_get_database_name(database_id),
            database_id=database_id,
            schema_text=schema_text,
            message=message,
        )

        # Call LLM
        try:
            raw_response = _get_llm_response(prompt)
            json_str = _extract_json(raw_response)
            alert_config = json.loads(json_str)
        except Exception as ex:
            logger.warning("AI alert generation failed: %s", ex)
            return self.response_500(
                message=f"Failed to generate alert: {ex}"
            )

        # Basic validation of generated SQL
        generated_sql = alert_config.get("sql", "")
        if not generated_sql or not _is_safe_alert_sql(generated_sql):
            return self.response_400(
                message="Generated SQL failed safety validation. "
                        "Please rephrase your request."
            )

        # Ensure database_id is set in the response
        alert_config["database_id"] = database_id

        return self.response(200, **alert_config)


_FORBIDDEN_SQL_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "GRANT", "REVOKE",
})


def _is_safe_alert_sql(sql: str) -> bool:
    """Basic static check: reject DDL/DML and require SELECT.

    Scans the entire SQL for dangerous keywords (not just the start)
    to prevent injection patterns like ``SELECT 1; DROP TABLE users``.
    """
    sql_stripped = sql.strip().upper()
    if not sql_stripped.startswith("SELECT"):
        return False
    for kw in _FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{kw}\b", sql_stripped):
            return False
    return True


def _get_database_name(database_id: int) -> str:
    """Get database name by ID."""
    try:
        from superset import db
        from superset.models.core import Database

        database = db.session.query(Database).get(database_id)
        return database.name if database else str(database_id)
    except Exception:
        return str(database_id)


def _get_database_schema(
    database_id: int,
    schema_name: str | None = None,
) -> str:
    """Get a text summary of tables and columns for the given database."""
    try:
        from superset import db
        from superset.models.core import Database

        database = db.session.query(Database).get(database_id)
        if not database:
            return ""

        # Get table names via Superset Database API
        try:
            table_names = database.get_all_table_names_in_schema(
                catalog=None, schema=schema_name,
            )
            # Table name may be a NamedTuple (catalog, schema, table)
            table_strs = [_extract_table_name(t) for t in table_names]
        except Exception:
            table_strs = []

        if not table_strs:
            return ""

        lines: list[str] = []
        for table_name in table_strs[:20]:
            lines.append(f"  Table: {table_name}")
            # Add column info for richer LLM context
            try:
                from superset.sql.parse import Table as SqlTable

                cols = database.get_columns(
                    SqlTable(table=table_name, schema=schema_name),
                ) or []
                for col in cols[:15]:
                    col_name = col.get("name", "?")
                    col_type = str(col.get("type", "")).split("(")[0]
                    lines.append(f"    {col_name} ({col_type})")
            except Exception:
                pass  # column introspection failure is non-fatal

        return "\n".join(lines) if lines else "(no tables found)"
    except Exception as exc:
        logger.warning("Failed to load database schema: %s", exc)
        return ""


def _extract_table_name(table: Any) -> str:
    """Extract a table name from various return formats of get_tables()."""
    if isinstance(table, str):
        return table
    if isinstance(table, tuple):
        return table[-1]
    return getattr(table, "table", str(table))
