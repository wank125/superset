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
"""LangChain LLM configuration for AI Agent."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk

from superset.ai.config import get_default_provider_name, get_llm_providers

logger = logging.getLogger(__name__)


class GLMChatOpenAI(ChatOpenAI):
    """Extended ChatOpenAI that captures GLM reasoning_content.

    ZhiPu's thinking models (GLM-4, GLM-5.x) return a
    ``reasoning_content`` field alongside standard ``content``.
    The standard ChatOpenAI silently discards it; this subclass
    patches the internal ``_convert_chunk_to_generation_chunk`` to preserve it.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: Any,
        base_generation_info: dict[str, Any] | None,
    ) -> Any:
        """Override to inject reasoning_content into additional_kwargs."""
        choices = chunk.get("choices", [])
        reasoning = None
        if choices:
            delta = choices[0].get("delta", {})
            reasoning = delta.get("reasoning_content")

        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )

        if result and reasoning and isinstance(result.message, AIMessageChunk):
            result.message.additional_kwargs["reasoning_content"] = reasoning

        return result


def get_langchain_llm() -> ChatOpenAI:
    """Build a LangChain ChatOpenAI from Superset's AI config.

    Forces ``parallel_tool_calls=False`` so the model returns at most
    one tool call per turn, preventing concurrent/skip-step issues.
    """
    provider_name = get_default_provider_name()
    providers = get_llm_providers()
    cfg = providers.get(provider_name, {})

    api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "")

    model_name = cfg.get("model", "gpt-4o")
    llm_cls = GLMChatOpenAI if "glm" in model_name.lower() else ChatOpenAI

    # repetition_penalty is a server-side setting for local LLM engines
    # (vLLM, llama.cpp).  Not all OpenAI-compatible APIs support it as a
    # request parameter, so we rely on the runner-level dedup instead.
    model_kwargs: dict[str, Any] = {
        "parallel_tool_calls": False,
    }
    # Only add frequency_penalty if explicitly configured (> 0)
    freq_pen = cfg.get("frequency_penalty", 0.0)
    if freq_pen:
        model_kwargs["frequency_penalty"] = freq_pen

    return llm_cls(
        model=model_name,
        api_key=api_key,
        base_url=cfg.get("base_url"),
        temperature=cfg.get("temperature", 0.0),
        max_tokens=cfg.get("max_tokens", 4096),
        streaming=True,
        model_kwargs=model_kwargs,
    )
