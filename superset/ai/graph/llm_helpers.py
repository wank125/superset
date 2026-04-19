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
"""LLM helper utilities for the LangGraph StateGraph agent."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from superset.ai.llm.types import LLMMessage
from superset.utils import json

logger = logging.getLogger(__name__)

try:
    from langsmith.run_helpers import traceable
except ImportError:  # pragma: no cover - LangSmith is an optional AI dependency.

    def traceable(*args: Any, **kwargs: Any) -> Callable[..., Any]:
        """Return a no-op decorator when LangSmith is unavailable."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        return decorator

# Regex to extract JSON from LLM output (handles ```json blocks)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _get_llm_response(prompt: str) -> str:
    """Call the configured LLM provider with a single prompt and return text."""
    import time

    from superset.ai.llm.registry import get_provider

    provider = get_provider()
    prompt_preview = prompt[:120].replace("\n", " ")
    logger.info("LLM call starting (prompt preview: %s...)", prompt_preview)
    t0 = time.monotonic()
    messages = [LLMMessage(role="user", content=prompt)]
    response = provider.chat(messages)
    elapsed = time.monotonic() - t0
    logger.info("LLM call done in %.1fs", elapsed)
    return response.content or ""


def _extract_json(text: str) -> str:
    """Extract JSON string from LLM output, handling ```json blocks."""
    # Try ```json``` block first
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()

    # Try raw JSON object
    m = _JSON_OBJECT_RE.search(text)
    if m:
        return m.group(0)

    return text.strip()


def _extract_json_array(text: str) -> str:
    """Extract JSON array from LLM output."""
    # Try ```json``` block first
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()

    # Try raw JSON array
    m = _JSON_ARRAY_RE.search(text)
    if m:
        return m.group(0)

    return text.strip()


@traceable(
    name="stategraph_llm_call_json",
    run_type="llm",
    tags=["superset-ai", "stategraph"],
)
def llm_call_json(prompt: str) -> dict[str, Any]:
    """Call LLM and parse response as a JSON object.

    Returns a dict parsed from the LLM response.
    Raises ValueError if parsing fails.
    """
    raw = _get_llm_response(prompt)
    json_str = _extract_json(raw)

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON: %s", raw[:200])
        raise ValueError(
            f"LLM response is not valid JSON: {raw[:200]}"
        ) from None

    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")

    return result


@traceable(
    name="stategraph_llm_call_json_list",
    run_type="llm",
    tags=["superset-ai", "stategraph"],
)
def llm_call_json_list(prompt: str) -> list[dict[str, Any]]:
    """Call LLM and parse response as a JSON array of objects.

    Returns a list of dicts parsed from the LLM response.
    Raises ValueError if parsing fails.
    """
    raw = _get_llm_response(prompt)
    json_str = _extract_json_array(raw)

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON array: %s", raw[:200])
        raise ValueError(
            f"LLM response is not valid JSON: {raw[:200]}"
        ) from None

    if isinstance(result, dict):
        # Single object → wrap in list
        return [result]

    if not isinstance(result, list):
        raise ValueError(f"Expected JSON array, got {type(result).__name__}")

    return result
