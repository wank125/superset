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
"""Safeguard callbacks for LangChain agent execution."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


class SafeguardCallbackHandler(BaseCallbackHandler):
    """Enforce per-turn text safety limits in the LangChain execution path.

    Mirrors the safeguards in BaseAgent:
      - _MAX_STREAM_CHARS = 10_000 (per-turn character limit)
      - _MAX_REPETITIONS = 8 (30-char tail repetition detection)

    These protect against infinite text generation and repetitive output
    that the token-level repetition detection catches, but tool-level
    loops are handled by ToolCallRepetitionGuard.
    """

    _MAX_STREAM_CHARS = 10_000
    # Tuned from (8, 30) → (15, 50): tool-call JSON tokens inflated the
    # repetition detector (now filtered in on_llm_new_token), so the
    # natural-language repetition threshold can be more permissive.
    _MAX_REPETITIONS = 15
    _TAIL_LEN = 50

    def __init__(self) -> None:
        self._turn_chars = 0
        self._turn_text = ""
        self._stopped = False
        # GLM reasoning_content accumulator — populated via on_llm_new_token
        # because LangGraph's stream_mode=["messages"] reconstructs
        # AIMessageChunk objects, losing additional_kwargs injected by
        # GLMChatOpenAI.
        self._reasoning_parts: list[str] = []

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Reset per-turn counters when a new LLM call starts."""
        self._turn_chars = 0
        self._turn_text = ""
        self._stopped = False
        self._reasoning_parts = []

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Check per-token safety limits."""
        if self._stopped:
            return

        # Skip tool-call structure tokens — only monitor natural-language
        # content.  LangGraph streams tool-call JSON through this callback,
        # which inflates the repetition detector with structural artifacts.
        chunk = kwargs.get("chunk")
        if chunk and hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
            return

        # Capture GLM reasoning_content from the callback chain.
        # The chunk.message.additional_kwargs still has the
        # reasoning_content injected by GLMChatOpenAI at this point,
        # even though LangGraph's StreamMessagesHandler will lose it
        # when reconstructing AIMessageChunk for stream_mode=["messages"].
        if chunk and hasattr(chunk, "message"):
            msg = chunk.message
            reasoning = (
                msg.additional_kwargs.get("reasoning_content")
                if hasattr(msg, "additional_kwargs")
                else None
            )
            if reasoning:
                self._reasoning_parts.append(reasoning)

        self._turn_chars += len(token)
        self._turn_text += token

        if self._turn_chars > self._MAX_STREAM_CHARS:
            logger.warning(
                "Stream exceeded %d chars, stopping", self._MAX_STREAM_CHARS
            )
            self._stopped = True
            return

        if len(self._turn_text) >= 200:
            tail = self._turn_text[-self._TAIL_LEN:]
            if self._turn_text.count(tail) >= self._MAX_REPETITIONS:
                logger.warning("Detected repetitive output, stopping")
                self._stopped = True
                return

    @property
    def stopped(self) -> bool:
        """Whether the callback has triggered a safety stop."""
        return self._stopped

    def get_and_clear_reasoning(self) -> str:
        """Return accumulated reasoning_content and reset for next turn."""
        full = "".join(self._reasoning_parts)
        self._reasoning_parts = []
        return full

    def drain_reasoning(self) -> list[str]:
        """Return new reasoning chunks since last drain and clear them.

        Used by the runner to emit incremental thinking events in real-time
        instead of batching everything until end of turn.
        """
        parts = self._reasoning_parts
        self._reasoning_parts = []
        return parts
