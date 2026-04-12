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
"""Tool to get detailed information about a Superset dataset."""

from __future__ import annotations

import json
from typing import Any

from superset.ai.tools.base import BaseTool


class GetDatasetDetailTool(BaseTool):
    """Get full details of a Superset dataset: columns, metrics, related charts."""

    name = "get_dataset_detail"
    description = (
        "Get full details of a Superset dataset: columns, metrics, "
        "filters, caching config, and optionally which charts use it."
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "required": ["dataset_id"],
        "properties": {
            "dataset_id": {
                "type": "integer",
                "description": "The dataset (SqlaTable) ID",
            },
            "include_charts": {
                "type": "boolean",
                "description": "Include charts using this dataset (default: true)",
            },
        },
    }

    def run(self, arguments: dict[str, Any]) -> str:
        from superset.daos.dataset import DatasetDAO
        from superset.extensions import security_manager

        dataset_id = arguments.get("dataset_id")
        if dataset_id is None:
            return json.dumps({"error": "dataset_id is required"})

        ds = DatasetDAO.find_by_id(dataset_id)
        if not ds:
            return json.dumps({"error": f"Dataset {dataset_id} not found"})

        # Permission check
        try:
            if not security_manager.can_access_datasource(ds):
                return json.dumps(
                    {"error": f"No access to dataset '{ds.table_name}'"}
                )
        except Exception:
            return json.dumps(
                {"error": f"Unable to verify access to dataset '{ds.table_name}'"}
            )

        result: dict[str, Any] = {
            "id": ds.id,
            "table_name": ds.table_name,
            "schema": ds.schema,
            "database": ds.database.database_name if ds.database else None,
            "description": ds.description,
            "main_datetime_col": ds.main_dttm_col,
            "cache_timeout_seconds": ds.cache_timeout,
            "columns": [
                {
                    "name": c.column_name,
                    "type": str(c.type),
                    "description": c.description,
                    "is_filterable": c.filterable,
                    "is_groupable": c.groupby,
                    "is_datetime": c.is_dttm,
                }
                for c in ds.columns[:30]
            ],
            "metrics": [
                {
                    "name": m.metric_name,
                    "expression": m.expression,
                    "description": m.description,
                }
                for m in ds.metrics
            ],
        }

        include_charts = arguments.get("include_charts", True)
        if include_charts:
            from superset import db
            from superset.models.slice import Slice

            charts = (
                db.session.query(Slice)
                .filter_by(datasource_id=ds.id)
                .order_by(Slice.changed_on.desc())
                .limit(10)
                .all()
            )
            result["charts_using_this_dataset"] = [
                {"id": c.id, "name": c.slice_name, "viz_type": c.viz_type}
                for c in charts
            ]

        return json.dumps(result, ensure_ascii=False, default=str)
