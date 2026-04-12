# Phase 8 终稿：LangGraph StateGraph 集成（已上线版本）

> 归档日期：2026-04-12
> 状态：已实现、已部署、E2E 测试通过

---

## 一、流程总览

### 父图（Dashboard级编排）

```
START
  ↓
parse_request        [LLM] NL → 结构化 goal
  ↓
search_dataset       [Code] 调用 SearchDatasetsTool
  ↓
select_dataset       [Code] 唯一→自动选，多个→规则排序自动选，空→错误
  ↓
read_schema          [Code] schema_raw → schema_summary
  ↓
plan_dashboard       [LLM] 规划 N 张图的意图列表
  ↓
single_chart_subgraph [子图，循环 N 次]
  ↓
create_dashboard     [Code] 4条显式前置条件 + 幂等保护
  ↓
END
```

### 子图（单图生成）

```
plan_query           [LLM] goal + schema_summary → sql_plan
  ↓
validate_sql         [Code] 编译 SQL + 静态校验
  ↓ 字段不存在/语法错 → 回 plan_query（带 error hint, max 3次）
execute_query        [Code, RetryPolicy] 执行 SQL
  ↓ SQL逻辑错 → 回 plan_query
analyze_result       [Code] 原始结果 → query_result_summary (含 suitability_flags)
  ↓
select_chart         [LLM] 摘要+特征 → viz_type + 语义参数
  ↓
normalize_chart_params [Code] 语义参数 → Superset form_data，6条规则
  ↓ 编译失败 ↘
  ↓           repair_chart_params [LLM] → 修正 chart_plan
  ↓           ↙ (最多3次)
create_chart         [Code] 幂等创建 + 写入父图 created_charts
```

---

## 二、State 设计

### 父图 State

```python
class DashboardState(TypedDict, total=False):
    # 输入
    request: str
    request_id: str
    session_id: str
    user_id: int
    database_id: int
    schema_name: str | None
    agent_mode: str              # "chart" | "dashboard"

    # parse_request 输出
    goal: dict[str, Any]

    # search_dataset + select_dataset 输出
    dataset_candidates: list[dict[str, Any]]
    selected_dataset: dict[str, Any] | None

    # read_schema 输出
    schema_raw: dict[str, Any] | None
    schema_summary: SchemaSummary | None

    # plan_dashboard 输出
    chart_intents: list[ChartIntent]
    current_chart_index: int

    # create_chart 累加（operator.add reducer）
    created_charts: Annotated[list[dict[str, Any]], operator.add]

    # create_dashboard 输出
    created_dashboard: dict[str, Any] | None

    # 全局错误
    last_error: dict[str, Any] | None
```

### 子图 State

```python
class SingleChartState(TypedDict, total=False):
    chart_intent: ChartIntent
    schema_summary: SchemaSummary
    database_id: int
    request_id: str

    sql_plan: dict[str, Any] | None
    sql: str | None
    sql_valid: bool
    query_result_raw: str | None
    query_result_summary: ResultSummary | None
    chart_plan: ChartPlan | None
    chart_form_data: dict[str, Any] | None
    created_chart: dict[str, Any] | None

    last_error: dict[str, Any] | None
    repair_attempts: int
    sql_attempts: int
```

---

## 三、文件清单

```
superset/ai/graph/
├── __init__.py          # 包初始化
├── state.py             # DashboardState + SingleChartState TypedDict
├── nodes_parent.py      # P1-P6: parse/search/select/schema/plan/dashboard
├── nodes_child.py       # C1-C8: plan_query/validate/execute/analyze/select/normalize/repair/create
├── normalizer.py        # compile_superset_form_data（6条规则）
├── llm_helpers.py       # llm_call_json / llm_call_json_list
├── builder.py           # build_chart_graph / build_dashboard_graph
└── runner.py            # run_graph + _emit_node_events（节点级实时推送）
```

### 修改的外部文件

| 文件 | 改动 |
|------|------|
| `superset/ai/tasks.py` | StateGraph 路径调用 `run_graph()` |
| `superset/ai/config.py` | `use_stategraph()` 配置函数 |

---

## 四、关键设计决策

### 4.1 select_dataset：自动选择而非 interrupt

原始设计使用 LangGraph `interrupt()` 实现人机交互选择数据集，但实测发现：
- `interrupt()` 需要 checkpointer 支持
- `run_graph()` 使用 `stream()` 模式，无 checkpointer
- 在 Celery 任务流中不适合暂停等待用户输入

**实际方案**：基于评分规则自动选择最佳候选（精确匹配 > 前缀匹配 > 包含匹配），空候选返回错误。

### 4.2 子图 State 映射

父图和子图使用不同的 TypedDict（`DashboardState` vs `SingleChartState`），需要 `_make_subgraph_wrapper()` 做：
1. 从父图提取当前 chart_intent + schema_summary
2. 构建 SingleChartState 输入
3. 同步调用子图
4. 将 created_chart 通过 `operator.add` 累加回父图

### 4.3 RetryPolicy

- `execute_query`：对 `TimeoutError / ConnectionError / OSError` 自动重试最多 2 次
- `create_chart`：通用重试最多 2 次（Superset API 瞬态错误）

### 4.4 幂等保护

- `create_chart`：10 分钟内同名同类型同数据源的图表自动复用
- `create_dashboard`：30 分钟内同 request_id 的仪表板自动复用

### 4.5 事件流

使用 `stream_mode="updates"` 实现节点级实时推送：
- 父图节点 → thinking 进度事件
- 子图通过 wrapper 汇总 → chart_created / error 事件
- create_dashboard → dashboard_created 事件

---

## 五、E2E 测试结果

### Chart 模式

```
请求: "查询birth_names表的出生人数趋势"
事件流:
  [thinking] 请求解析完成
  [thinking] 数据集搜索完成
  [thinking] 数据集已确定
  [thinking] Schema 读取完成
  [thinking] 图表规划完成
  [chart_created] chart_id=347, echarts_timeseries_line
  [done]
总计: 7 events, LLM 调用 6 次
```

### Dashboard 模式

```
请求: "创建birth_names的仪表板，包含趋势图和性别分布饼图"
事件流:
  [thinking] 请求解析完成
  [thinking] 数据集搜索完成
  [thinking] 数据集已确定
  [thinking] Schema 读取完成
  [thinking] 图表规划完成
  [chart_created] chart_id=347, echarts_timeseries_line, "出生人数趋势"
  [chart_created] chart_id=348, pie, "性别分布"
  [dashboard_created] dashboard_id=40, "birth_names 仪表板", 2 charts
  [done]
总计: 9 events
```
