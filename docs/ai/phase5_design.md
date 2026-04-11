# Phase 5 设计文档：智能图表类型选择 + 数据分析

## 1. 背景与目标

### 当前问题
- 图表类型选择完全依赖 LLM 猜测，LLM 没看到真实数据就构造参数
- 仅支持 8 种 viz_type，Superset 共有 46+ 种
- 无数据预分析步骤，图表参数经常错误

### 目标
1. 建立图表类型描述文件（Chart Type Registry），作为 LLM 选型的单一事实来源
2. 新增 `AnalyzeDataTool`，先查询数据、分析数据形状，再选型创建
3. 扩展支持到 25 种常用图表类型
4. 建立"先看数据再选类型"的四步工作流

---

## 2. 架构设计

### 2.1 新增文件结构

```
superset/ai/
  chart_types/
    __init__.py          # 包入口，导出 get_chart_registry()
    schema.py            # ChartTypeDescriptor + ParamDescriptor 数据类
    catalog.py           # 25 种图表类型的完整描述
    registry.py          # ChartTypeRegistry 类
  tools/
    analyze_data.py      # AnalyzeDataTool — SQL 执行 + 数据形状分析
```

### 2.2 修改文件

| 文件 | 改动 |
|---|---|
| `superset/ai/tools/create_chart.py` | 扩展 SUPPORTED_VIZ_TYPES；添加 registry 参数校验 |
| `superset/ai/prompts/chart_creation.py` | 重写为 6 步工作流 + 动态图表类型表 |
| `superset/ai/agent/chart_agent.py` | 添加 AnalyzeDataTool；注入 registry 到 prompt |
| `superset/ai/agent/events.py` | 添加 `"data_analyzed"` 事件 |

---

## 3. Chart Type Registry 设计

### 3.1 数据模型 (`schema.py`)

```python
@dataclass
class ParamDescriptor:
    name: str                           # 参数名，如 "x_axis", "metrics"
    type: str                           # "string" | "string_array" | "metric" | "metric_array" | ...
    required: bool
    description: str                    # 参数说明
    default: Any = None
    conflicts_with: list[str] = field(default_factory=list)

@dataclass
class ChartTypeDescriptor:
    viz_type: str                       # 类型标识，如 "pie"
    display_name: str                   # 显示名，如 "饼图/环形图"
    category: str                       # 分类：timeseries / categorical / kpi / distribution / relationship
    description: str                    # 一句话描述
    best_for: list[str]                 # 适用场景
    not_for: list[str]                  # 不适用场景
    params: list[ParamDescriptor]       # 参数列表
    example_form_data: dict             # form_data 示例
    uses_metric_singular: bool          # True = 用 metric（饼图、大数字）
    requires_time_column: bool          # True = 需要时间列（时序图）
    max_groupby_dimensions: int         # 建议 groupby 最大维度数
```

### 3.2 图表类型目录 (`catalog.py`)

**25 种类型分 6 大类：**

| 分类 | 类型 | viz_type | 核心参数 |
|---|---|---|---|
| **时序(6)** | 折线图 | `echarts_timeseries_line` | granularity_sqla, metrics, groupby |
| | 柱状图 | `echarts_timeseries_bar` | x_axis, metrics, groupby |
| | 平滑线图 | `echarts_timeseries_smooth` | granularity_sqla, metrics, groupby |
| | 面积图 | `echarts_area` | granularity_sqla, metrics, groupby |
| | 阶梯图 | `echarts_timeseries_step` | granularity_sqla, metrics, groupby |
| | 时序散点图 | `echarts_timeseries_scatter` | x_axis, metrics, groupby |
| **分类(5)** | 饼图 | `pie` | metric(单数), groupby |
| | 漏斗图 | `funnel` | groupby, metric(单数) |
| | 雷达图 | `radar` | metrics, groupby |
| | 矩形树图 | `treemap_v2` | metrics, groupby |
| | 旭日图 | `sunburst_v2` | metrics, groupby/columns |
| **KPI(3)** | 大数字 | `big_number_total` | metric(单数) |
| | 大数字+趋势 | `big_number` | metric(单数), granularity_sqla |
| | 仪表盘 | `gauge_chart` | metric(单数) |
| **表格(2)** | 数据表 | `table` | metrics, groupby |
| | 透视表 | `pivot_table_v2` | metrics, groupby, columns |
| **分布(3)** | 直方图 | `histogram_v2` | column, groupby |
| | 箱线图 | `box_plot` | metrics, groupby |
| | 瀑布图 | `waterfall` | x_axis, metrics |
| **关系(3)** | 桑基图 | `sankey_v2` | source, target, metric |
| | 热力图 | `heatmap_v2` | x_axis, y_axis, metric |
| | 网络图 | `graph_chart` | source, target, metric |
| **多维(3)** | 气泡图 | `bubble_v2` | series, entity, x, y, size |
| | 混合图 | `mixed_timeseries` | metricsA/B, groupbyA/B |
| | 散点图 | `echarts_timeseries_scatter` | x_axis, metrics, groupby |

**每种类型的 catalog 条目示例（饼图）：**

```python
"pie": ChartTypeDescriptor(
    viz_type="pie",
    display_name="饼图/环形图",
    category="categorical",
    description="展示各部分占整体的比例关系",
    best_for=["占比分析", "分类对比", "Top-N 分布"],
    not_for=["时间趋势", "超过 10 个类别", "精确数值比较"],
    params=[
        ParamDescriptor(name="metric", type="metric", required=True,
                        description="定义扇形大小的单个指标"),
        ParamDescriptor(name="groupby", type="string_array", required=True,
                        description="定义扇形标签的列"),
        ParamDescriptor(name="row_limit", type="integer", required=False,
                        description="最大行数", default=100),
    ],
    example_form_data={"metric": "SUM(revenue)", "groupby": ["region"], "row_limit": 100},
    uses_metric_singular=True,
    requires_time_column=False,
    max_groupby_dimensions=1,
),
```

### 3.3 Registry 类 (`registry.py`)

```python
class ChartTypeRegistry:
    def get(self, viz_type: str) -> ChartTypeDescriptor | None
    def get_supported_types(self) -> set[str]
    def format_for_prompt(self) -> str           # 生成 LLM 可读的摘要表格
    def format_type_detail(self, viz_type: str) -> str  # 单个类型的详细参数说明
    def validate_form_data(self, viz_type: str, form_data: dict) -> list[str]
```

---

## 4. AnalyzeDataTool 设计

### 4.1 功能

执行 SQL 查询并分析返回数据的形状，输出结构化分析结果供 LLM 决策。

### 4.2 输出格式

```json
{
  "columns": [
    {"name": "gender", "type": "string", "distinct_count": 2,
     "sample_values": ["boy", "girl"]},
    {"name": "num", "type": "numeric", "min": 100, "max": 50000}
  ],
  "row_count": 2,
  "chart_recommendations": [
    {"viz_type": "pie", "confidence": "high",
     "reason": "2 个分类 + 1 个数值指标 → 适合饼图展示占比"},
    {"viz_type": "echarts_timeseries_bar", "confidence": "high",
     "reason": "分类维度 + 数值指标 → 适合柱状图"}
  ]
}
```

### 4.3 启发式推荐规则

```python
def _recommend_charts(columns, row_count):
    has_date = any(c["type"] == "date" for c in columns)
    string_cols = [c for c in columns if c["type"] == "string"]
    numeric_cols = [c for c in columns if c["type"] == "numeric"]

    # 规则 1: 少量分类 + 1 指标 → pie, bar
    if len(string_cols) == 1 and len(numeric_cols) >= 1:
        distinct = string_cols[0]["distinct_count"]
        if distinct <= 8:
            recommend("pie", high)     # 占比场景
            recommend("bar", high)     # 对比场景

    # 规则 2: 日期列 + 数值 → 时序图
    if has_date and numeric_cols:
        recommend("line", high)
        recommend("area", medium)
        recommend("bar", medium)

    # 规则 3: 无 groupby + 单数值 → big_number_total
    if not string_cols and len(numeric_cols) == 1 and row_count == 1:
        recommend("big_number_total", high)

    # 规则 4: 2+ 数值 + 1 分类 → scatter, radar
    if len(numeric_cols) >= 2 and string_cols:
        recommend("radar", medium)
        recommend("scatter", medium)

    # 规则 5: 通用兜底 → table
    recommend("table", low)
```

---

## 5. 四步工作流设计

### 5.1 新 System Prompt 结构

```
[角色定义 + 规则]                    ← 不变

[图表类型参考表]                     ← 动态生成，从 registry.format_for_prompt()
viz_type | 名称 | 分类 | 适用场景 | 核心参数

[工作流 - 必须按顺序执行]
Step 1: 理解需求（用户指定了类型？数据要表达什么？）
Step 2: 查找数据（search_datasets → get_schema）
Step 3: 分析数据（调用 analyze_data，获取推荐）
Step 4: 选型创建（匹配推荐 → 构造 form_data → create_chart）

[各类型详细参数说明]                  ← 动态生成，从 registry.format_type_detail()
```

### 5.2 工作流对比

**旧流程（LLM 猜）：**
```
用户消息 → search_datasets → LLM 猜类型和参数 → create_chart
```

**新流程（先看数据再选）：**
```
用户消息 → search_datasets → analyze_data(执行SQL+分析)
                              ↓
                     数据形状分析 + 类型推荐
                              ↓
                     LLM 基于分析结果选型
                              ↓
                     构造 form_data → create_chart
```

---

## 6. 实施顺序

| 步骤 | 内容 | 文件 |
|---|---|---|
| **1** | 创建 chart_types 包 | `schema.py` + `registry.py` + `catalog.py`（先填 8 种） |
| **2** | 扩展 catalog 到 25 种 | `catalog.py` |
| **3** | 创建 AnalyzeDataTool | `analyze_data.py` |
| **4** | 更新 CreateChartTool | `create_chart.py`（扩展白名单 + registry 校验） |
| **5** | 重写 Prompt + 更新 Agent | `chart_creation.py` + `chart_agent.py` |
| **6** | 集成测试 | MCP 浏览器验证 |
