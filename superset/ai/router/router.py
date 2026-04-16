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
"""IntentRouter — two-level intent classification (keyword → LLM)."""

from __future__ import annotations

import logging

from superset.ai.router.rules import is_continuation, keyword_route
from superset.ai.router.types import RouteDecision, RouterContext

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.75  # Keyword match must reach this to skip LLM.
_LLM_MIN_CONFIDENCE = 0.50  # LLM result below this → fallback to nl2sql.


class IntentRouter:
    """Two-level intent router: keyword rules → LLM classifier.

    Design goals:
    1. Zero latency for clear cases (keyword match >= 0.75 confidence).
    2. High accuracy for ambiguous cases (LLM classification).
    3. Context-awareness (session history influences routing).
    4. Always returns a valid agent type (nl2sql as safe fallback).
    """

    def route(self, message: str, context: RouterContext) -> RouteDecision:
        # Step 1: Context continuation — reuse previous agent.
        if context.last_agent and is_continuation(message):
            logger.debug(
                "router: context_continuation last_agent=%s", context.last_agent
            )
            return RouteDecision(
                agent=context.last_agent,
                confidence=0.88,
                method="context",
                reason=f"Continues previous {context.last_agent} session",
            )

        # Step 2: Keyword fast path — O(n) string scan, ~0ms.
        agent, confidence = keyword_route(message)
        logger.debug(
            "router: keyword agent=%s confidence=%.2f", agent, confidence
        )
        if confidence >= _CONFIDENCE_THRESHOLD:
            return RouteDecision(
                agent=agent,
                confidence=confidence,
                method="keyword",
                reason=f"Keyword match for {agent}",
            )

        # Step 3: LLM precise classification — single call, ~0.5-1s.
        from superset.ai.router.llm_classifier import llm_classify

        llm_agent, llm_confidence, llm_reason = llm_classify(
            message=message,
            last_agent=context.last_agent,
            last_message=context.last_message,
        )
        logger.debug(
            "router: llm agent=%s confidence=%.2f reason=%s",
            llm_agent,
            llm_confidence,
            llm_reason,
        )

        if llm_reason.startswith("fallback"):
            logger.info(
                "router: llm classifier fallback, defaulting to data_assistant. "
                "message=%s reason=%s",
                message[:100],
                llm_reason,
            )
            return RouteDecision(
                agent="data_assistant",
                confidence=llm_confidence,
                method="fallback",
                reason=llm_reason,
            )

        if llm_confidence >= _LLM_MIN_CONFIDENCE:
            return RouteDecision(
                agent=llm_agent,
                confidence=llm_confidence,
                method="llm",
                reason=llm_reason,
            )

        # Fallback — safe default.
        logger.info(
            "router: low confidence (%.2f), fallback to data_assistant. message=%s",
            llm_confidence,
            message[:100],
        )
        return RouteDecision(
            agent="data_assistant",
            confidence=0.5,
            method="fallback",
            reason="Low confidence, defaulting to data_assistant",
        )
