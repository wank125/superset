# Superset AI Agent 开发总结

> 生成日期：2026-04-14（最后更新：2026-04-14）
> 分支：`feature/supersonic`
> 提交数：40+ commits (AI 相关)
> 文件数：70+ Python + 9 TypeScript
> 完成度：19 个 Phase 全部完成（仅剩 P2 白名单参数逃生舱可选迭代）

---

## 一、项目概览

在 Apache Superset 平台上构建了一套完整的 AI Agent 系统，用户通过自然语言即可完成 SQL 查询、图表创建、仪表板生成和自动排错。系统支持 5 种 Agent 模式（NL2SQL、Chart、Dashboard、Debug、Copilot），后端采用 LangGraph StateGraph 管线架构，前端实现了节点级实时事件流渲染。系统具备意图路由、多轮对话、多数据集仪表板、置信度评分与计划确认等高级能力。

### 架构总览

```
用户输入（自然语言）
    ↓
前端 AI Chat Drawer（SQL Lab 集成）
    ↓ POST /api/v1/ai/chat/
Celery Worker
    ↓ Redis Streams 事件推送
前端轮询渲染（11 种事件类型）
```

### 系统架构图

```
┌──────────────────────────────────────────────────────────────┐
│                    前端 (React + TypeScript)                   │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ SQL Lab  │→ │ AiChat   │  │ AiStep   │  │ AiSqlPreview │ │
│  │ Editor  │  │ Drawer   │  │ Progress │  │ Chart Links  │ │
│  └─────────┘  └────┬─────┘  └──────────┘  └──────────────┘ │
│                    │ POST /chat   GET /events (500ms 轮询)     │
└────────────────────┼─────────────────────────────────────────┘
                     ↓
┌────────────────────┼─────────────────────────────────────────┐
│               Flask REST API                                  │
│           /api/v1/ai/chat/   /api/v1/ai/events/              │
└────────────────────┼─────────────────────────────────────────┘
                     ↓ dispatch Celery task
┌────────────────────┼─────────────────────────────────────────┐
│              Celery Worker                                    │
│                    │                                          │
│  ┌─────────────────┴──────────────────────────────────────┐  │
│  │            LangGraph StateGraph 管线                    │  │
│  │                                                         │  │
│  │  父图: parse_request → search_dataset → select_dataset  │  │
│  │        → read_schema → plan_dashboard                   │  │
│  │        → single_chart_subgraph × N → create_dashboard  │  │
│  │                                                         │  │
│  │  子图: plan_query → validate_sql → execute_query        │  │
│  │        → analyze_result → select_chart                  │  │
│  │        → normalize → repair → create_chart              │  │
│  └─────────────────────────────────────────────────────────┘  │
│                    │ AgentEvent 流                             │
│           Redis Streams (xadd/xrange)                        │
└──────────────────────────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│              Superset 后端 (Flask + SQLAlchemy)               │
│  Tools: SearchDatasets / GetSchema / ExecuteSql              │
│         AnalyzeData / CreateChart / CreateDashboard          │
│  LLM: GLM-4.7-Flash (LM Studio 本地部署)                    │
│  DB: PostgreSQL + Redis                                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、分阶段开发历程

### Phase 1: NL2SQL Agent（自然语言转 SQL）

**目标**：用户输入自然语言，AI 自动生成 SQL 查询

- 实现完整 AI 框架基础设施：LLM Provider 注册表、Agent 基类、事件系统、流式传输
- 支持 OpenAI / Anthropic / Ollama 三种 LLM Provider
- `NL2SQLAgent`：ReAct 循环 + `GetSchemaTool` + `ExecuteSqlTool`
- 前端 AI Chat Drawer 集成到 SQL Lab 编辑器

**关键文件**：
- `superset/ai/agent/base.py` — ReAct Agent 基类
- `superset/ai/agent/nl2sql_agent.py` — NL2SQL Agent
- `superset/ai/llm/` — LLM Provider 注册表
- `superset/ai/streaming/manager.py` — Redis Streams 事件管理

### Phase 2: Chart Agent（一句话建图表）

**目标**：用户输入自然语言，AI 自动创建图表

- `ChartAgent`：新增 `SearchDatasetsTool`、`CreateChartTool`
- 前端模式切换：SQL / Chart / Dashboard 三种 Agent 类型
- 图表创建完成后显示 "View Chart" 链接

### Phase 3: Auto-Debug Agent（自动排错）

**目标**：SQL 执行失败时，AI 自动诊断并修复

- `DebugAgent`：分析错误原因、建议修复方案
- 前端 AI Fix 按钮
- 错误诊断 + SQL 修正闭环

### Phase 4: Dashboard Agent（一句话建仪表板）

**目标**：用户输入自然语言，AI 自动创建包含多张图表的仪表板

- `DashboardAgent`：新增 `CreateDashboardTool`
- 规划 → 创建多张图表 → 组装仪表板
- 修复 `DashboardDAO.set_dash_metadata` 兼容性问题

### Phase 5: 智能图表类型选择

**目标**：从 7 种硬编码图表扩展到 24 种，基于数据分析结果智能选型

- 图表类型注册表 (`chart_types/registry.py`)：24 种图表类型，含参数 schema 和示例
- `AnalyzeDataTool`：执行 SQL 后分析数据形态（行数、列类型、基数等）
- 数据驱动的图表选择：先执行 SQL 看数据，再决定用什么图表类型
- `suitability_flags`：纯代码推导（good_for_trend / composition / kpi / distribution / comparison）

### Phase 6: Dashboard Agent 智能化改造

**目标**：将 Phase 5 的智能选型能力集成到 Dashboard Agent

- Dashboard Agent 添加 `AnalyzeDataTool` + 动态 registry prompt 注入
- 重写 dashboard_creation.py prompt

### Phase 7: LangChain + LangGraph 双路径集成

**目标**：引入 LangChain/LangGraph 框架，提升稳定性

- LangChain ReAct Agent 路径（feature flag: `AI_AGENT_USE_LANGCHAIN`）
- GLM 稳定性修复：动态 prompt、参数归一化、顺序管线、幂等保护
- `ToolOrderGuard`：强制工具调用顺序
- 23 个单元测试通过

### Phase 8: LangGraph StateGraph 管线

**目标**：用 LangGraph StateGraph 替代 ReAct 循环，实现确定性节点管线

- **父图**（P1-P6）：`parse_request → search_dataset → select_dataset → read_schema → plan_dashboard → create_dashboard`
- **子图**（C1-C8）：`plan_query → validate_sql → execute_query → analyze_result → select_chart → normalize → repair → create_chart`
- State 映射：`_make_subgraph_wrapper()` 实现 `DashboardState` ↔ `SingleChartState` 转换
- 自动评分数据集选择（替代 interrupt，无需 checkpointer）
- 6 条规则参数归一器（normalizer.py）
- RetryPolicy：`execute_query` 和 `create_chart` 瞬态错误自动重试
- 幂等保护：图表 10 分钟复用、仪表板 30 分钟复用
- E2E 测试：Chart 模式 7 events、Dashboard 模式 9 events 均通过

**关键文件**：
```
superset/ai/graph/
├── __init__.py
├── state.py          # DashboardState + SingleChartState TypedDict
├── nodes_parent.py   # 父图 6 个节点
├── nodes_child.py    # 子图 8 个节点
├── normalizer.py     # compile_superset_form_data（6 条规则）
├── llm_helpers.py    # llm_call_json / llm_call_json_list
├── builder.py        # build_chart_graph / build_dashboard_graph
└── runner.py         # run_graph + _emit_node_events
```

### Phase 9: 前端事件渲染增强

**目标**：让前端正确渲染后端 11 种事件，用户实时看到 Agent 进度

- `useAiChat` hook 重写：处理全部 11 种事件类型
- `AiStepProgress` 组件：实时步骤进度（✓/●/✗ 状态 + 脉冲动画）
- 结构化事件替代正则提取：`chartResults` / `dashboardResult` 直接渲染
- 超时 60s → 180s
- 删除死代码 `useAiStream.ts`

### Phase 10: E2E 测试

**目标**：建立端到端自动化测试，验证 AI Agent 完整链路

- `tests/ai/test_e2e_agent.py`：HTTP 测试客户端，10 个测试用例
- 覆盖场景：NL2SQL（简单/聚合/歧义）、Chart（柱状/趋势/饼图）、Dashboard（多图表）、Debug（SQL 修复）、边界（无效类型/空消息）
- 事件轮询 + 终端事件检测 + 控制台报告

### Phase 11: 多轮对话

**目标**：支持跨轮次对话上下文，让 Agent 理解追问和修正

- `session_id` 绑定：`ConversationContext` 按 `user_id + session_id` 隔离，Redis 缓存最近 20 轮
- SQL 历史摘要：`add_tool_summary()` 持久化 SQL 执行和图表创建摘要到 Redis
- LLM insight 生成：`analyze_result` 节点调用 LLM 生成一行数据洞察
- `parse_request` 注入 `conversation_history` 上下文
- 新增 `insight_generated` 事件类型

**关键文件**：
- `superset/ai/agent/context.py` — `ConversationContext` 重写（Redis + trimming）
- `superset/ai/graph/nodes_child.py` — insight 生成
- `superset/ai/tasks.py` — tool_summary 持久化

### Phase 12: 数据集模糊搜索

**目标**：用户不精确指定表名时也能找到正确数据集

- `SearchDatasetsTool` 4 级模糊搜索：精确匹配 → 描述匹配 → 子串匹配 → difflib 相似度（>= 0.4）
- 列描述注入：`column_descriptions` 和 `column_verbose_names` 注入到 `plan_query` prompt
- 候选评分：`select_dataset` 节点综合 fuzzy match_score + description bonus
- `SchemaSummary` 新增 `column_descriptions`、`column_verbose_names` 字段

**关键文件**：
- `superset/ai/tools/search_datasets.py` — `_fuzzy_search` 4 级搜索
- `superset/ai/graph/nodes_parent.py` — `select_dataset` 评分选择
- `superset/ai/graph/nodes_child.py` — prompt 注入列描述

### Phase 13: 业务指标目录

**目标**：预定义业务指标（GMV、DAU 等），让 LLM 生成更准确的 SQL

- `metric_catalog.py`：YAML 加载 + `lru_cache`，支持通配符表名匹配
- `metric_catalog.yaml`：示例指标（gmv、conversion_rate、dau、arpu、new_user_ratio、total_births）
- 双路径注入：`plan_query`（SQL 生成）+ `plan_dashboard`（仪表板规划）
- `SchemaSummary` 新增 `business_metrics` 字段

**关键文件**：
- `superset/ai/metric_catalog.py` — 指标加载引擎
- `superset/ai/metric_catalog.yaml` — 指标定义
- `superset/ai/graph/nodes_child.py` — business_metrics_block 注入
- `superset/ai/graph/nodes_parent.py` — dashboard planning 指标提示

### Phase 14: 图表修改

**目标**：支持用户对已创建图表进行修改（改颜色、换指标、调维度等）

**实现内容**：
- 4 个新节点：`classify_intent`（意图分类：新建/修改/追问）、`load_existing_chart`（加载已有图表）、`apply_chart_modification`（LLM 生成修改指令）、`update_chart`（就地更新图表）
- State 新增字段：`previous_charts`、`existing_chart`、`modification`
- 意图分类支持 code 快速路径 + LLM 兜底分类
- 就地更新模式：保留 chart_id，仅更新 params，不创建新图表
- Builder 注册修改路径：`classify_intent` → 分支路由 → 新建/修改子流程

**关键文件**：
- `superset/ai/graph/nodes_parent.py` — `classify_intent`、`load_existing_chart` 节点
- `superset/ai/graph/nodes_child.py` — `apply_chart_modification`、`update_chart` 节点
- `superset/ai/graph/builder.py` — 修改路径注册

### Phase 15: Copilot 大管家

**目标**：通用 AI 助手，可查询 Superset 资产信息

- `CopilotAgent`：10 个资产查询工具
  - 数据库：`ListDatabasesTool`、`GetDatasetDetailTool`
  - 图表：`ListChartsTool`、`GetChartDetailTool`
  - 仪表板：`ListDashboardsTool`、`GetDashboardDetailTool`
  - 用户：`WhoAmITool`
  - 查询：`QueryHistoryTool`、`SavedQueryTool`
  - 报表：`ReportStatusTool`
- `database_id` 可选：不指定时仅暴露查询类工具
- Agent 注册：`commands/chat.py` `_AGENT_MAP["copilot"]`
- API 门控：`AI_AGENT_COPILOT` feature flag

**关键文件**：
- `superset/ai/agent/copilot_agent.py` — CopilotAgent 定义
- `superset/ai/tools/list_databases.py` 等 10 个工具文件
- `superset/ai/prompts/copilot.py` — 系统提示词

### Phase 16: 意图路由

**目标**：自动识别用户意图并路由到正确的 Agent 类型

- `IntentRouter`：3 步分类流程
  1. 上下文延续检测：匹配 `is_continuation()` 判断追问
  2. 关键词快速路径：`keyword_route()` 匹配路由规则表
  3. LLM 分类：`llm_classify()` 兜底语义分类
- 4 类 Agent：`nl2sql`、`chart`、`dashboard`、`copilot`
- `tasks.py` 集成：`agent_type == "auto"` 时触发路由
- `intent_routed` 事件：前端可展示路由决策

**关键文件**：
- `superset/ai/router/` — 路由包（router.py, rules.py, llm_classifier.py, types.py）
- `superset/ai/tasks.py` — 路由集成
- `superset/ai/agent/context.py` — `add_router_meta()` 路由元数据持久化

### Phase 17: 澄清追问

**目标**：数据集不唯一时，主动向用户确认选择

- `clarify_user` 节点：发布结构化 `clarify` 事件（含选项列表）+ 文本回退
- `select_dataset` 集成：多候选时路由到 `clarify_user`，返回选项供用户选择
- State 字段：`clarify_question`、`clarify_type`、`clarify_options`、`answer_prefix`
- 前端零改动：追问模式通过 text_chunk 文本回退兼容现有 UI

**关键文件**：
- `superset/ai/graph/nodes_parent.py` — `clarify_user` 节点
- `superset/ai/graph/builder.py` — 注册 clarify_user 节点

### Phase 18: 多数据集仪表板

**目标**：支持跨多个 dataset 创建仪表板（如 Slack Dashboard 跨 7 个表）

- 双路径模式（向后兼容）：
  - 单表模式：保持现有 `search → select → read_schema → plan` 流程
  - 多表模式：`parse_request → plan_dashboard（前置）→ subgraph（内含 resolve_dataset）×N`
- 多表检测：括号正则提取 + 实际数据集验证（不依赖 LLM 输出）
- `PLAN_DASHBOARD_PROMPT_V2`：无 schema，基于可用表列表规划
- `_backfill_target_tables`：关键词匹配 + round-robin 回退分配
- `resolve_dataset`：带 schema_cache 的按需数据集解析
- `create_dashboard`：多表模式标题从 `target_tables[0]` 派生
- 22 个新增单元测试（全部通过）

**关键文件**：
- `superset/ai/graph/state.py` — `ChartIntent.target_table` + `DashboardState.schema_cache`
- `superset/ai/graph/nodes_parent.py` — 多表检测、V2 prompt、`_backfill_target_tables`、`resolve_dataset`、`_build_schema_summary`
- `superset/ai/graph/builder.py` — subgraph wrapper 内嵌 resolve_dataset

### Phase 19a: Plan 分析确认（置信度评分 + 双模式执行）

**目标**：执行前暴露系统关键假设，用户可纠偏语义理解偏差

**实现内容**：
- `review_analysis` 节点：插入在 `plan_dashboard` 之后、`single_chart_subgraph` 之前
- 6 项风险信号自动评分（纯代码，零 LLM 开销）：
  - 数据集选择不确定（+30）、多主题混合（+20）、Dashboard 3+ 图表（+20）
  - 涉及派生/比例指标（+15）、无明确时间列（+10）、多数据集模式（+10）
- 双模式运行：
  - **direct mode**（置信度 ≥ 0.7）：自动执行，零阻塞
  - **plan mode**（置信度 < 0.7）：输出结构化计划摘要，等待用户确认
- `analysis_plan` 事件类型：结构化计划数据 + text_chunk 文本回退
- 确认后第二轮：`execution_mode="direct"` 跳过 `review_analysis`
- 单元测试：`TestComputeConfidence`、`TestReviewAnalysis`、`TestBuildAnalysisPlan`

**关键文件**：
- `superset/ai/graph/nodes_parent.py` — `review_analysis`、`_compute_confidence`、`_build_analysis_plan`、`_publish_plan_event`
- `superset/ai/graph/state.py` — `execution_mode`、`analysis_plan` 字段
- `superset/ai/graph/builder.py` — 注册 `review_analysis` 节点

### Phase 19b: 动态布局引擎

**目标**：根据图表类型智能分配仪表板布局宽度，解决固定 width=4 的刻板问题

**实现内容**：
- `ChartTypeDescriptor` 新增 `default_width` 字段（1-12），24 种图表类型已补全宽度：
  - KPI 类（big_number/gauge）：width=3，小巧精干
  - 时序类（line/bar/area/step/smooth/scatter）：width=6，需要横向空间
  - 表格类（table/pivot_table）：width=12，占满整行
  - 其他类（pie/funnel/radar 等 13 种）：width=4，默认中等
- `append_charts_v2`：流式装箱算法，支持可变 width + 尾部补全
  - 按 width 累加，超过 12 列则换行
  - 行末剩余宽度自动扩张最后一张图填满整行
  - 宽度值 clamp 到 [1, 12] 范围
  - 保留原 `append_charts` 向后兼容
- `select_chart` 节点输出 `suggested_width`：从 chart registry 读取 `default_width`
- `create_chart` 节点输出 `suggested_width`：传递到 `created_chart` 字典
- `create_dashboard` 节点收集 `chart_widths` 字典传给 `CreateDashboardTool`
- `CreateDashboardTool` 接受 `chart_widths` 参数，调用 `append_charts_v2`
- 前端 `analysis_plan` 事件处理：`types.ts` 新增 `AnalysisPlanData`，`useAiChat.ts` 处理 `analysis_plan` 事件渲染计划摘要
- 嵌入式仪表板工具：`EmbedDashboardTool` 封装 Superset 原生 `EmbeddedDashboardDAO`
- 12 个单元测试全部通过

**关键文件**：
- `superset/ai/chart_types/schema.py` — `ChartTypeDescriptor` 新增 `default_width`
- `superset/ai/chart_types/catalog.py` — 24 种图表类型补全 `default_width`
- `superset/commands/dashboard/export.py` — `append_charts_v2` 流式装箱算法
- `superset/ai/graph/nodes_child.py` — `select_chart` 输出 `suggested_width`，`create_chart` 传递宽度
- `superset/ai/graph/state.py` — `ChartPlan` 新增 `suggested_width` 字段
- `superset/ai/graph/nodes_parent.py` — `create_dashboard` 传递 `chart_widths`
- `superset/ai/tools/create_dashboard.py` — 接受 `chart_widths`，调用 `append_charts_v2`
- `superset/ai/tools/embed_dashboard.py` — 新增嵌入式仪表板工具
- `superset/ai/agent/copilot_agent.py` — 注册 `EmbedDashboardTool`（第 11 个工具）
- `superset-frontend/src/features/ai/types.ts` — 新增 `AnalysisPlanData` 接口
- `superset-frontend/src/features/ai/hooks/useAiChat.ts` — 处理 `analysis_plan` 事件
- `tests/unit_tests/ai/test_dynamic_layout.py` — 12 个单元测试
- `append_charts_v2`：流式装箱算法，按图表类型分配 width（KPI:3, 饼图:4, 折线/柱状:6, 表格:12）
- `catalog.py` 新增 `default_width` 和 `advanced_params_schema` 字段
- 白名单参数逃生舱：仅允许纯视觉参数（color_scheme、show_legend 等）透传
- 尾部补全策略：行末剩余宽度自动扩张至填满整行

---

## 三、技术栈

| 层级 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Ant Design + @superset-ui/core |
| 后端 | Flask + SQLAlchemy + Celery + Redis |
| AI 框架 | LangGraph StateGraph + LangChain (双路径) |
| LLM | GLM-4.7-Flash (LM Studio 本地部署, OpenAI 兼容 API) |
| 流式传输 | Redis Streams (xadd/xrange) + HTTP 轮询 (500ms) |
| 数据库 | PostgreSQL |
| 容器化 | Docker Compose (superset + worker + node + redis + db) |

---

## 四、配置项

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `AI_AGENT` | False | 总开关 |
| `AI_AGENT_NL2SQL` | False | NL2SQL 模式 |
| `AI_AGENT_CHART` | False | Chart 模式 |
| `AI_AGENT_DASHBOARD` | False | Dashboard 模式 |
| `AI_AGENT_DEBUG` | False | Debug 模式 |
| `AI_AGENT_COPILOT` | False | Phase 15: Copilot 模式 |
| `AI_AGENT_USE_LANGCHAIN` | False | LangChain ReAct 路径 |
| `AI_AGENT_USE_STATEGRAPH` | False | StateGraph 管线路径 |
| `AI_LLM_DEFAULT_PROVIDER` | "openai" | LLM Provider |
| `AI_LLM_PROVIDERS` | {} | LLM 配置（model, api_key, base_url 等） |
| `AI_AGENT_MAX_TURNS` | 10 | Agent 最大轮次 |
| `AI_AGENT_TIMEOUT` | 180 | Agent 超时（秒） |

---

## 五、文件清单

### 后端（70+ 个 Python 文件）

```
superset/ai/
├── __init__.py
├── api.py                          # REST API 端点
├── schemas.py                      # 请求/响应 Schema
├── config.py                       # 配置辅助函数
├── tasks.py                        # Celery 任务
├── runner.py                       # Agent Runner 工厂
├── metric_catalog.py               # Phase 13: 业务指标目录引擎
├── metric_catalog.yaml             # Phase 13: 指标定义文件
├── commands/
│   └── chat.py                     # Chat 命令
├── agent/
│   ├── base.py                     # ReAct Agent 基类
│   ├── events.py                   # 12 种事件类型
│   ├── context.py                  # ConversationContext（Redis + 多轮）
│   ├── nl2sql_agent.py             # NL2SQL Agent
│   ├── chart_agent.py              # Chart Agent
│   ├── dashboard_agent.py          # Dashboard Agent
│   ├── debug_agent.py              # Debug Agent
│   ├── copilot_agent.py            # Phase 15: Copilot 大管家
│   └── langchain/                  # LangChain 集成
├── graph/
│   ├── state.py                    # TypedDict 定义（含 Phase 18/19 字段）
│   ├── nodes_parent.py             # 父图节点（含 Phase 18 多表、Phase 19 确认）
│   ├── nodes_child.py              # 子图节点（含 Phase 14 修改、Phase 11 insight）
│   ├── normalizer.py               # 参数归一化（6 条规则）
│   ├── llm_helpers.py              # LLM 调用辅助
│   ├── builder.py                  # 图构建器（含修改路径、clarify 路径）
│   └── runner.py                   # StateGraph Runner
├── router/                         # Phase 16: 意图路由
│   ├── router.py                   # IntentRouter 主逻辑
│   ├── rules.py                    # 关键词路由规则表
│   ├── llm_classifier.py           # LLM 兜底分类器
│   └── types.py                    # 路由类型定义
├── tools/
│   ├── search_datasets.py          # 数据集搜索（4 级模糊）
│   ├── get_schema.py               # Schema 获取
│   ├── execute_sql.py              # SQL 执行
│   ├── analyze_data.py             # 数据分析
│   ├── create_chart.py             # 图表创建
│   ├── create_dashboard.py         # 仪表板创建
│   ├── list_databases.py           # Phase 15: 数据库列表
│   ├── list_charts.py              # Phase 15: 图表列表
│   ├── list_dashboards.py          # Phase 15: 仪表板列表
│   ├── get_chart_detail.py         # Phase 15: 图表详情
│   ├── get_dashboard_detail.py     # Phase 15: 仪表板详情
│   ├── get_dataset_detail.py       # Phase 15: 数据集详情
│   ├── query_history.py            # Phase 15: 查询历史
│   ├── report_status.py            # Phase 15: 报表状态
│   ├── saved_query.py              # Phase 15: 保存的查询
│   └── whoami.py                   # Phase 15: 用户身份
├── chart_types/
│   └── registry.py                 # 24 种图表类型注册表
├── llm/
│   ├── base.py                     # LLM Provider 基类
│   ├── registry.py                 # Provider 注册表
│   ├── openai_provider.py          # OpenAI 兼容
│   ├── anthropic_provider.py       # Anthropic
│   └── ollama_provider.py          # Ollama 本地
├── prompts/                        # 系统 Prompt 模板
└── streaming/
    └── manager.py                  # Redis Streams 管理
```

### 前端（9 个 TypeScript 文件）

```
superset-frontend/src/features/ai/
├── types.ts                        # 类型定义（含 AiStep, ChartResult 等）
├── api/
│   └── aiClient.ts                 # API 客户端
├── hooks/
│   └── useAiChat.ts                # Chat Hook（11 种事件处理）
└── components/
    ├── AiChatPanel.tsx             # 主聊天面板
    ├── AiChatDrawer.tsx            # Drawer 包装
    ├── AiMessageBubble.tsx         # 消息气泡
    ├── AiSqlPreview.tsx            # SQL 预览
    ├── AiStreamingText.tsx         # 流式文本（闪烁光标）
    └── AiStepProgress.tsx          # 步骤进度组件（Phase 9）
```

---

## 六、设计文档索引

| 文档 | 阶段 |
|---|---|
| `docs/ai/00_overall_design.md` | 总体设计 |
| `docs/ai/phase1_nl2sql.md` | Phase 1 NL2SQL |
| `docs/ai/phase2_chart_agent.md` | Phase 2 Chart Agent |
| `docs/ai/phase3_auto_debug.md` | Phase 3 Auto-Debug |
| `docs/ai/phase4_dashboard_agent.md` | Phase 4 Dashboard |
| `docs/ai/phase5_smart_chart.md` | Phase 5 智能选型 |
| `docs/ai/phase6_dashboard_upgrade.md` | Phase 6 Dashboard 升级 |
| `docs/ai/phase7_langchain_refactor.md` | Phase 7 LangChain |
| `docs/ai/phase8_stategraph.md` | Phase 8 StateGraph 设计 |
| `docs/ai/phase8_stategraph_final.md` | Phase 8 StateGraph 终稿 |
| `docs/ai/phase9_frontend_events.md` | Phase 9 前端事件渲染 |
| `docs/ai/phase10_e2e_test_plan.md` | Phase 10 E2E 测试 |
| `docs/ai/phase11_multi_turn_conversation.md` | Phase 11 多轮对话 |
| `docs/ai/phase12_dataset_discovery.md` | Phase 12 数据集模糊搜索 |
| `docs/ai/phase13_metric_catalog.md` | Phase 13 业务指标目录 |
| `docs/ai/phase14_chart_modification.md` | Phase 14 图表修改 |
| `docs/ai/phase15_copilot_agent.md` | Phase 15 Copilot 大管家 |
| `docs/ai/phase16_intent_router.md` | Phase 16 意图路由 |
| `docs/ai/phase17_clarification_loop.md` | Phase 17 澄清追问 |
| `docs/ai/phase19_dynamic_layout_and_params.md` | Phase 19 动态布局引擎 & Plan Mode（动态布局未实现） |
| `docs/ai/phase19_plan_analysis_confirmation.md` | Phase 19 Plan 分析确认 |

---

## 七、Git 提交历史

```
75a3771 feat(ai): Phase 19 plan analysis confirmation — confidence scoring and dual-mode execution
aa7d995 docs(ai): add Phase 19 design — dynamic layout engine & plan analysis confirmation mode
428a198 feat(ai): Phase 14 chart modification with intent classification and in-place update
b94a8f8 feat(ai): Phase 18 multi-dataset dashboard support with keyword-based table assignment
796f3fc fix(ai): multi-chart dashboard generation and table groupby dedup
fdd4e59 fix(ai): add R7 safety net for table chart groupby
7eb1d72 fix(ai): merge x_axis into groupby for table charts
c58cee5 fix(ai): auto-fill granularity_sqla for big_number charts
2c4ed5e fix(ai): resolve chart generation dimension loss and row layout bugs in Phase 14
f2c3faa feat(ai): add business metric catalog (Phase 13) and fix 3 bugs
c01fa14 feat(ai): wire up Phase 16 intent router indicator and Phase 17 clarification UI
5478f54 fix(ai): resolve 6 bugs from code review + Codex P2 fix for event suppression
27e4774 feat(ai): add multi-turn conversation context (Phase 11) and fuzzy dataset discovery (Phase 12)
e248257 feat(ai): add intent router (Phase 16), clarification loop (Phase 17), checkpointer, and LangSmith config
2ecc812 feat(ai): enable LangSmith tracing for agent observability
2ae59e2 feat(ai): add Copilot agent with 10 asset-query tools (Phase 15)
525fbef feat(ai): add standalone AI Assistant workspace page
f94bf93 feat(ai): add tool summaries for multi-turn context and Codex review fixes
ce32248 feat(ai): observability, maintainability, multi-turn, and Codex review fixes
09e3b01 feat(ai): Phase 9 — frontend event rendering for StateGraph progress
75b35dd feat(ai): Phase 8 — LangGraph StateGraph pipeline for chart/dashboard agents
2824b54 fix(ai): harden S3/S4 - tool-layer order guard, params-hash idempotency, 23 tests
400ec2e feat(ai): S1-S4 GLM stability fixes — dynamic prompt, param normalization, sequential pipeline, idempotency
a86f116 fix(ai): fix user context and tool repetition guard in LangChain path
3b1d051 feat(ai): add Phase 7 LangChain/LangGraph integration with feature flag
946796c fix(ai): disable parallel tool calls
ed16d22 feat(ai): add Phase 6 dashboard agent with smart chart selection + bug fixes
a6cc1fd feat(ai): add Phase 5 smart chart type selection with data analysis workflow
15288ca feat(ai): add Phase 4 dashboard creation agent with one-sentence dashboard builder
a56a814 feat(ai): add Phase 3 auto-debug agent with SQL error diagnosis
5bbc45f feat(ai): add Phase 2 chart creation agent with frontend mode toggle
349f33c fix(ai): resolve NL2SQL agent bugs and add Phase 1/2 documentation
dccaac0 feat(ai): integrate AI Chat Drawer into SQL Lab editor
e2c81fc feat(ai): implement NL2SQL agent with full AI framework infrastructure
```

---

## 八、待实现事项

> 所有 P0/P1 事项已在本次迭代中完成。以下为剩余低优先级待办。

| 优先级 | 事项 | 说明 | 设计文档 |
|--------|------|------|----------|
| 🟢 P2 | `AiChartPreview` 组件 | 图表创建完成后仅显示链接，可在 Chat 内嵌 iframe 预览 | `00_overall_design.md` §六 |
| 🟢 P2 | 白名单参数逃生舱 | `normalizer.py` 新增 `SAFE_VISUAL_PARAMS` 白名单，允许纯视觉参数透传 | `phase19_dynamic_layout_and_params.md` §二 |

**本次迭代已完成：**
- ✅ Phase 19b 动态布局引擎 — `append_charts_v2` + 24 种图表 `default_width` + 12 个单元测试
- ✅ 前端 `analysis_plan` 事件处理 — `types.ts` + `useAiChat.ts` 结构化渲染
- ✅ 嵌入式仪表板 AI 工具 — `EmbedDashboardTool`（Copilot 第 11 个工具）
- ✅ 全局导航栏 AI 入口 — 已集成在 `Menu.tsx` 中，路由 `/aiassistant`

---

## 九、后续可选迭代

### 白名单参数逃生舱（P2）

`normalizer.py` 中新增 `SAFE_VISUAL_PARAMS` 白名单，仅允许纯视觉参数（`color_scheme`、`show_legend`、`donut` 等）从 LLM 输出透传到 `form_data`。需在 `catalog.py` 的 `advanced_params_schema` 中定义每种图表的合法参数及取值范围。

**风险**：LLM 幻觉字段名（如 `donut=true` 实际应为 `innerRadius` 数值），需要严格的 schema 校验。建议等动态布局稳定后再推进。

### AiChartPreview 组件（P2）

图表创建完成后仅显示链接，可在 Chat 内嵌 iframe 预览。需要考虑 Superset 的权限和跨域策略。
