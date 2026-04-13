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
"""User-facing error formatting for AI agent failures."""

from __future__ import annotations

from typing import Any

CONTENT_FILTER_MESSAGE = (
    "模型服务拒绝了本次请求，可能触发了内容安全过滤。"
    "请调整提问后重试。"
)

_CONTENT_FILTER_MARKERS = (
    "contentfilter",
    "content_filter",
    "code': '1301",
    'code": "1301',
    "系统检测到输入或生成内容可能包含不安全或敏感内容",
)


def format_user_facing_error(exc: Exception) -> str:
    """Return a concise error message safe to show to users."""

    raw = _extract_error_text(exc)
    lowered = raw.lower()
    if any(marker in lowered for marker in _CONTENT_FILTER_MARKERS):
        return CONTENT_FILTER_MESSAGE

    return str(exc)


def _extract_error_text(exc: Exception) -> str:
    """Extract provider response text when available."""

    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)

    try:
        data: Any = response.json()
    except Exception:
        text = getattr(response, "text", None)
        return str(text or exc)

    return str(data)
