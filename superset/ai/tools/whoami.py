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
"""Tool to get current user identity and permissions."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class WhoAmITool(BaseTool):
    """Get information about the current user: name, roles, and permissions."""

    name = "whoami"
    description = (
        "Get information about the current user: username, roles, "
        "and key permissions. Useful for answering access questions."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from flask import g
        from superset.extensions import security_manager

        user = getattr(g, "user", None)
        if not user:
            return json.dumps({"error": "Not authenticated"})

        roles = [r.name for r in user.roles] if user.roles else []

        return json.dumps(
            {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "roles": roles,
                "is_admin": "Admin" in roles,
                "can_create_charts": security_manager.can_access(
                    "can_write", "Chart"
                ),
                "can_create_dashboards": security_manager.can_access(
                    "can_write", "Dashboard"
                ),
                "can_access_sql_lab": security_manager.can_access(
                    "can_read", "Superset"
                ),
            },
            ensure_ascii=False,
            default=str,
        )
