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
"""Tests for LLM providers."""

import json
from unittest.mock import MagicMock, patch

from superset.ai.llm.types import LLMMessage, ToolCall


class TestOpenAIProvider:
    """Tests for OpenAI provider."""

    @patch("superset.ai.llm.openai_provider.httpx")
    def test_chat_returns_response(self, mock_httpx):
        from superset.ai.llm.openai_provider import OpenAIProvider

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Hello!", "role": "assistant"},
                    "finish_reason": "stop",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = OpenAIProvider({"model": "gpt-4o", "temperature": 0})
        result = provider.chat(
            [LLMMessage(role="user", content="Hi")],
        )

        assert result.content == "Hello!"
        assert result.finish_reason == "stop"
        assert result.tool_calls is None

    @patch("superset.ai.llm.openai_provider.httpx")
    def test_chat_with_tool_calls(self, mock_httpx):
        from superset.ai.llm.openai_provider import OpenAIProvider

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_schema",
                                    "arguments": '{"database_id": 1}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = OpenAIProvider({"model": "gpt-4o"})
        result = provider.chat(
            [LLMMessage(role="user", content="Show me the schema")],
            tools=[{"type": "function", "function": {"name": "get_schema"}}],
        )

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_schema"
        assert result.tool_calls[0].arguments == {"database_id": 1}


class TestAnthropicProvider:
    """Tests for Anthropic provider."""

    @patch("superset.ai.llm.anthropic_provider.httpx")
    def test_chat_returns_response(self, mock_httpx):
        from superset.ai.llm.anthropic_provider import AnthropicProvider

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = AnthropicProvider({"model": "claude-sonnet-4-20250514"})
        result = provider.chat(
            [LLMMessage(role="user", content="Hi")],
        )

        assert result.content == "Hello!"
        assert result.finish_reason == "end_turn"

    def test_split_messages_extracts_system_prompt(self):
        from superset.ai.llm.anthropic_provider import AnthropicProvider

        messages = [
            LLMMessage(role="system", content="You are a helpful assistant"),
            LLMMessage(role="user", content="Hi"),
        ]
        system, api_msgs = AnthropicProvider._split_messages(messages)
        assert system == "You are a helpful assistant"
        assert len(api_msgs) == 1
        assert api_msgs[0]["role"] == "user"


class TestOllamaProvider:
    """Tests for Ollama provider."""

    @patch("superset.ai.llm.ollama_provider.httpx")
    def test_chat_returns_response(self, mock_httpx):
        from superset.ai.llm.ollama_provider import OllamaProvider

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": "Hello!", "role": "assistant"},
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        provider = OllamaProvider({"model": "llama3"})
        result = provider.chat(
            [LLMMessage(role="user", content="Hi")],
        )

        assert result.content == "Hello!"
        assert result.finish_reason == "stop"


class TestPluginRegistration:
    """Test that providers auto-register."""

    def test_providers_registered(self):
        from superset.ai.llm.base import BaseLLMProvider

        names = {p.provider_name for p in BaseLLMProvider.plugins}
        assert "openai" in names
        assert "anthropic" in names
        assert "ollama" in names

    @patch("superset.ai.llm.registry.get_llm_providers")
    @patch("superset.ai.llm.registry.get_default_provider_name")
    def test_registry_get_provider(self, mock_default, mock_providers):
        from superset.ai.llm.registry import get_provider

        mock_default.return_value = "openai"
        mock_providers.return_value = {
            "openai": {"model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
        }

        provider = get_provider()
        assert provider.provider_name == "openai"

    @patch("superset.ai.llm.registry.get_llm_providers")
    def test_registry_unknown_provider_raises(self, mock_providers):
        from superset.ai.llm.registry import get_provider

        mock_providers.return_value = {"openai": {}}
        import pytest

        with pytest.raises(ValueError, match="not configured"):
            get_provider("nonexistent")
