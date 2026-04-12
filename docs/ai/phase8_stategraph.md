# Phase 8: LangGraph StateGraph Agent

## Overview

Replaces the ReAct loop (LegacyAgentRunner / LangChainAgentRunner) with a deterministic LangGraph StateGraph for chart and dashboard generation. The graph provides explicit flow control, node-level real-time event streaming, and code-level validation at every step.

## Architecture

### Parent Graph (Dashboard orchestration)

```
START → parse_request → search_dataset → select_dataset → read_schema
→ plan_dashboard → [single_chart_subgraph × N] → create_dashboard → END
```

### Child Subgraph (Single chart generation)

```
START → plan_query → validate_sql → execute_query → analyze_result
→ select_chart → normalize_chart_params → create_chart → END
                           ↘ repair_chart_params ↗ (up to 3 repairs)
```

## Files

```
superset/ai/graph/
├── __init__.py          # Package init
├── state.py             # DashboardState + SingleChartState TypedDicts
├── llm_helpers.py       # llm_call_json() / llm_call_json_list()
├── normalizer.py        # compile_superset_form_data() (6 rules)
├── nodes_child.py       # 8 child graph nodes
├── nodes_parent.py      # 6 parent graph nodes
├── builder.py           # Graph assembly
└── runner.py            # run_graph() + node-level event streaming
```

## Key Design Decisions

1. **Command(goto=...) routing**: All edges are determined by nodes returning `Command` objects, enabling conditional loops (repair, SQL retry) without external state management.

2. **suitability_flags**: Code-derived data shape analysis (good_for_trend, good_for_composition, etc.) — no LLM needed for data analysis.

3. **interrupt()**: Human-in-the-loop for dataset selection when multiple candidates exist with ambiguous scores.

4. **4 preconditions for create_dashboard**: Non-empty charts, all have chart_id, minimum threshold met, idempotency check.

5. **stream_mode="updates"**: Each node completion triggers real-time AgentEvent emission to the frontend.

6. **Node-level event translation**: `_emit_node_events()` maps node outputs to typed events (chart_created, dashboard_created, sql_generated, data_analyzed, thinking, error).

## Integration

`run_graph()` is called from `tasks.py` when the `AI_AGENT_USE_STATEGRAPH` feature flag is enabled. It yields `AgentEvent` objects compatible with the existing `AiStreamManager`.

## Error Handling

- SQL errors: up to 3 retries (plan_query → validate_sql loop)
- Param errors: up to 3 repair attempts (normalize → repair loop)
- Both track attempt counts in state (sql_attempts, repair_attempts)
- Unrecoverable errors route directly to `__end__`
