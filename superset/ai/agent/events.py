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
"""Agent event types for streaming."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "thinking",
    "retrying",
    "text_chunk",
    "tool_call",
    "tool_result",
    "sql_generated",
    "data_analyzed",
    "insight_generated",  # Phase 11: data insight text
    "chart_created",
    "chart_updated",    # Phase 14: existing chart modified
    "dashboard_created",
    "error_fixed",
    "intent_routed",
    "clarify",        # Phase 17: ask user for missing info (dataset, etc.)
    "done",
    "error",
]


@dataclass
class AgentEvent:
    """A single event emitted by an agent during execution."""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
