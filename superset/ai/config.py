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
"""AI Agent configuration helpers."""

from flask import current_app


def get_ai_config(key: str, default: object = None) -> object:
    """Read an AI-related config value from ``current_app.config``."""
    return current_app.config.get(key, default)


def get_llm_providers() -> dict:
    """Return the ``AI_LLM_PROVIDERS`` config dict."""
    return get_ai_config("AI_LLM_PROVIDERS", {})


def get_default_provider_name() -> str:
    """Return the configured default LLM provider name."""
    return get_ai_config("AI_LLM_DEFAULT_PROVIDER", "openai")


def get_max_turns() -> int:
    """Return the max ReAct loop turns."""
    return int(get_ai_config("AI_AGENT_MAX_TURNS", 10))


def get_agent_timeout() -> int:
    """Return the agent execution timeout in seconds."""
    return int(get_ai_config("AI_AGENT_TIMEOUT", 60))


def get_max_context_rounds() -> int:
    """Return the max conversation rounds kept in context."""
    return int(get_ai_config("AI_AGENT_MAX_CONTEXT_ROUNDS", 20))


def get_stream_channel_prefix() -> str:
    """Return the Redis stream channel prefix for AI events."""
    return str(get_ai_config("AI_AGENT_STREAM_CHANNEL_PREFIX", "ai-agent-"))


def use_langchain() -> bool:
    """Return True if the LangChain agent path should be used."""
    return bool(get_ai_config("AI_AGENT_USE_LANGCHAIN", False))


def use_stategraph() -> bool:
    """Return True if chart/dashboard agents should use StateGraph."""
    return bool(get_ai_config("AI_AGENT_USE_STATEGRAPH", False))
