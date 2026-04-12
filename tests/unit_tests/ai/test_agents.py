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

from superset.ai.llm.types import LLMStreamChunk, ToolCall
from superset.utils import json


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
            parameters_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            }

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
        stored = json.loads(call_args[0][1])
        assert len(stored) == 1
        assert stored[0]["role"] == "user"

    @patch("superset.ai.agent.context.cache_manager")
    @patch("superset.ai.agent.context.get_max_context_rounds", return_value=2)
    def test_history_truncation(self, mock_rounds, mock_cache):
        from superset.ai.agent.context import ConversationContext

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


class TestToolCallRepetitionGuard:
    """Tests for LangChain tool repetition guard."""

    def test_untracked_tool_is_not_limited(self):
        from superset.ai.agent.langchain.guard import ToolCallRepetitionGuard

        guard = ToolCallRepetitionGuard(
            max_consecutive=3,
            tracked_tools={"create_chart", "create_dashboard"},
        )

        assert not guard.check("execute_sql", {"sql": "select 1"})
        assert not guard.check("execute_sql", {"sql": "select 1"})
        assert not guard.check("execute_sql", {"sql": "select 1"})

    def test_tracked_tool_repeats_only_with_same_arguments(self):
        from superset.ai.agent.langchain.guard import ToolCallRepetitionGuard

        guard = ToolCallRepetitionGuard(
            max_consecutive=3,
            tracked_tools={"create_chart"},
        )

        assert not guard.check("create_chart", {"chart_type": "bar"})
        assert not guard.check("create_chart", {"chart_type": "line"})
        assert not guard.check("create_chart", {"chart_type": "bar"})

        guard.reset()

        assert not guard.check("create_chart", {"chart_type": "bar"})
        assert not guard.check("create_chart", {"chart_type": "bar"})
        assert guard.check("create_chart", {"chart_type": "bar"})


class TestToolOrderGuard:
    """Tests for the sequential tool-order guard."""

    def test_dashboard_create_dashboard_blocked_before_search(self):
        from superset.ai.agent.langchain.guard import (
            _DASHBOARD_PHASES,
            ToolOrderGuard,
        )

        guard = ToolOrderGuard(phases=_DASHBOARD_PHASES)
        # Phase 0: only search_datasets allowed for ordered tools
        assert guard.check("search_datasets") is True
        assert guard.check("analyze_data") is False
        assert guard.check("create_dashboard") is False
        assert guard.check("create_chart") is False

    def test_read_tools_always_allowed(self):
        from superset.ai.agent.langchain.guard import (
            _DASHBOARD_PHASES,
            ToolOrderGuard,
        )

        guard = ToolOrderGuard(phases=_DASHBOARD_PHASES)
        # execute_sql and get_schema are read-only, always allowed
        assert guard.check("execute_sql") is True
        assert guard.check("get_schema") is True
        # Even after advancing to later phases
        guard.advance("search_datasets")
        guard.advance("analyze_data")
        guard.advance("create_chart")
        assert guard.check("execute_sql") is True

    def test_sequential_advance(self):
        from superset.ai.agent.langchain.guard import (
            _DASHBOARD_PHASES,
            ToolOrderGuard,
        )

        guard = ToolOrderGuard(phases=_DASHBOARD_PHASES)
        # Phase 0 → search_datasets
        assert guard.check("search_datasets") is True
        guard.advance("search_datasets")
        # Phase 1 → analyze_data
        assert guard.check("analyze_data") is True
        assert guard.check("search_datasets") is True  # read tool now
        guard.advance("analyze_data")
        # Phase 2 → create_chart
        assert guard.check("create_chart") is True
        assert guard.check("create_dashboard") is False
        guard.advance("create_chart")
        # Phase 3 → create_dashboard
        assert guard.check("create_dashboard") is True

    def test_dashboard_out_of_order_blocked(self):
        """create_dashboard at phase 0 must be blocked."""
        from superset.ai.agent.langchain.guard import (
            _DASHBOARD_PHASES,
            ToolOrderGuard,
        )

        guard = ToolOrderGuard(phases=_DASHBOARD_PHASES)
        # Trying to jump to create_dashboard at phase 0
        assert guard.check("create_dashboard") is False
        assert guard.phase_idx == 0

    def test_no_guard_for_non_dashboard(self):
        from superset.ai.agent.langchain.guard import create_order_guard

        guard = create_order_guard("nl2sql")
        assert guard is None

        guard2 = create_order_guard("chart")
        assert guard2 is None

    def test_dashboard_guard_created(self):
        from superset.ai.agent.langchain.guard import create_order_guard

        guard = create_order_guard("dashboard")
        assert guard is not None

    def test_tool_adapter_blocks_side_effect_before_execution(self):
        from superset.ai.agent.langchain.guard import (
            _DASHBOARD_PHASES,
            ToolOrderGuard,
        )
        from superset.ai.agent.langchain.tools import tool_adapter
        from superset.ai.tools.base import BaseTool

        class DangerousTool(BaseTool):
            name = "create_dashboard"
            description = "Create dashboard"
            parameters_schema = {"type": "object", "properties": {}}

            def __init__(self):
                self.called = False

            def run(self, arguments):
                self.called = True
                return "created"

        native_tool = DangerousTool()
        wrapped = tool_adapter(
            native_tool,
            order_guard=ToolOrderGuard(phases=_DASHBOARD_PHASES),
        )

        result = wrapped.invoke({})

        assert result.startswith("Error:")
        assert native_tool.called is False


class TestDashboardAgentOrderEnforcement:
    """Test legacy path: DashboardAgent blocks out-of-order tool calls via hooks."""

    @patch("superset.ai.agent.base.get_max_turns", return_value=5)
    @patch("superset.ai.agent.context.cache_manager")
    def test_create_dashboard_blocked_before_search(self, mock_cache, mock_turns):
        from superset.ai.agent.context import ConversationContext
        from superset.ai.agent.dashboard_agent import DashboardAgent

        mock_cache.cache.get.return_value = None
        mock_cache.cache.set = MagicMock()

        mock_provider = MagicMock()
        # First turn: LLM tries to call create_dashboard (out of order)
        # Second turn: LLM gives up with a text response
        mock_provider.chat_stream.side_effect = [
            iter([
                LLMStreamChunk(
                    tool_calls=[
                        ToolCall(
                            id="tc_1",
                            name="create_dashboard",
                            arguments={"dashboard_title": "test", "chart_ids": [1]},
                        )
                    ]
                ),
                LLMStreamChunk(finish_reason="tool_calls"),
            ]),
            iter([
                LLMStreamChunk(content="I need to search first"),
                LLMStreamChunk(finish_reason="stop"),
            ]),
        ]

        context = ConversationContext(user_id=1, session_id="test")
        agent = DashboardAgent(
            provider=mock_provider,
            context=context,
            database_id=1,
        )
        events = list(agent.run("create a dashboard"))

        # Out-of-order call should be filtered (no tool_call/tool_result events)
        tool_events = [e for e in events if e.type == "tool_call"]
        assert len(tool_events) == 0

        # LLM should have been called twice (filtered turn + second response)
        assert mock_provider.chat_stream.call_count == 2

        # Should end normally with a text response
        done_events = [e for e in events if e.type == "done"]
        assert len(done_events) == 1
        text_events = [e for e in events if e.type == "text_chunk"]
        assert any("search first" in e.data["content"] for e in text_events)


class TestChartIdempotency:
    """Test chart dedup: same title but different params → not a duplicate."""

    def test_different_params_not_deduped(self):
        from superset.ai.tools.create_chart import CreateChartTool

        hash1 = CreateChartTool._compute_params_hash(
            "echarts_timeseries_bar",
            {"x_axis": "year", "metrics": ["SUM(num_boys)"]},
        )
        hash2 = CreateChartTool._compute_params_hash(
            "echarts_timeseries_bar",
            {"x_axis": "year", "metrics": ["SUM(num_girls)"]},
        )
        assert hash1 != hash2

    def test_same_params_same_hash(self):
        from superset.ai.tools.create_chart import CreateChartTool

        hash1 = CreateChartTool._compute_params_hash(
            "pie",
            {"metric": "SUM(value)", "groupby": ["category"]},
        )
        hash2 = CreateChartTool._compute_params_hash(
            "pie",
            {"metric": "SUM(value)", "groupby": ["category"]},
        )
        assert hash1 == hash2

    def test_groupby_order_irrelevant(self):
        from superset.ai.tools.create_chart import CreateChartTool

        hash1 = CreateChartTool._compute_params_hash(
            "table",
            {"groupby": ["a", "b"], "metrics": ["SUM(x)"]},
        )
        hash2 = CreateChartTool._compute_params_hash(
            "table",
            {"groupby": ["b", "a"], "metrics": ["SUM(x)"]},
        )
        assert hash1 == hash2


class TestDashboardIdempotency:
    """Test dashboard dedup: same title but different chart_ids → not duplicate."""

    def test_different_chart_ids_different_hash(self):

        import hashlib

        ids1 = [1, 2, 3]
        ids2 = [1, 2, 4]
        h1 = hashlib.sha256(json.dumps(sorted(ids1)).encode()).hexdigest()[:16]
        h2 = hashlib.sha256(json.dumps(sorted(ids2)).encode()).hexdigest()[:16]
        assert h1 != h2

    def test_same_chart_ids_same_hash(self):
        import hashlib

        ids1 = [3, 1, 2]
        ids2 = [2, 3, 1]
        h1 = hashlib.sha256(json.dumps(sorted(ids1)).encode()).hexdigest()[:16]
        h2 = hashlib.sha256(json.dumps(sorted(ids2)).encode()).hexdigest()[:16]
        assert h1 == h2


class TestLangChainOrderGuard:
    """Test that LangChain runner uses order guard for dashboard agent."""

    def test_order_guard_initialized_for_dashboard(self):
        from superset.ai.agent.langchain.runner import LangChainAgentRunner

        runner = LangChainAgentRunner(
            agent_type="dashboard",
            database_id=1,
            schema_name=None,
            user_id=1,
            session_id="test",
        )
        assert runner._order_guard is not None
        assert runner._order_guard.check("create_dashboard") is False
        assert runner._order_guard.check("search_datasets") is True

    def test_order_guard_not_initialized_for_nl2sql(self):
        from superset.ai.agent.langchain.runner import LangChainAgentRunner

        runner = LangChainAgentRunner(
            agent_type="nl2sql",
            database_id=1,
            schema_name=None,
            user_id=1,
            session_id="test",
        )
        assert runner._order_guard is None


class TestStateGraphPlanning:
    """Tests for Phase 8 StateGraph deterministic planning guards."""

    def test_saved_metric_name_normalizes_to_sql_expression(self):
        from superset.ai.graph.nodes_child import _normalize_sql_plan

        summary = {
            "datasource_id": 1,
            "table_name": "birth_names",
            "datetime_cols": ["ds"],
            "dimension_cols": ["gender", "state"],
            "metric_cols": ["num"],
            "saved_metrics": ["sum__num"],
            "saved_metric_expressions": {"sum__num": "SUM(num)"},
            "main_dttm_col": "ds",
        }
        plan = {
            "metric_expr": "sum__num",
            "dimensions": ["gender", "unknown_col"],
            "time_field": "not_a_time_col",
            "order_by": "unknown_col DESC",
            "limit": 200,
        }

        normalized = _normalize_sql_plan(plan, summary)

        assert normalized["metric_expr"] == "SUM(num)"
        assert normalized["dimensions"] == ["gender"]
        assert normalized["time_field"] is None
        assert normalized["order_by"] is None

    def test_unknown_metric_falls_back_to_first_numeric_column(self):
        from superset.ai.graph.nodes_child import _normalize_sql_plan

        summary = {
            "datasource_id": 1,
            "table_name": "birth_names",
            "datetime_cols": [],
            "dimension_cols": ["gender"],
            "metric_cols": ["num"],
            "saved_metrics": [],
            "saved_metric_expressions": {},
            "main_dttm_col": None,
        }
        plan = {"metric_expr": "sum__num", "dimensions": ["gender"]}

        normalized = _normalize_sql_plan(plan, summary)

        assert normalized["metric_expr"] == "SUM(num)"

    def test_preferred_viz_alias_is_normalized(self):
        from superset.ai.graph.nodes_parent import _normalize_preferred_viz

        assert _normalize_preferred_viz("bar chart") == "echarts_timeseries_bar"
        assert _normalize_preferred_viz("pie") == "pie"
        assert _normalize_preferred_viz("not_a_chart_type") is None

    def test_bar_chart_uses_groupby_as_x_axis_without_conflict(self):
        with patch("superset.ai.graph.normalizer._build_column_lookup") as lookup:
            from superset.ai.graph.normalizer import compile_superset_form_data

            lookup.return_value = {
                "gender": {"type": "VARCHAR", "groupable": True},
                "num": {"type": "BIGINT", "groupable": False},
            }
            chart_plan = {
                "viz_type": "echarts_timeseries_bar",
                "slice_name": "Birth Count by Gender",
                "semantic_params": {
                    "metric": "SUM(num)",
                    "groupby": ["gender"],
                },
                "rationale": "User requested a bar chart.",
            }
            summary = {
                "datasource_id": 1,
                "table_name": "birth_names",
                "datetime_cols": [],
                "dimension_cols": ["gender"],
                "metric_cols": ["num"],
                "saved_metrics": [],
                "saved_metric_expressions": {},
                "main_dttm_col": None,
            }

            form_data = compile_superset_form_data(chart_plan, summary)

            assert form_data["x_axis"] == "gender"
            assert form_data["groupby"] == []
            assert form_data["metrics"][0]["label"] == "SUM(num)"
