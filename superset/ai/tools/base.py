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
"""Base class for agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Abstract base class for agent tools.

    Each tool must define a ``name``, ``description``, and ``parameters_schema``
    (JSON Schema) that the LLM uses for function calling.
    """

    name: str = ""
    description: str = ""
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> str:
        """Execute the tool and return a string result for the LLM."""
