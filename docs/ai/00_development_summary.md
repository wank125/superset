# Superset AI Agent 开发总结

> 生成日期：2026-04-14
> 分支：`feature/supersonic`
> 提交数：30+ commits (AI 相关)
> 文件数：70+ Python + 9 TypeScript

---

## 一、项目概览

在 Apache Superset 平台上构建了一套完整的 AI Agent 系统，用户通过自然语言即可完成 SQL 查询、图表创建、仪表板生成和自动排错。系统支持 4 种 Agent 模式，后端采用 LangGraph StateGraph 管线架构，前端实现了节点级实时事件流渲染。

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

### Phase 14: 图表修改（未实现）

**目标**：支持用户对已创建图表进行修改（改颜色、换指标、调维度等）

**状态**：设计文档已完成（`docs/ai/phase14_chart_modification.md`），代码未实现。

**计划内容**：
- 4 个新节点：`classify_intent`、`load_existing_chart`、`apply_chart_modification`、`update_chart`
- State 新增字段：`previous_charts`、`existing_chart`、`modification`
- 意图分类：新建 vs 修改 vs 追问

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
| `AI_AGENT_USE_LANGCHAIN` | False | LangChain ReAct 路径 |
| `AI_AGENT_USE_STATEGRAPH` | False | StateGraph 管线路径 |
| `AI_LLM_DEFAULT_PROVIDER` | "openai" | LLM Provider |
| `AI_LLM_PROVIDERS` | {} | LLM 配置（model, api_key, base_url 等） |
| `AI_AGENT_MAX_TURNS` | 10 | Agent 最大轮次 |
| `AI_AGENT_TIMEOUT` | 120 | Agent 超时（秒） |

---

## 五、文件清单

### 后端（57 个 Python 文件）

```
superset/ai/
├── __init__.py
├── api.py                          # REST API 端点
├── schemas.py                      # 请求/响应 Schema
├── config.py                       # 配置辅助函数
├── tasks.py                        # Celery 任务
├── runner.py                       # Agent Runner 工厂
├── commands/
│   └── chat.py                     # Chat 命令
├── agent/
│   ├── base.py                     # ReAct Agent 基类
│   ├── events.py                   # 11 种事件类型
│   ├── nl2sql_agent.py             # NL2SQL Agent
│   ├── chart_agent.py              # Chart Agent
│   ├── dashboard_agent.py          # Dashboard Agent
│   ├── debug_agent.py              # Debug Agent
│   └── langchain/                  # LangChain 集成
├── graph/
│   ├── state.py                    # TypedDict 定义
│   ├── nodes_parent.py             # 父图节点
│   ├── nodes_child.py              # 子图节点
│   ├── normalizer.py               # 参数归一化
│   ├── llm_helpers.py              # LLM 调用辅助
│   ├── builder.py                  # 图构建器
│   └── runner.py                   # StateGraph Runner
├── tools/
│   ├── search_datasets.py          # 数据集搜索
│   ├── get_schema.py               # Schema 获取
│   ├── execute_sql.py              # SQL 执行
│   ├── analyze_data.py             # 数据分析
│   ├── create_chart.py             # 图表创建
│   └── create_dashboard.py        # 仪表板创建
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
| `docs/ai/phase14_chart_modification.md` | Phase 14 图表修改（未实现） |
| `docs/ai/phase15_copilot_agent.md` | Phase 15 Copilot 大管家 |
| `docs/ai/phase16_intent_router.md` | Phase 16 意图路由 |
| `docs/ai/phase17_clarification_loop.md` | Phase 17 澄清追问 |

---

## 七、Git 提交历史

```
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
