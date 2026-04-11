# Phase 6：Dashboard Agent 智能化改造

## Context

Phase 5 已将 `ChartAgent` 升级为"先分析数据再选类型"的智能工作流（24 种图表类型 + AnalyzeDataTool + 动态 registry prompt），但 `DashboardAgent` 仍是 Phase 4 的旧版本：

| 能力 | ChartAgent (Phase 5) | DashboardAgent (当前) |
|---|---|---|
| 图表类型 | 24 种（registry 动态注入） | 7 种（prompt 硬编码） |
| AnalyzeDataTool | 有 | 无 |
| 参数 schema / example | 有（registry 提供） | 无 |
| 指标格式指导 | 有 | 无 |
| analyze_data 步骤 | 强制执行 | 无 |

**目标**：将 Phase 5 的智能选型能力集成到 Dashboard Agent，使其在创建 dashboard 内的每个 chart 时也能"先看数据再选类型"。

## 改动概览

只需改 2 个文件，新增 0 个文件：

### 1. `superset/ai/agent/dashboard_agent.py`（小改）

- 添加 `AnalyzeDataTool` 到 tools 列表
- 导入 `get_chart_registry`
- `get_system_prompt()` 改为动态注入 registry 内容（同 ChartAgent 模式）

```python
# 改动点
from superset.ai.tools.analyze_data import AnalyzeDataTool
from superset.ai.chart_types.registry import get_chart_registry

tools: list[BaseTool] = [
    GetSchemaTool(...),
    ExecuteSqlTool(...),
    AnalyzeDataTool(database_id=database_id),   # 新增
    SearchDatasetsTool(...),
    CreateChartTool(),
    CreateDashboardTool(),
]

def get_system_prompt(self) -> str:
    registry = get_chart_registry()
    chart_table = registry.format_for_prompt()
    chart_details = registry.format_all_details()
    prompt = DASHBOARD_CREATION_SYSTEM_PROMPT.format(
        chart_type_table=chart_table,
        chart_type_details=chart_details,
    )
    ...
```

### 2. `superset/ai/prompts/dashboard_creation.py`（重写）

重写 system prompt，核心变化：

**a. 添加 `{chart_type_table}` 和 `{chart_type_details}` 占位符** — 与 ChartAgent prompt 一致，动态注入 24 种图表类型的完整描述和参数 schema。

**b. 添加 6 步工作流中的 analyze_data 步骤** — 在"创建图表"之前增加"分析数据"步骤：

```
Step 1: 理解需求 — 分析用户想要的 dashboard 包含哪些分析维度
Step 2: 探索数据 — get_schema + search_datasets
Step 3: 分析数据 — 对每个图表调用 analyze_data，获取推荐类型
Step 4: 规划图表 — 根据分析结果决定 2-5 个图表的类型
Step 5: 逐个创建图表 — create_chart × N
Step 6: 创建 Dashboard — create_dashboard(chart_ids=[...])
```

**c. 添加指标格式指导** — 从 chart_creation.py 复用 metric format 说明。

**d. 保留 Dashboard 设计指南** — 趋势/对比/占比/KPI/明细 的布局建议不变，但改为引用 registry 的分类体系。

**e. 图表类型列表** — 删除硬编码的 7 种类型列表，改为 `{chart_type_table}` 动态生成。

## 不改动的文件

| 文件 | 原因 |
|---|---|
| `create_chart.py` | 已在 Phase 5 支持 24 种类型 + registry 校验，无需改动 |
| `create_dashboard.py` | 布局和关联逻辑正确，无需改动 |
| `analyze_data.py` | 已存在，直接复用 |
| `chart_types/` | 已存在，直接复用 |
| `events.py` | `data_analyzed` 已在 Phase 5 添加 |

## 实施步骤

| 步骤 | 内容 | 文件 |
|---|---|---|
| 1 | 重写 dashboard_creation.py prompt | `superset/ai/prompts/dashboard_creation.py` |
| 2 | 更新 dashboard_agent.py（添加 AnalyzeDataTool + 动态 prompt） | `superset/ai/agent/dashboard_agent.py` |
| 3 | 部署到 Docker（复制到 superset + superset-worker 容器 + 重启） | — |
| 4 | 浏览器测试 | — |

## 验证方案

通过 AI Assistant → Dashboard 模式测试：

1. **基础测试**：`"创建一个出生数据分析仪表板"` → 应自动创建含多种类型图表的 dashboard
2. **指定类型测试**：`"创建仪表板包含饼图和折线图"` → 应正确使用指定类型
3. **新类型测试**：`"创建仪表板，包含漏斗图展示性别分布和面积图展示趋势"` → 应使用 Phase 5 新增的类型
4. 验证 dashboard 能正确渲染所有图表
