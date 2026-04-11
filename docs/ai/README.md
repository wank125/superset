# AI Agent 集成设计文档

Superset AI Agent 分 6 个阶段迭代构建，每个阶段在前一阶段基础上扩展。

## 阶段总览

| 阶段 | 名称 | 核心能力 | 设计文档 |
|---|---|---|---|
| 总览 | 整体架构 | 4 阶段迭代方案 + 代码结构 | [00_overall_design.md](00_overall_design.md) |
| Phase 1 | NL2SQL | 自然语言转 SQL，AI 基础框架 | [phase1_nl2sql.md](phase1_nl2sql.md) |
| Phase 2 | Chart Agent | 一句话建图表，前端模式切换 | [phase2_chart_agent.md](phase2_chart_agent.md) |
| Phase 3 | Auto-Debug | SQL 错误自动诊断修复 | [phase3_auto_debug.md](phase3_auto_debug.md) |
| Phase 4 | Dashboard Agent | 一句话建仪表板，多图表自动组装 | [phase4_dashboard_agent.md](phase4_dashboard_agent.md) |
| Phase 5 | Smart Chart | 24 种图表类型 + 数据分析驱动选型 | [phase5_smart_chart.md](phase5_smart_chart.md) |
| Phase 6 | Dashboard 智能化 | Dashboard Agent 集成 Phase 5 智能选型 | [phase6_dashboard_upgrade.md](phase6_dashboard_upgrade.md) |

## 依赖关系

```
Phase 1 (NL2SQL)
  └→ Phase 2 (Chart Agent)
       └→ Phase 3 (Auto-Debug)
            └→ Phase 4 (Dashboard Agent)
                 ├→ Phase 5 (Smart Chart)  ← ChartAgent 升级
                 └→ Phase 6 (Dashboard 智能化) ← DashboardAgent 升级
```

## 代码结构

```
superset/ai/
  agent/             # Agent 实现
    base.py          # BaseAgent — ReAct 循环
    sql_agent.py     # Phase 1: NL2SQL
    chart_agent.py   # Phase 2+5: Chart 创建（Phase 5 增强为智能选型）
    debug_agent.py   # Phase 3: SQL 排错
    dashboard_agent.py # Phase 4+6: Dashboard 创建（Phase 6 集成智能选型）
    context.py       # 对话上下文
    events.py        # SSE 事件类型
  chart_types/       # Phase 5 新增
    schema.py        # ChartTypeDescriptor 数据类
    catalog.py       # 24 种图表类型描述
    registry.py      # ChartTypeRegistry
  tools/             # 工具实现
    execute_sql.py   # SQL 执行
    get_schema.py    # 表结构查询
    search_datasets.py # 数据集搜索
    analyze_data.py  # Phase 5: 数据分析 + 图表推荐
    create_chart.py  # 图表创建
    create_dashboard.py # 仪表板创建
  prompts/           # LLM Prompt 模板
    nl2sql.py        # Phase 1
    chart_creation.py  # Phase 2+5
    debug.py         # Phase 3
    dashboard_creation.py # Phase 4+6
  llm/               # LLM Provider
    openai_provider.py  # OpenAI 兼容（支持 LM Studio）
  streaming/         # 流式输出
    redis_stream.py  # Redis Stream + SSE
  api.py             # REST API 端点
  schemas.py         # 请求/响应 Schema
  commands/chat.py   # Agent 路由
  tasks.py           # Celery 异步任务
  config.py          # 配置读取
```

## 配置

详见 `docker/pythonpath_dev/superset_config_docker.py`

```python
FEATURE_FLAGS = {
    "AI_AGENT": True,
    "AI_AGENT_NL2SQL": True,
    "AI_AGENT_CHART": True,
    "AI_AGENT_DEBUG": True,
    "AI_AGENT_DASHBOARD": True,
}
```
