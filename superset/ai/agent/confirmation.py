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
"""Confirmation helpers for side-effecting AI tools."""

from __future__ import annotations

SIDE_EFFECT_TOOLS = frozenset({"create_chart", "create_dashboard"})

_NEGATIVE_TERMS = (
    "不要",
    "别",
    "先不",
    "取消",
    "不创建",
    "不用创建",
    "不要创建",
    "no",
    "not now",
    "cancel",
    "do not",
    "don't",
)

_CONFIRM_TERMS = (
    "确认",
    "确认创建",
    "可以创建",
    "开始创建",
    "执行创建",
    "同意",
    "继续",
    "创建吧",
    "生成吧",
    "yes",
    "confirm",
    "approved",
    "go ahead",
    "create it",
    "proceed",
)


def is_side_effect_tool(tool_name: str) -> bool:
    """Return whether *tool_name* mutates Superset state."""

    return tool_name in SIDE_EFFECT_TOOLS


def is_creation_confirmed(message: str) -> bool:
    """Return whether the current user message explicitly confirms creation."""

    text = message.strip().lower()
    if not text:
        return False
    if any(term in text for term in _NEGATIVE_TERMS):
        return False
    return any(term in text for term in _CONFIRM_TERMS)


def confirmation_required_message(tool_name: str) -> str:
    """Build the user-facing confirmation gate message."""

    target = "图表" if tool_name == "create_chart" else "仪表板"
    return (
        f"需要你确认后才能创建{target}。我还没有执行创建操作。"
        "请回复“确认创建”后我再继续。"
    )
