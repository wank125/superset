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
"""Tests for agent framework."""

from unittest.mock import MagicMock, patch

from superset.ai.agent.events import AgentEvent
from superset.ai.llm.types import LLMMessage, LLMStreamChunk, ToolCall


class TestBaseAgent:
    """Tests for the BaseAgent ReAct loop."""

    @patch("superset.ai.agent.base.get_max_turns", return_value=3)
    @patch("superset.ai.agent.context.cache_manager")
    def test_agent_final_answer_no_tools(self, mock_cache, mock_turns):
        from superset.ai.agent.base import BaseAgent
        from superset.ai.agent.context import ConversationContext

        # Setup mock provider that returns a final answer
        mock_provider = MagicMock()
        mock_provider.chat_stream.return_value = iter([
            LLMStreamChunk(content="SELECT 1"),
            LLMStreamChunk(finish_reason="stop"),
        ])

        # Setup mock context
        mock_cache.cache.get.return_value = None
        mock_cache.cache.set = MagicMock()
        context = ConversationContext(user_id=1, session_id="test")

        # Create a concrete agent
        class TestAgent(BaseAgent):
            def get_system_prompt(self):
                return "test prompt"

        agent = TestAgent(mock_provider, context, tools=[])
        events = list(agent.run("test message"))

        # Should get text_chunk + done
        assert len(events) == 2
        assert events[0].type == "text_chunk"
        assert events[0].data["content"] == "SELECT 1"
        assert events[1].type == "done"

    @patch("superset.ai.agent.base.get_max_turns", return_value=3)
    @patch("superset.ai.agent.context.cache_manager")
    def test_agent_with_tool_call(self, mock_cache, mock_turns):
        from superset.ai.agent.base import BaseAgent
        from superset.ai.agent.context import ConversationContext
        from superset.ai.tools.base import BaseTool

        # Create a mock tool
        class EchoTool(BaseTool):
            name = "echo"
            description = "Echo input"
            parameters_schema = {"type": "object", "properties": {"text": {"type": "string"}}}

            def run(self, arguments):
                return f"Echo: {arguments.get('text', '')}"

        # First call returns tool call, second call returns final answer
        mock_provider = MagicMock()
        mock_provider.chat_stream.side_effect = [
            # First turn: tool call
            iter([
                LLMStreamChunk(
                    tool_calls=[
                        ToolCall(id="tc_1", name="echo", arguments={"text": "hello"})
                    ]
                ),
                LLMStreamChunk(finish_reason="tool_calls"),
            ]),
            # Second turn: final answer
            iter([
                LLMStreamChunk(content="The echo returned: Echo: hello"),
                LLMStreamChunk(finish_reason="stop"),
            ]),
        ]

        mock_cache.cache.get.return_value = None
        mock_cache.cache.set = MagicMock()
        context = ConversationContext(user_id=1, session_id="test")

        class TestAgent(BaseAgent):
            def get_system_prompt(self):
                return "test"

        agent = TestAgent(mock_provider, context, tools=[EchoTool()])
        events = list(agent.run("echo hello"))

        types = [e.type for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text_chunk" in types
        assert "done" in types

    @patch("superset.ai.agent.base.get_max_turns", return_value=3)
    @patch("superset.ai.agent.context.cache_manager")
    def test_agent_handles_llm_error(self, mock_cache, mock_turns):
        from superset.ai.agent.base import BaseAgent
        from superset.ai.agent.context import ConversationContext

        mock_provider = MagicMock()
        mock_provider.chat_stream.side_effect = Exception("API error")

        mock_cache.cache.get.return_value = None
        mock_cache.cache.set = MagicMock()
        context = ConversationContext(user_id=1, session_id="test")

        class TestAgent(BaseAgent):
            def get_system_prompt(self):
                return "test"

        agent = TestAgent(mock_provider, context, tools=[])
        events = list(agent.run("test"))

        assert len(events) == 1
        assert events[0].type == "error"
        assert "API error" in events[0].data["message"]


class TestConversationContext:
    """Tests for ConversationContext."""

    @patch("superset.ai.agent.context.cache_manager")
    @patch("superset.ai.agent.context.get_max_context_rounds", return_value=2)
    def test_add_and_get_history(self, mock_rounds, mock_cache):
        from superset.ai.agent.context import ConversationContext

        mock_cache.cache.get.return_value = None
        ctx = ConversationContext(user_id=1, session_id="s1")

        ctx.add_message("user", "hello")
        call_args = mock_cache.cache.set.call_args
        import json
        stored = json.loads(call_args[0][1])
        assert len(stored) == 1
        assert stored[0]["role"] == "user"

    @patch("superset.ai.agent.context.cache_manager")
    @patch("superset.ai.agent.context.get_max_context_rounds", return_value=2)
    def test_history_truncation(self, mock_rounds, mock_cache):
        from superset.ai.agent.context import ConversationContext
        import json

        # Simulate Redis: cache.get returns the last value set by cache.set
        store: dict[str, str] = {}

        def fake_get(key):
            return store.get(key)

        def fake_set(key, value, timeout=None):
            store[key] = value

        mock_cache.cache.get.side_effect = fake_get
        mock_cache.cache.set.side_effect = fake_set

        ctx = ConversationContext(user_id=1, session_id="s1")

        # Add 5 messages (max rounds=2, so max 4 messages)
        for i in range(5):
            ctx.add_message("user" if i % 2 == 0 else "assistant", f"msg {i}")

        stored = json.loads(store[ctx._key])
        # Should keep only last 4 messages
        assert len(stored) == 4

    @patch("superset.ai.agent.context.cache_manager")
    def test_clear(self, mock_cache):
        from superset.ai.agent.context import ConversationContext

        ctx = ConversationContext(user_id=1, session_id="s1")
        ctx.clear()
        mock_cache.cache.delete.assert_called_once()
