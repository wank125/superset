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
"""Tool to list database connections available in Superset."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class ListDatabasesTool(BaseTool):
    """List database connections available in Superset."""

    name = "list_databases"
    description = (
        "List database connections available in Superset. "
        "Returns engine type and configuration summary. "
        "Optionally filter by name keyword."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Filter by database name keyword",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.database import DatabaseDAO
        from superset.extensions import security_manager

        databases = DatabaseDAO.find_all()
        search = (arguments.get("search") or "").lower()
        result = []

        for db_obj in databases:
            try:
                if not security_manager.can_access_database(db_obj):
                    continue
            except Exception:
                continue

            if search and search not in db_obj.database_name.lower():
                continue

            result.append(
                {
                    "id": db_obj.id,
                    "name": db_obj.database_name,
                    "engine": db_obj.backend,
                    "expose_in_sqllab": db_obj.expose_in_sqllab,
                }
            )

        return json.dumps(
            {"databases": result, "total": len(result)},
            ensure_ascii=False,
        )
