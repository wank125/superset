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
"""Data types for the intent router."""

from __future__ import annotations

from dataclasses import dataclass

AgentType = str  # "nl2sql" | "chart" | "dashboard" | "copilot" | "debug"

VALID_AGENT_TYPES: tuple[AgentType, ...] = (
    "nl2sql",
    "chart",
    "dashboard",
    "copilot",
    "debug",
)


@dataclass
class RouteDecision:
    """Result of an intent routing decision."""

    agent: AgentType
    confidence: float  # 0.0 - 1.0
    method: str  # "context" | "keyword" | "llm" | "fallback"
    reason: str  # Debug info, not shown to user


@dataclass
class RouterContext:
    """Context available to the router for making decisions."""

    last_agent: AgentType | None
    last_message: str | None
    session_id: str
    user_id: int
