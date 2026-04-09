# Superset AI Agent 集成设计文档

## Context

Apache Superset 是一个成熟的数据可视化平台，但缺少 AI/LLM 能力。参照 Coze CLI 的产品理念——"Agent 获得执行能力，贯穿从创建到发布的全流程"——我们设计一套 4 阶段迭代方案，让 Superset 用户能用自然语言完成从 SQL 查询到仪表板交付的全链路操作。

当前代码现状：零 AI 功能，但 REST API、Feature Flag、Celery 异步任务、ExtensionsRegistry 插件系统、嵌入式仪表板等基础设施齐全。

---

## 一、整体架构

### 新增顶层模块

```
superset/ai/
├── __init__.py
├── api.py                    # REST API (AiAgentRestApi)
├── schemas.py                # Marshmallow 请求/响应 Schema
├── config.py                 # AI 相关默认配置
│
├── llm/                      # LLM 提供商抽象层
│   ├── __init__.py
│   ├── base.py               # BaseLLMProvider（插件自动注册）
│   ├── openai_provider.py    # OpenAI 实现
│   ├── anthropic_provider.py # Claude 实现
│   ├── ollama_provider.py    # 本地模型实现
│   ├── registry.py           # 提供商注册表
│   └── types.py              # LLMMessage, LLMResponse, LLMStreamChunk
│
├── agent/                    # Agent 框架
│   ├── __init__.py
│   ├── base.py               # BaseAgent（ReAct 推理循环）
│   ├── nl2sql_agent.py       # Phase 1: 自然语言转 SQL
│   ├── chart_agent.py        # Phase 2: 一句话建图表
│   ├── debug_agent.py        # Phase 3: 自动排错
│   ├── dashboard_agent.py    # Phase 4: 一句话建仪表板
│   └── context.py            # ConversationContext（Redis 缓存）
│
├── tools/                    # Agent 工具集
│   ├── __init__.py
│   ├── base.py               # BaseTool 抽象类
│   ├── execute_sql.py        # 执行 SQL
│   ├── get_schema.py         # 获取数据库 Schema
│   ├── create_chart.py       # 创建图表
│   ├── create_dashboard.py   # 创建仪表板
│   ├── fix_error.py          # 诊断修复错误
│   ├── embed_dashboard.py    # 生成嵌入式链接
│   └── search_datasets.py    # 搜索数据集
│
├── prompts/                  # Prompt 模板
│   ├── __init__.py
│   ├── nl2sql.py
│   ├── chart_creation.py
│   ├── debug.py
│   └── dashboard_creation.py
│
├── commands/
│   ├── __init__.py
│   └── chat.py               # AiChatCommand 入口
│
├── streaming/
│   ├── __init__.py
│   └── manager.py            # AiStreamManager（复用 Redis Streams）
│
└── tasks.py                  # Celery 异步任务
```

### 数据流

```
用户输入自然语言
    ↓
POST /api/v1/ai/chat/  (AiChatCommand)
    ↓
选择 Agent → 构建 Prompt + 工具列表 → 调用 LLM
    ↓
ReAct 循环: LLM 推理 → 调用工具 → 反馈结果 → 继续推理
    ↓
每个事件通过 Redis Stream 推送到前端
    ↓
前端轮询 GET /api/v1/ai/events/ 展示流式结果
```

---

## 二、LLM 提供商抽象层

### 插件模式（复用 BaseNotification 自动注册模式）

```python
# superset/ai/llm/base.py
class BaseLLMProvider:
    plugins: list[type["BaseLLMProvider"]] = []
    provider_name: str

    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        cls.plugins.append(cls)

    def chat(self, messages, tools=None) -> LLMResponse: ...
    def chat_stream(self, messages, tools=None) -> Iterator[LLMStreamChunk]: ...
```

### 配置项（添加到 `superset/config.py`）

```python
# Feature Flags
"AI_AGENT": False,           # 总开关
"AI_AGENT_NL2SQL": False,    # Phase 1
"AI_AGENT_CHART": False,     # Phase 2
"AI_AGENT_DEBUG": False,     # Phase 3
"AI_AGENT_DASHBOARD": False, # Phase 4

# LLM 配置
AI_LLM_DEFAULT_PROVIDER = "openai"
AI_LLM_PROVIDERS = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "temperature": 0.0,
        "max_tokens": 4096,
    },
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "temperature": 0.0,
        "max_tokens": 4096,
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "llama3",
        "temperature": 0.0,
    },
}
AI_AGENT_MAX_TURNS = 10
AI_AGENT_TIMEOUT = 60
AI_AGENT_STREAM_CHANNEL_PREFIX = "ai-agent-"
```

HTTP 调用复用 `superset/utils/retries.py:retry_call()` + `requests` 库。

---

## 三、Agent 框架

### ReAct 循环（`superset/ai/agent/base.py`）

```python
class BaseAgent(ABC):
    def run(self, user_message: str) -> Iterator[AgentEvent]:
        messages = self._context.get_history()
        messages.insert(0, {"role": "system", "content": self.get_system_prompt()})
        messages.append({"role": "user", "content": user_message})

        for turn in range(self._max_turns):
            response = self._provider.chat_stream(messages, tools=self._get_tool_defs())
            tool_calls = []
            for chunk in response:
                yield chunk
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)

            if not tool_calls:
                return  # LLM 返回最终答案

            for tc in tool_calls:
                result = self._tools[tc.name].run(tc.arguments)
                messages.append({"role": "tool", "content": result})
                yield ToolResultEvent(tool=tc.name, result=result)
```

### 会话上下文（`superset/ai/agent/context.py`）

使用现有 `cache_manager`（Redis）存储对话历史，按 `user_id + session_id` 隔离，保留最近 20 轮对话，TTL 1 小时。

---

## 四、工具系统

| 工具 | 功能 | 复用的 Superset 模块 |
|------|------|---------------------|
| `execute_sql` | 执行 SQL 并返回结果 | `sql_lab.py:get_sql_results()` + `ExecuteSqlCommand` |
| `get_schema` | 获取数据库表/列元数据 | `DatabaseDAO` + `SqlaTable.columns` |
| `create_chart` | 创建图表 | `CreateChartCommand` (`superset/commands/chart/create.py`) |
| `create_dashboard` | 创建仪表板 | `CreateDashboardCommand` (`superset/commands/dashboard/create.py`) |
| `fix_error` | 诊断并修复错误 | `db_engine_spec.extract_errors()` + `SupersetErrorType` |
| `embed_dashboard` | 生成嵌入式链接 | `EmbeddedDashboardDAO` |
| `search_datasets` | 搜索数据集 | `DatasetDAO` |

每个工具继承 `BaseTool`，定义 `name`、`description`、`parameters_schema`（JSON Schema，供 LLM function calling 使用）。工具执行继承当前用户的权限。

---

## 五、API 设计

### 端点

| Phase | Method | Path | 说明 |
|-------|--------|------|------|
| All | `POST` | `/api/v1/ai/chat/` | 主入口：`{message, database_id, schema, agent_type, session_id}` |
| All | `GET` | `/api/v1/ai/events/?last_id=` | 轮询流式事件 |
| 1 | `POST` | `/api/v1/ai/nl2sql/` | 便捷接口：直接返回 SQL |
| 2 | `POST` | `/api/v1/ai/create-chart/` | 便捷接口：返回 chart_id |
| 3 | `POST` | `/api/v1/ai/debug/` | 接收错误上下文，返回修复方案 |
| 4 | `POST` | `/api/v1/ai/create-dashboard/` | 返回 dashboard_id + 嵌入式 URL |

### 注册位置

- 后端：`superset/initialization/__init__.py` 的 `init_views` 中添加 `appbuilder.add_api(AiAgentRestApi)`
- 前端 Feature Flag：`superset-frontend/packages/superset-ui-core/src/utils/featureFlags.ts` 的 `FeatureFlag` 枚举

---

## 六、前端集成

### 新增文件

```
superset-frontend/src/features/ai/
├── components/
│   ├── AiChatPanel.tsx          # 主聊天面板（抽屉）
│   ├── AiMessageBubble.tsx      # 消息气泡
│   ├── AiStreamingText.tsx      # 流式文本显示
│   ├── AiSqlPreview.tsx         # SQL 预览（语法高亮）
│   └── AiChartPreview.tsx       # 图表预览
├── hooks/
│   ├── useAiChat.ts             # 聊天状态管理
│   └── useAiStream.ts           # 轮询流式事件
└── api/
    └── aiClient.ts              # API 客户端
```

### 扩展点注册

| 扩展点 | 位置 | 用途 |
|--------|------|------|
| `sqleditor.extension.form` | SQL Lab 编辑器 | Phase 1: NL2SQL 输入框 |
| `dashboard.nav.right` | 仪表板导航栏 | Phase 4: AI 创建仪表板 |
| `navbar.right` | 全局导航栏 | 全局 AI 聊天入口 |
| `welcome.banner` | 首页 | AI 快捷入口 |

---

## 七、流式推送

复用 Global Async Queries (GAQ) 的 Redis Streams 模式：

```
Celery Task (run_agent)
    → AiStreamManager.publish_event(channel_id, event)
    → Redis Stream xadd

Frontend Polling
    → GET /api/v1/ai/events/?last_id=xxx
    → Redis Stream xrange
    → 渲染事件
```

事件类型：`thinking` → `text_chunk` → `tool_call` → `tool_result` → `sql_generated` / `chart_created` / `error_fixed` / `dashboard_created` → `done`

---

## 八、安全模型

| 维度 | 策略 |
|------|------|
| 权限继承 | 工具执行继承当前用户权限（`override_user` 上下文） |
| SQL 安全 | LLM 生成的 SQL 经过 `SQLScript` 解析，禁止 DDL/DML |
| 速率限制 | `AI_AGENT_RATE_LIMIT = "30/minute"`，Redis 计数 |
| 审计日志 | 每次交互记录：user_id, agent_type, tools_called, tokens_used |
| 数据脱敏 | Schema 元数据脱敏后注入 Prompt，不暴露行级数据 |

---

## 九、四阶段实施计划

### Phase 1: NL2SQL（最小闭环）

**目标：** 用户输入自然语言 → Agent 生成 SQL → SQL Lab 执行 → 展示结果

**新增文件（11 个）：**
- `superset/ai/__init__.py`
- `superset/ai/api.py`
- `superset/ai/schemas.py`
- `superset/ai/llm/base.py`, `openai_provider.py`, `registry.py`, `types.py`
- `superset/ai/agent/base.py`, `context.py`, `nl2sql_agent.py`
- `superset/ai/tools/execute_sql.py`, `get_schema.py`
- `superset/ai/prompts/nl2sql.py`
- `superset/ai/commands/chat.py`
- `superset/ai/streaming/manager.py`
- `superset/ai/tasks.py`
- 前端: `features/ai/` 目录

**修改文件：**
- `superset/config.py` — 添加 AI 配置项和 Feature Flags
- `superset/initialization/__init__.py` — 注册 API
- `superset-frontend/.../featureFlags.ts` — 添加 FeatureFlag 枚举值

### Phase 2: 一句话建图表

**新增：** `chart_agent.py`, `create_chart.py`, `search_datasets.py`, `chart_creation.py`

**工具链：** NL2SQL → 分析数据类型 → 选择 viz_type → 构造 params → CreateChartCommand

### Phase 3: 自动排错

**新增：** `debug_agent.py`, `fix_error.py`, `debug.py`

**集成点：** `sql_lab.py:handle_query_error()` — 错误时展示 "AI Fix" 按钮

**修复闭环：** 读取错误 → extract_errors() → 分析根因 → 修复 SQL/配置 → 重跑

### Phase 4: 一句话建仪表板 + 交付

**新增：** `dashboard_agent.py`, `create_dashboard.py`, `embed_dashboard.py`, `dashboard_creation.py`

**交付链：** 自然语言 → 多图表 → 组装 position_json → 创建 Dashboard → 嵌入式配置 → 返回 `/embedded/<uuid>` 公网链接

---

## 十、验证方案

### Phase 1 验证
```bash
# 1. 启动 Superset（配置 OPENAI_API_KEY）
# 2. 在 SQL Lab 中使用 AI 输入框输入："查询每个部门的平均薪资"
# 3. 验证生成的 SQL 正确并可在 SQL Lab 中执行
# 4. 测试流式输出是否正常
# 5. 测试权限：非授权用户无法使用 AI 功能
```

### Phase 2 验证
```bash
# 1. 输入："用柱状图展示各部门人数"
# 2. 验证图表创建成功，参数正确（viz_type, metrics, groupby）
# 3. 验证图表可在 Explore 页面中查看
```

### Phase 3 验证
```bash
# 1. 故意执行一条错误 SQL（引用不存在的列）
# 2. 点击 "AI Fix" 按钮
# 3. 验证 Agent 识别错误、定位原因、修复并重新执行
```

### Phase 4 验证
```bash
# 1. 输入："创建一个销售数据仪表板，包含月度趋势图、区域分布饼图和销售排行表"
# 2. 验证仪表板创建成功，包含 3 个图表
# 3. 验证嵌入式链接可正常访问
```

### 单元测试
- `tests/unit_tests/ai/test_llm_providers.py` — Mock LLM API 响应
- `tests/unit_tests/ai/test_agents.py` — Agent 推理循环
- `tests/unit_tests/ai/test_tools.py` — 工具执行（Mock Superset 内部 API）
- `tests/unit_tests/ai/test_api.py` — API 端点测试
