# Phase 2: 一句话建图表 — 详细设计文档

> 状态：设计阶段
> 前置依赖：Phase 1 (NL2SQL) 已完成
> 目标：用户输入自然语言 → Agent 自动创建图表 → 返回 Explore URL

---

## 一、目标与价值

用户在 AI 对话中输入如 "用柱状图展示各部门人数"，Agent 自动完成：

```
用户自然语言
    ↓
NL2SQL → 生成查询 SQL
    ↓
分析数据类型（维度列、度量列）
    ↓
选择合适的 viz_type（柱状图/折线图/饼图/表格...）
    ↓
查找或创建 Dataset（SqlaTable）
    ↓
构造 form_data params（metrics, groupby, time_range...）
    ↓
调用 CreateChartCommand → 创建图表
    ↓
返回 Explore URL → 用户点击查看/编辑图表
```

---

## 二、架构设计

### 2.1 模块依赖图

```
                    ┌─────────────────┐
                    │   ChartAgent     │
                    │ (新增 agent)     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───┐  ┌──────▼──────┐  ┌───▼───────────┐
     │ GetSchema  │  │ ExecuteSql  │  │ SearchDatasets│
     │ (Phase 1)  │  │ (Phase 1)   │  │ (新增 tool)   │
     └────────────┘  └─────────────┘  └───┬───────────┘
                                           │
                                    ┌──────▼──────┐
                                    │ CreateChart  │
                                    │ (新增 tool)  │
                                    └─────────────┘
```

### 2.2 新增文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `superset/ai/agent/chart_agent.py` | Agent | 图表创建 Agent |
| `superset/ai/tools/create_chart.py` | Tool | 图表创建工具 |
| `superset/ai/tools/search_datasets.py` | Tool | 数据集查找工具 |
| `superset/ai/prompts/chart_creation.py` | Prompt | 图表创建系统提示词 |

### 2.3 需修改的文件

| 文件 | 修改内容 |
|------|----------|
| `superset/ai/schemas.py` | `AiChatPostSchema.agent_type` 添加 `"chart"` 选项 |
| `superset/ai/commands/chat.py` | `_AGENT_MAP` 添加 `"chart": ChartAgent` |
| `superset/ai/tasks.py` | 导入 `ChartAgent` |
| `superset/ai/config.py` | 添加 `get_chart_default_row_limit()` 等配置 |
| `superset-frontend/.../featureFlags.ts` | 添加 `AI_AGENT_CHART` 枚举值 |
| `docker/pythonpath_dev/superset_config_docker.py` | 添加 `AI_AGENT_CHART` Feature Flag |

---

## 三、详细设计

### 3.1 ChartAgent (`superset/ai/agent/chart_agent.py`)

```python
class ChartAgent(BaseAgent):
    """一句话建图表 Agent。

    工具链: get_schema → execute_sql → search_datasets → create_chart
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        context: ConversationContext,
        database_id: int,
        schema_name: str | None = None,
    ) -> None:
        tools: list[BaseTool] = [
            GetSchemaTool(database_id=database_id, default_schema=schema_name),
            ExecuteSqlTool(database_id=database_id),
            SearchDatasetsTool(database_id=database_id, schema_name=schema_name),
            CreateChartTool(database_id=database_id, schema_name=schema_name),
        ]
        super().__init__(provider, context, tools)
        self._database_id = database_id
        self._schema_name = schema_name

    def get_system_prompt(self) -> str:
        prompt = CHART_CREATION_SYSTEM_PROMPT
        if self._schema_name:
            prompt += f"\n\n当前数据库 schema: {self._schema_name}"
        return prompt
```

**与 NL2SQLAgent 的区别：**
- 多了 `SearchDatasetsTool` 和 `CreateChartTool` 两个工具
- System prompt 不同，强调图表类型选择和参数构造
- 最终目标不是返回 SQL，而是创建图表并返回 URL

### 3.2 SearchDatasetsTool (`superset/ai/tools/search_datasets.py`)

```python
class SearchDatasetsTool(BaseTool):
    """查找 Superset 中的数据集（SqlaTable），获取 datasource_id。"""

    name = "search_datasets"
    description = (
        "Search for existing datasets (tables) in Superset. "
        "Returns the datasource_id needed to create charts. "
        "If the table is not registered, you can use create_chart "
        "which will register it automatically."
    )

    parameters_schema = {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Exact table name to search for",
            },
        },
    }

    def __init__(self, database_id: int, schema_name: str | None = None) -> None:
        self._database_id = database_id
        self._schema_name = schema_name

    def run(self, arguments: dict[str, Any]) -> str:
        table_name = arguments.get("table_name", "")
        if not table_name:
            return "Error: table_name is required"

        table = (
            db.session.query(SqlaTable)
            .filter(
                SqlaTable.database_id == self._database_id,
                SqlaTable.table_name == table_name,
            )
        )
        if self._schema_name:
            table = table.filter(SqlaTable.schema == self._schema_name)
        table = table.first()

        if not table:
            # 返回可用表列表，帮助 LLM 纠正
            all_tables = (
                db.session.query(SqlaTable.table_name)
                .filter(SqlaTable.database_id == self._database_id)
                .limit(30)
                .all()
            )
            available = ", ".join(sorted(t[0] for t in all_tables))
            return (
                f"Dataset '{table_name}' not found in database. "
                f"Available datasets: {available}"
            )

        # 返回关键信息：datasource_id + 列信息
        columns = [
            {
                "name": col.column_name,
                "type": str(col.type),
                "groupby": col.groupby,
                "filterable": col.filterable,
            }
            for col in table.columns
        ]
        metrics = [
            {"name": m.metric_name, "expression": m.expression}
            for m in table.metrics
        ]

        return json.dumps({
            "datasource_id": table.id,
            "datasource_type": "table",
            "table_name": table.table_name,
            "schema": table.schema,
            "columns": columns[:30],     # 限制列数避免 context 过长
            "metrics": metrics[:20],
        }, ensure_ascii=False, indent=2)
```

**核心职责：**
- 通过 `database_id + table_name` 查找 `SqlaTable`
- 返回 `datasource_id`（图表创建所需的关键字段）
- 同时返回列定义和已有 metrics，供 LLM 构造 `params`

### 3.3 CreateChartTool (`superset/ai/tools/create_chart.py`)

```python
class CreateChartTool(BaseTool):
    """通过 Superset 的 CreateChartCommand 创建图表。"""

    name = "create_chart"
    description = (
        "Create a chart (visualization) in Superset. "
        "Requires datasource_id, viz_type, and params (form_data JSON)."
    )

    parameters_schema = {
        "type": "object",
        "required": ["slice_name", "viz_type", "datasource_id", "params"],
        "properties": {
            "slice_name": {
                "type": "string",
                "description": "Chart title/name",
            },
            "viz_type": {
                "type": "string",
                "description": "Visualization type (e.g., echarts_bar, pie, table, echarts_timeseries_line)",
            },
            "datasource_id": {
                "type": "integer",
                "description": "Dataset ID from search_datasets",
            },
            "params": {
                "type": "object",
                "description": "Chart form_data (metrics, groupby, time_range, etc.)",
            },
            "description": {
                "type": "string",
                "description": "Optional chart description",
            },
        },
    }

    def __init__(self, database_id: int, schema_name: str | None = None) -> None:
        self._database_id = database_id
        self._schema_name = schema_name

    def run(self, arguments: dict[str, Any]) -> str:
        slice_name = arguments.get("slice_name", "")
        viz_type = arguments.get("viz_type", "")
        datasource_id = arguments.get("datasource_id")
        params_dict = arguments.get("params", {})
        description = arguments.get("description", "")

        if not all([slice_name, viz_type, datasource_id]):
            return "Error: slice_name, viz_type, and datasource_id are required"

        # 如果 datasource 不存在，先注册
        datasource = self._ensure_dataset(datasource_id)

        # 构造 form_data
        form_data = {
            "viz_type": viz_type,
            "datasource": f"{datasource.id}__table",
            **params_dict,
        }

        # 调用 CreateChartCommand
        try:
            chart_data = {
                "slice_name": slice_name,
                "description": description,
                "viz_type": viz_type,
                "params": json.dumps(form_data),
                "datasource_id": datasource.id,
                "datasource_type": "table",
            }
            command = CreateChartCommand(g.user, chart_data)
            chart = command.run()
        except Exception as exc:
            return f"Error creating chart: {exc}"

        # 返回图表信息 + URL
        explore_url = f"/explore/?slice_id={chart.id}"
        return json.dumps({
            "chart_id": chart.id,
            "slice_name": chart.slice_name,
            "viz_type": viz_type,
            "explore_url": explore_url,
            "message": f"Chart '{slice_name}' created successfully. "
                       f"View at: {explore_url}",
        }, ensure_ascii=False)

    def _ensure_dataset(self, datasource_id: int) -> SqlaTable:
        """确保数据集存在，如果未注册则自动注册。"""
        table = (
            db.session.query(SqlaTable)
            .filter(SqlaTable.id == datasource_id)
            .first()
        )
        if table:
            return table

        # 尝试自动注册（同 superset/examples/helpers.py 模式）
        raise ValueError(
            f"Dataset with id {datasource_id} not found. "
            "Use search_datasets first to find the correct datasource_id."
        )
```

**核心职责：**
- 接收 LLM 构造的 `viz_type` + `params`
- 调用 Superset 原生的 `CreateChartCommand` 创建图表
- 返回图表 ID 和 Explore URL

### 3.4 Chart Creation Prompt (`superset/ai/prompts/chart_creation.py`)

```python
CHART_CREATION_SYSTEM_PROMPT = """\
You are a data visualization expert integrated into Apache Superset. \
Your job is to help users create charts from their data using natural language.

## Rules
1. **Always call `search_datasets` first** to find the datasource_id for the \
target table, then call `get_schema` to understand the columns.
2. Only generate SELECT queries. Never generate DDL/DML.
3. Choose the appropriate viz_type based on the user's request and data characteristics.
4. Construct proper form_data params for the chosen viz_type.
5. After creating the chart, present the explore_url to the user.

## Visualization Type Guide

### When to use each viz_type:

| User Wants | Data Characteristics | viz_type | Key params |
|---|---|---|---|
| Bar chart comparison | Categorical x-axis, numeric y-axis | `echarts_bar` | x_axis, metrics, groupby |
| Column chart (vertical bars) | Same as bar | `echarts_bar` | x_axis, metrics, groupby |
| Time trend | Date/time x-axis | `echarts_timeseries_line` | granularity_sqla, metrics, groupby, time_range |
| Time trend (bars) | Date/time x-axis | `echarts_timeseries_bar` | granularity_sqla, metrics, groupby, time_range |
| Pie/Donut | Parts of a whole | `pie` | metric (singular), groupby |
| Table | Raw/tabular data | `table` | metrics, groupby, all_columns |
| Big number | Single KPI | `big_number_total` | metric (singular) |
| Big number with trend | KPI with trend | `big_number` | metric (singular), granularity_sqla |
| Scatter plot | Two numeric dimensions | `echarts_timeseries_scatter` | metrics, groupby |
| Area chart | Cumulative/stacked over time | `echarts_area` | metrics, groupby, granularity_sqla |
| Heatmap | Matrix of values | `heatmap_v2` | metric, x_axis, groupby |
| Treemap | Hierarchical proportions | `treemap_v2` | metric, groupby |
| Funnel | Sequential conversion | `funnel` | metric, groupby |
| Word cloud | Text frequency | `word_cloud` | metric, series |
| Gauge | Progress toward target | `gauge_chart` | metric |

### form_data params structure:

```json
// Bar chart (categorical)
{
    "x_axis": "category_column",
    "metrics": ["SUM(numeric_column)"],
    "groupby": ["category_column"],
    "row_limit": 100,
    "order_desc": true
}

// Line chart (timeseries)
{
    "granularity_sqla": "date_column",
    "time_range": "100 years ago : now",
    "metrics": ["SUM(numeric_column)"],
    "groupby": ["series_column"]
}

// Pie chart
{
    "metric": "SUM(numeric_column)",
    "groupby": ["category_column"],
    "row_limit": 100
}

// Table
{
    "metrics": ["SUM(numeric_column)"],
    "groupby": ["dimension_column"],
    "all_columns": ["col1", "col2"],
    "row_limit": 100
}

// Big Number
{
    "metric": "SUM(numeric_column)",
    "time_range": "100 years ago : now"
}
```

### Metric format:

Metrics can be either:
1. **Simple aggregate**: Use the string format like `"SUM(column_name)"` or `"COUNT(*)"`
2. **Saved metric**: Use the metric name from search_datasets result (e.g., `"sum__num_boys"`)
3. **Ad-hoc metric** (for complex cases):
```json
{
    "expressionType": "SIMPLE",
    "column": {"column_name": "revenue"},
    "aggregate": "SUM",
    "label": "SUM(revenue)",
    "optionName": "metric_<random_suffix>"
}
```

## Workflow
1. Receive user request (e.g., "用柱状图展示各部门人数")
2. Call `search_datasets` to find the target table's datasource_id
3. Call `get_schema` (with table_name) to get column details
4. Optionally call `execute_sql` to sample data and verify
5. Determine the best viz_type based on the request
6. Construct the form_data params
7. Call `create_chart` with all required parameters
8. Present the result with the explore_url

## Output format
When a chart is created, present it like:
✅ 图表创建成功！

**图表名称：** XXX
**图表类型：** 柱状图
**查看链接：** [打开图表](/explore/?slice_id=XXX)

你也可以点击链接在 Explore 页面中进一步调整图表配置。
"""
```

### 3.5 Prompt 关键设计考量

1. **viz_type 选择指南** — 以表格形式列出常见场景 → viz_type → 参数的映射，减少 LLM 选择错误的概率
2. **form_data 示例** — 提供每种图表类型的完整参数示例
3. **Metric 格式说明** — 支持简单聚合字符串、已保存 metric、ad-hoc metric 三种格式
4. **工作流约束** — 强制先 `search_datasets` → `get_schema` → 可选 `execute_sql` → `create_chart` 的顺序

---

## 四、Schema 变更

### 4.1 AiChatPostSchema (`superset/ai/schemas.py`)

```python
# 修改前
agent_type = fields.String(
    load_default="nl2sql",
    validate=validate.OneOf(["nl2sql"]),
)

# 修改后
agent_type = fields.String(
    load_default="nl2sql",
    validate=validate.OneOf(["nl2sql", "chart"]),
)
```

### 4.2 Agent Map (`superset/ai/commands/chat.py`)

```python
# 修改前
_AGENT_MAP: dict[str, type] = {
    "nl2sql": NL2SQLAgent,
}

# 修改后
_AGENT_MAP: dict[str, type] = {
    "nl2sql": NL2SQLAgent,
    "chart": ChartAgent,
}
```

---

## 五、Feature Flag

### 5.1 后端

```python
FEATURE_FLAGS = {
    "AI_AGENT": True,
    "AI_AGENT_NL2SQL": True,
    "AI_AGENT_CHART": True,    # Phase 2 新增
}
```

### 5.2 前端 (`featureFlags.ts`)

```typescript
export enum FeatureFlag {
    // ...
    AI_AGENT = 'AI_AGENT',
    AI_AGENT_NL2SQL = 'AI_AGENT_NL2SQL',
    AI_AGENT_CHART = 'AI_AGENT_CHART',    // Phase 2 新增
}
```

### 5.3 tasks.py 中的检查

```python
# run_agent_task 中根据 agent_type 选择 Agent 类
agent_type = kwargs.get("agent_type", "nl2sql")

if agent_type == "chart":
    from superset.ai.agent.chart_agent import ChartAgent
    agent_class = ChartAgent
else:
    from superset.ai.agent.nl2sql_agent import NL2SQLAgent
    agent_class = NL2SQLAgent
```

---

## 六、前端变更

### 6.1 图表类型图标/标签显示

在 `AiChatPanel` 中，当 agent_type 为 `chart` 时，展示图表创建结果：

```typescript
// 新增事件类型处理
if (event.type === 'chart_created') {
    // 显示图表卡片：图表名称 + viz_type 图标 + Explore 链接
}
```

### 6.2 事件类型扩展 (`types.ts`)

```typescript
export type AgentEventType =
    | 'thinking'
    | 'text_chunk'
    | 'tool_call'
    | 'tool_result'
    | 'sql_generated'
    | 'chart_created'    // Phase 2 新增
    | 'done'
    | 'error';
```

### 6.3 新增事件：chart_created

当 `CreateChartTool` 成功创建图表后，Agent 发布 `chart_created` 事件：

```python
AgentEvent(
    type="chart_created",
    data={
        "chart_id": chart.id,
        "slice_name": chart.slice_name,
        "viz_type": viz_type,
        "explore_url": f"/explore/?slice_id={chart.id}",
    }
)
```

前端收到此事件后，渲染为可点击的图表卡片，点击直接跳转到 Explore 页面。

### 6.4 Agent 选择器（可选增强）

在 AI Chat 面板顶部添加 Agent 类型切换：

- **NL2SQL** — 仅生成 SQL
- **建图表** — 创建完整图表

通过 `AiChatPostSchema.agent_type` 字段传递选择。

---

## 七、安全考量

| 维度 | 策略 |
|------|------|
| 权限继承 | `CreateChartCommand` 继承当前用户权限，无权创建图表的用户会收到错误 |
| 数据集访问 | `SearchDatasetsTool` 仅返回用户有权访问的数据库中的数据集 |
| 输入验证 | `CreateChartTool` 验证 `viz_type` 必须在白名单内 |
| 图表数量限制 | 单个会话最多创建 10 个图表（防止滥用） |
| Feature Flag | `AI_AGENT_CHART` 可独立关闭，不影响 Phase 1 |

---

## 八、测试计划

### 8.1 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| `tests/unit_tests/ai/test_search_datasets.py` | Mock SqlaTable 查询，验证 datasource_id 返回 |
| `tests/unit_tests/ai/test_create_chart.py` | Mock `CreateChartCommand`，验证 params 构造正确 |
| `tests/unit_tests/ai/test_chart_agent.py` | Mock LLM 响应，验证工具调用顺序 |
| `tests/unit_tests/ai/test_chart_prompt.py` | 验证 prompt 中包含正确的 viz_type 指南 |

### 8.2 集成测试

```bash
# 1. 柱状图
curl -X POST /api/v1/ai/chat/ \
  -d '{"message": "用柱状图展示各部门人数", "database_id": 1, "agent_type": "chart"}'

# 2. 饼图
curl -X POST /api/v1/ai/chat/ \
  -d '{"message": "饼图显示各性别出生人数比例", "database_id": 1, "agent_type": "chart"}'

# 3. 折线图
curl -X POST /api/v1/ai/chat/ \
  -d '{"message": "折线图展示2000-2008年每年男孩出生人数趋势", "database_id": 1, "agent_type": "chart"}'

# 4. 大数字
curl -X POST /api/v1/ai/chat/ \
  -d '{"message": "显示总出生人数", "database_id": 1, "agent_type": "chart"}'
```

### 8.3 Web UI 验证

```
1. 打开 SQL Lab → 点击 AI 按钮
2. 选择 "建图表" 模式
3. 输入 "用柱状图展示各部门人数"
4. 验证：
   - Agent 调用 search_datasets 找到 datasource_id
   - Agent 调用 get_schema 获取列信息
   - Agent 选择 echarts_bar 作为 viz_type
   - 图表创建成功，返回 explore_url
   - 点击 explore_url 可在 Explore 页面查看图表
   - 图表参数（metrics, groupby）正确
```

---

## 九、实施步骤

### Step 1: 创建 SearchDatasetsTool (约 1 小时)

- 创建 `superset/ai/tools/search_datasets.py`
- 实现 `SqlaTable` 查询逻辑
- 处理未找到数据集的情况（返回可用列表）

### Step 2: 创建 CreateChartTool (约 1.5 小时)

- 创建 `superset/ai/tools/create_chart.py`
- 集成 `CreateChartCommand`
- 构造 form_data 并创建图表
- 返回 explore_url

### Step 3: 编写 Chart Creation Prompt (约 1 小时)

- 创建 `superset/ai/prompts/chart_creation.py`
- 包含完整的 viz_type 选择指南
- 包含 form_data 参数示例

### Step 4: 创建 ChartAgent (约 0.5 小时)

- 创建 `superset/ai/agent/chart_agent.py`
- 组合 4 个工具（get_schema + execute_sql + search_datasets + create_chart）
- 注册到 `_AGENT_MAP`

### Step 5: 更新 Schema 和 Config (约 0.5 小时)

- 修改 `schemas.py` 添加 `"chart"` agent_type
- 添加 `AI_AGENT_CHART` Feature Flag
- 更新 Celery task 导入

### Step 6: 前端扩展 (约 1 小时)

- 扩展 `AgentEventType` 添加 `chart_created`
- 在 `AiChatPanel` 中处理图表创建事件
- 显示可点击的图表链接卡片

### Step 7: 测试和调优 (约 1 小时)

- API 测试（柱状图、饼图、折线图）
- Web UI 测试（Playwright MCP）
- Prompt 调优（根据实际 LLM 输出质量调整）

**总计预估：约 6.5 小时**

---

## 十、风险和缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| LLM 选择错误的 viz_type | 中 | 低 | Prompt 中提供详细的选择指南 + 白名单校验 |
| form_data 参数构造错误 | 中 | 中 | 提供 params 示例 + CreateChartCommand 自带验证 |
| 数据集未注册（SqlaTable 不存在） | 高 | 高 | search_datasets 返回可用列表，引导 LLM 使用已有数据集 |
| 图表创建权限不足 | 低 | 低 | 继承用户权限，返回友好的权限错误提示 |
| LLM 生成的 metric 格式不兼容 | 中 | 中 | 支持 SIMPLE 聚合字符串格式（如 `"SUM(col)"`），降低复杂度 |

---

## 十一、与 Phase 1 的关系

Phase 2 完全构建在 Phase 1 的基础设施之上：

| Phase 1 组件 | Phase 2 复用方式 |
|-------------|----------------|
| `BaseAgent` (ReAct 循环) | `ChartAgent` 直接继承 |
| `BaseTool` | `SearchDatasetsTool` / `CreateChartTool` 继承 |
| `GetSchemaTool` | ChartAgent 直接使用 |
| `ExecuteSqlTool` | ChartAgent 直接使用（用于数据采样验证） |
| `AiStreamManager` | 复用同一事件流通道 |
| `ConversationContext` | 复用同一会话历史管理 |
| `AiAgentRestApi` | 复用 `/chat/` 和 `/events/` 端点 |
| 前端 `useAiChat` Hook | 复用轮询逻辑，扩展事件类型处理 |

**零基础设施变更** — Phase 2 仅需新增 Agent + Tools + Prompt，底层框架完全复用。
