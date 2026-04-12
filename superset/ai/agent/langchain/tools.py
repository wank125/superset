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
from typing import Any, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from superset.ai.agent.confirmation import confirmation_required_message
from superset.ai.tools.base import BaseTool

if TYPE_CHECKING:
    from superset.ai.agent.langchain.guard import ToolOrderGuard

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


def tool_adapter(
    native_tool: BaseTool,
    order_guard: ToolOrderGuard | None = None,
    requires_confirmation: bool = False,
    confirmed: bool = False,
) -> StructuredTool:
    """Wrap a Superset BaseTool as a LangChain StructuredTool.

    If *order_guard* is provided, the wrapper checks the guard before
    executing the tool.  When the guard blocks the call, the tool
    returns an error message instead of executing the real tool — this
    prevents LangGraph from performing side effects out of order.
    """
    args_schema = _schema_to_pydantic(native_tool.parameters_schema)
    tool_name = native_tool.name

    def _run(**kwargs: Any) -> str:
        if requires_confirmation and not confirmed:
            logger.info(
                "Confirmation gate blocked '%s' with args=%s",
                tool_name,
                kwargs,
            )
            return (
                f"Error: {confirmation_required_message(tool_name)} "
                "Do not call this tool again in the same turn."
            )

        # Order guard: block execution if tool is called out of sequence.
        # This runs *inside* LangGraph's tool execution, so it actually
        # prevents side effects — unlike checking in the stream handler.
        if order_guard is not None and not order_guard.check(tool_name):
            allowed = sorted(order_guard.allowed_tools)
            msg = (
                f"Tool '{tool_name}' called out of order. "
                f"Call one of: {', '.join(allowed)} first."
            )
            logger.warning(
                "Order guard blocked '%s' (phase=%d)",
                tool_name,
                order_guard.phase_idx,
            )
            return f"Error: {msg}"

        result = native_tool.run(kwargs)

        # Advance phase after successful execution.
        if order_guard is not None and not result.startswith("Error"):
            order_guard.advance(tool_name)

        return result

    return StructuredTool.from_function(
        func=_run,
        name=tool_name,
        description=native_tool.description,
        args_schema=args_schema,
    )
