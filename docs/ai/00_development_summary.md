# Superset AI Agent 开发总结

> 生成日期：2026-04-12
> 分支：`feature/supersonic`
> 提交数：16 commits (AI 相关)
> 文件数：57 Python + 9 TypeScript

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
