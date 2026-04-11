# Phase 4 设计文档：一句话建仪表板（Dashboard Agent）

## 1. 背景与目标

### 当前状态
- Phase 1-3 已实现 NL2SQL、Chart 创建、Auto-Debug
- 用户需要手动逐个创建图表，再手动组装 Dashboard

### 目标
1. 新增 DashboardAgent：用户描述需求，AI 自动创建多个图表并组装成 Dashboard
2. 前端新增 Dashboard 模式
3. 支持自动布局和图表关联

---

## 2. 架构设计

### 2.1 新增文件

```
superset/ai/
  agent/
    dashboard_agent.py         # DashboardAgent — 5 工具链
  prompts/
    dashboard_creation.py      # 仪表板创建 prompt
  tools/
    create_dashboard.py        # CreateDashboardTool
```

### 2.2 修改文件

| 文件 | 改动 |
|---|---|
| `superset/ai/schemas.py` | 新增 `agent_type: "dashboard"` |
| `superset/ai/commands/chat.py` | 注册 DashboardAgent |
| `superset/ai/api.py` | AI_AGENT_DASHBOARD flag 检查 |
| `superset/ai/agent/events.py` | `dashboard_created` 事件 |
| 前端 `AiChatPanel.tsx` | Dashboard 模式 + View Dashboard 链接 |
| 前端 `AiChatDrawer.tsx` | onDashboardCreated 回调 |
| 前端 `SqlEditor/index.tsx` | onDashboardCreated 打开新标签页 |
| 前端 `types.ts` | dashboard_created 事件类型 |
| 前端 `featureFlags.ts` | AI_AGENT_DASHBOARD 枚举 |
| `superset_config_docker.py` | AI_AGENT_DASHBOARD feature flag |

---

## 3. DashboardAgent 设计

### 3.1 工具链

```python
class DashboardAgent(BaseAgent):
    tools = [
        GetSchemaTool(database_id),      # 获取表结构
        ExecuteSqlTool(database_id),     # 执行 SQL 预览数据
        SearchDatasetsTool(database_id), # 查找数据集
        CreateChartTool(),               # 创建图表（复用 Phase 2）
        CreateDashboardTool(),           # 创建仪表板
    ]
```

### 3.2 工作流

```
用户："创建一个出生数据分析仪表板"
    ↓
search_datasets → 找到 birth_names 数据集
    ↓
LLM 规划 3-5 个图表（趋势图、饼图、统计卡片等）
    ↓ 逐个创建
create_chart × N → 收集 chart_ids
    ↓
create_dashboard(chart_ids, title) → 组装 Dashboard
    ↓
前端展示 "View Dashboard" 链接
```

---

## 4. CreateDashboardTool 设计

### 4.1 核心逻辑

```python
class CreateDashboardTool(BaseTool):
    def run(self, title, chart_ids, ...):
        # 1. 调用 CreateDashboardCommand 创建 dashboard
        dashboard = CreateDashboardCommand(...)()

        # 2. 根据 chart_ids 生成网格布局
        position = get_default_position(dashboard, chart_ids)

        # 3. 关联图表到 Dashboard
        DashboardDAO.set_dash_metadata(dashboard, {"positions": position})
```

### 4.2 布局生成

`get_default_position()` 自动计算网格位置：
- 每行最多 3 个图表
- 每个图表占 4 列 × 20 行（标准尺寸）
- 自动换行排列

### 4.3 关键修复：Dashboard 图表关联

**问题**：`CreateDashboardCommand.run()` 使用 `@transaction()` 提交后，直接设置 `dashboard.slices = slices` 不会持久化图表关联。

**修复**：使用 `DashboardDAO.set_dash_metadata(dashboard, {"positions": position})` 从 position_json 提取 chartIds 并正确建立 `dashboard_slices` 多对多关联。

---

## 5. 前端改动

### 模式切换扩展

AI 对话框顶部：SQL / Chart / **Dashboard**

- Dashboard 模式 placeholder："Describe the dashboard you want to create..."

### Dashboard 链接解析

解析 Agent 输出中的 `/superset/dashboard/` URL，生成 "View Dashboard →" 链接，点击在新标签页打开。

---

## 6. 安全措施

- Per-chart 数据源权限检查（`security_manager.can_access_datasource`）
- Dashboard 写权限检查
- 使用 `DashboardDAO.set_dash_metadata()` 替代手动设置关联

---

## 7. 测试验证

通过 SQL Lab AI Assistant 测试：
1. Dashboard 模式输入 "创建出生数据分析仪表板"
2. Agent 自动创建 3-5 个图表 + 组装 Dashboard
3. 点击 "View Dashboard" 链接查看结果
