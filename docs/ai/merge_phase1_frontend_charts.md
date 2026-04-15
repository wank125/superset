# Merge Phase 1: Frontend Chart Component Migration

> Status: COMPLETE
> Branch: feature/supersonic
> Estimated: 4-5 weeks
> Source: SuperSonic `webapp/packages/chat-sdk/src/components/ChatMsg/`

## Goal

Render charts (KPI cards, trend lines, bar/pie charts, tables) inline in the AI chat panel, with drill-down interactions and suggested follow-up questions.

## Key Findings

- SuperSonic uses **ECharts 5.4.2** (same as Superset) — no library swap needed
- `MsgDataType` has 20+ fields, not just `queryColumns` + `queryResults`
- Components use `axios` for API calls (no UMI routing deps)
- Uses standard **antd 5.17.4** (not antd-mobile)

## Tasks

### 1.1 Data Format Adapter (5 days)

**File**: `superset-frontend/src/features/ai/utils/chatMsgAdapter.ts`

Map Superset `execute_sql` result → SuperSonic `MsgDataType`:
- `queryColumns`: column type inference (DATE/NUMBER/CATEGORY)
- `queryResults`: direct pass-through
- `aggregateInfo`: synthesize from single-row results (KPI mode)
- `chatContext`: construct minimal context (dimensions, metrics, dateInfo)
- `recommendedDimensions`: extract from column metadata

### 1.2 Chart Components Port (8 days)

Port from SuperSonic, replace API calls with callback props:

| Source | Target | Days |
|--------|--------|------|
| `MetricCard/index.tsx` | `charts/KpiCard.tsx` | 1 |
| `MetricCard/PeriodCompareItem.tsx` | `charts/PeriodCompareItem.tsx` | 0.5 |
| `MetricTrend/MetricTrendChart.tsx` | `charts/TrendChart.tsx` | 2 |
| `Bar/index.tsx` | `charts/BarChart.tsx` | 1 |
| `Pie/index.tsx` + `PieChart.tsx` | `charts/PieChart.tsx` | 1 |
| `Table/index.tsx` | `charts/DataTable.tsx` | 2 |
| `RecommendOptions/index.tsx` | `SuggestQuestions.tsx` | 0.5 |

### 1.3 AiInlineChart Container (3 days)

**File**: `superset-frontend/src/features/ai/components/AiInlineChart.tsx`

- Auto-detect chart type from data shape
- Render appropriate chart component
- Show insight text + suggested questions

### 1.4 Integration (3 days)

- Modify `AiMessageBubble.tsx` to render `AiInlineChart`
- Modify `useAiChat.ts` to handle `data_analyzed` event with query result
- Update `types.ts` with new message fields
- Add `_generate_suggest_questions()` to `nodes_child.py`

### 1.5 Tests (2 days)

- Unit tests for `chatMsgAdapter.ts`
- Render tests for `AiInlineChart`
- Integration test for data flow

## Acceptance Criteria

- [x] "今日销售额多少" → KPI card with period comparison
- [x] "各区域销售趋势" → trend line chart
- [x] "各渠道占比" → pie chart
- [x] "销售明细" → data table with drill-down
- [x] Each result shows 3 suggested follow-up questions

## Implementation Summary

### Frontend Files
- `src/features/ai/utils/chatMsgAdapter.ts` — SQL result → chart data adapter + chart type inference
- `src/features/ai/utils/useECharts.ts` — Shared ECharts hook with ResizeObserver
- `src/features/ai/components/AiInlineChart.tsx` — Auto-detect chart type container
- `src/features/ai/components/charts/KpiCard.tsx` — KPI card with period comparison
- `src/features/ai/components/charts/PeriodCompareItem.tsx` — 环比/同比 display
- `src/features/ai/components/charts/TrendChart.tsx` — ECharts line chart
- `src/features/ai/components/charts/BarChart.tsx` — ECharts bar chart
- `src/features/ai/components/charts/PieChart.tsx` — ECharts donut chart
- `src/features/ai/components/charts/DataTable.tsx` — Table with drill-down links
- `src/features/ai/components/SuggestQuestions.tsx` — Follow-up question chips

### Backend Files
- `superset/ai/graph/nodes_child.py` — KPI insight + statistics generation, suggest questions
- `superset/ai/graph/runner.py` — data_analyzed event with columns/rows/statistics
- `superset/ai/graph/state.py` — statistics field in SingleChartState

### Tests
- `chatMsgAdapter.test.ts` — 11 unit tests
- `AiInlineChart.test.tsx` — 7 render tests
- `KpiCard.test.tsx` — 6 component tests
- `dataFlow.test.ts` — 6 integration tests
