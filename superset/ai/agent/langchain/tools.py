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
"""Tool adapter — wraps Superset BaseTool as LangChain StructuredTool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from superset.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Mapping from JSON Schema types to Python types for Pydantic model creation
_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _schema_to_pydantic(schema: dict[str, Any]) -> type[BaseModel]:
    """Convert a JSON Schema dict to a Pydantic model class.

    LangChain's StructuredTool requires a Pydantic model for args_schema.
    """
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _TYPE_MAP.get(prop.get("type", "string"), str)
        if name not in required:
            # Optional field with None default
            field_definitions[name] = (py_type | None, None)  # type: ignore[assignment]
        else:
            field_definitions[name] = (py_type, ...)  # type: ignore[assignment]

    return create_model("ToolArgs", **field_definitions)


def tool_adapter(native_tool: BaseTool) -> StructuredTool:
    """Wrap a Superset BaseTool as a LangChain StructuredTool.

    Zero-modification wrapper — existing tools don't need any changes.
    """
    args_schema = _schema_to_pydantic(native_tool.parameters_schema)

    def _run(**kwargs: Any) -> str:
        return native_tool.run(kwargs)

    return StructuredTool.from_function(
        func=_run,
        name=native_tool.name,
        description=native_tool.description,
        args_schema=args_schema,
    )
