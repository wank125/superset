# Phase 1: NL2SQL — 设计与实现文档

> 状态：已完成并通过测试
> 测试环境：Docker Compose + LM Studio (GLM-4.7-Flash)
> 测试时间：2026-04-11
> 更新：`httpx` 已加入正式依赖，Docker 和干净安装环境均可导入 LLM Provider。

---

## 一、目标

用户在 SQL Lab 中输入自然语言，AI Agent 自动完成：

```
用户输入自然语言 → Agent 识别意图 → 查询数据库 Schema → 生成 SQL → 验证执行 → 返回结果 + SQL
```

用户可通过 "Copy to SQL Lab" 按钮将生成的 SQL 一键填入编辑器。

---

## 二、架构设计

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend (React)                                                │
│                                                                  │
│  SqlEditor                                                       │
│    ├─ ThunderboltOutlined 按钮 (FeatureFlag: AI_AGENT)           │
│    └─ AiChatDrawer                                               │
│         └─ AiChatPanel                                           │
│              ├─ useAiChat(databaseId)                            │
│              │    ├─ sendMessage → POST /api/v1/ai/chat/         │
│              │    └─ pollEvents  → GET  /api/v1/ai/events/       │
│              ├─ AiMessageBubble (user/assistant)                 │
│              ├─ AiSqlPreview (提取 SQL + "Copy to SQL Lab")      │
│              └─ AiStreamingText (流式文本 + 光标动画)             │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Backend (Flask + Celery)                                        │
│                                                                  │
│  AiAgentRestApi (BaseSupersetApi)                                │
│    ├─ POST /chat/ → run_agent_task.delay() → 返回 channel_id    │
│    └─ GET  /events/ → AiStreamManager.read_events()             │
│                                                                  │
│  Celery Worker                                                   │
│    └─ run_agent_task()                                           │
│         ├─ get_provider() → OpenAIProvider                       │
│         ├─ ConversationContext (Redis 缓存历史)                   │
│         └─ NL2SQLAgent.run(message)                              │
│              ├─ LLM streaming (ReAct 循环)                       │
│              ├─ GetSchemaTool → SQLAlchemy Inspector              │
│              ├─ ExecuteSqlTool → ExecuteSqlCommand                │
│              └─ 每个事件 → AiStreamManager.publish_event()       │
│                                                                  │
│  Redis Streams                                                   │
│    └─ ai-agent-{channel_id} (事件总线)                            │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
1. 用户输入 "查询birth_names表每年男孩和女孩的出生人数"
2. POST /api/v1/ai/chat/  →  返回 {"channel_id": "abc123"}
3. Celery Worker 启动 NL2SQLAgent
4. Agent ReAct 循环:
   Turn 1: LLM → "我需要先查看有哪些表" → get_schema()
   Turn 2: LLM → "让我看看 birth_names 的列" → get_schema(table_name="birth_names")
   Turn 3: LLM → 生成 SQL → execute_sql("SELECT EXTRACT(YEAR FROM ds)...")
   Turn 4: LLM → 解释结果 + 呈现最终 SQL → done
5. 每个事件通过 Redis Stream XADD 推送
6. 前端每 500ms 轮询 GET /api/v1/ai/events/?channel_id=abc123&last_id=xxx
7. 前端累积 text_chunk → 实时显示流式文本
8. 收到 done 事件 → 保存为完整消息，提取 SQL 显示 "Copy to SQL Lab"
```

---

## 三、文件清单

### 3.1 后端文件 (20 个)

#### 核心框架

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/__init__.py` | 17 | 模块标记 |
| `superset/ai/config.py` | 55 | 配置读取层（从 Flask config 读取 AI 相关配置） |
| `superset/ai/schemas.py` | 40 | Marshmallow 请求验证 Schema |
| `superset/ai/api.py` | 169 | REST API 端点（`AiAgentRestApi`） |
| `superset/ai/tasks.py` | 83 | Celery 异步任务（`run_agent_task`） |
| `superset/ai/commands/chat.py` | 94 | Command 模式入口（`AiChatCommand`） |

#### Agent 系统

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/agent/base.py` | 166 | `BaseAgent` — ReAct 推理循环基类 |
| `superset/ai/agent/events.py` | 41 | `AgentEvent` / `EventType` 事件类型定义 |
| `superset/ai/agent/context.py` | 70 | `ConversationContext` — Redis 对话历史管理 |
| `superset/ai/agent/nl2sql_agent.py` | 54 | `NL2SQLAgent` — 自然语言转 SQL 具体实现 |

#### LLM 提供商层

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/llm/base.py` | 62 | `BaseLLMProvider` — 插件自动注册抽象基类 |
| `superset/ai/llm/types.py` | 59 | `LLMMessage` / `LLMResponse` / `LLMStreamChunk` / `ToolCall` |
| `superset/ai/llm/registry.py` | 56 | `get_provider()` — 提供商工厂/注册表 |
| `superset/ai/llm/openai_provider.py` | 227 | `OpenAIProvider` — OpenAI 兼容 API 实现（含 SSE 流式解析） |
| `superset/ai/llm/anthropic_provider.py` | — | `AnthropicProvider` — Claude 实现（骨架） |
| `superset/ai/llm/ollama_provider.py` | — | `OllamaProvider` — 本地模型实现（骨架） |

#### 工具集

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/tools/base.py` | 42 | `BaseTool` — 工具抽象基类 |
| `superset/ai/tools/get_schema.py` | 161 | `GetSchemaTool` — 数据库 Schema 查询工具 |
| `superset/ai/tools/execute_sql.py` | 123 | `ExecuteSqlTool` — SQL 执行工具（只读、RBAC） |

#### Prompt 模板

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/prompts/nl2sql.py` | 51 | NL2SQL 系统提示词 |

#### 流式推送

| 文件 | 行数 | 说明 |
|------|------|------|
| `superset/ai/streaming/__init__.py` | — | 包标记 |
| `superset/ai/streaming/manager.py` | 121 | `AiStreamManager` — Redis Streams 事件总线 |

### 3.2 前端文件 (9 个)

| 文件 | 行数 | 说明 |
|------|------|------|
| `features/ai/types.ts` | 56 | TypeScript 类型定义 |
| `features/ai/api/aiClient.ts` | 43 | API 客户端（`sendChat` / `fetchEvents`） |
| `features/ai/hooks/useAiChat.ts` | 180 | 聊天状态管理 Hook（轮询 + 文本累积） |
| `features/ai/hooks/useAiStream.ts` | 92 | 底层流式轮询 Hook（备用） |
| `features/ai/components/AiChatDrawer.tsx` | 54 | 抽屉容器（420px 宽） |
| `features/ai/components/AiChatPanel.tsx` | 165 | 聊天面板主体 |
| `features/ai/components/AiMessageBubble.tsx` | 47 | 消息气泡组件 |
| `features/ai/components/AiSqlPreview.tsx` | 90 | SQL 预览 + "Copy to SQL Lab" 按钮 |
| `features/ai/components/AiStreamingText.tsx` | 55 | 流式文本显示（带闪烁光标） |

### 3.3 配置文件

| 文件 | 说明 |
|------|------|
| `docker/pythonpath_dev/superset_config.py` | 基础 Docker 配置（含嵌入式仪表板支持） |
| `docker/pythonpath_dev/superset_config_docker.py` | AI Agent 覆盖配置（Feature Flags + LLM + Celery） |
| `docker/.env` | 环境变量（API Key、Redis、数据库） |

### 3.4 修改的现有文件

| 文件 | 修改内容 |
|------|----------|
| `superset/initialization/__init__.py` | 在 `init_views` 中注册 `AiAgentRestApi` |
| `superset-frontend/.../featureFlags.ts` | 添加 `AI_AGENT` / `AI_AGENT_NL2SQL` 枚举值 |
| `superset-frontend/.../SqlEditor/index.tsx` | 集成 AI 按钮 + `AiChatDrawer` |

---

## 四、核心设计决策

### 4.1 ReAct 推理循环 (`BaseAgent.run`)

```python
def run(self, user_message: str) -> Iterator[AgentEvent]:
    messages = [system_prompt] + history + [user_message]

    for turn in range(max_turns):          # 默认最多 10 轮
        response = provider.chat_stream(messages, tools=tool_defs)

        for chunk in response:
            yield AgentEvent(type="text_chunk", data={"content": chunk.content})
            # 同时累积完整内容 + 收集 tool_calls

        if no_tool_calls:
            break  # LLM 给出最终答案

        for tool_call in tool_calls:
            result = tools[tool_call.name].run(tool_call.arguments)
            messages.append(tool_result_message)
            yield AgentEvent(type="tool_result", ...)

    yield AgentEvent(type="done", data={})
```

**设计要点：**
- 最多 10 轮推理（防止无限循环）
- 流式输出（每个 token 立即推送到前端）
- Tool Call 粒度（LLM 可同时调用多个工具）
- 对话历史自动保存到 Redis（TTL 1 小时）

### 4.2 LLM 提供商插件化

```python
class BaseLLMProvider(ABC):
    plugins: list[type[BaseLLMProvider]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.provider_name:
            cls.plugins.append(cls)  # 自动注册
```

**支持的提供商：**
- `openai` — OpenAI / Azure / LM Studio（OpenAI 兼容 API）
- `anthropic` — Claude（骨架）
- `ollama` — 本地模型（骨架）

当前使用 **LM Studio + GLM-4.7-Flash**，通过 `base_url` 指向 `http://host.docker.internal:1234/v1`。

### 4.3 事件流协议

| 事件类型 | 方向 | 数据 |
|----------|------|------|
| `thinking` | Worker → Redis | Agent 思考状态 |
| `text_chunk` | Worker → Redis | `{"content": "部分文本..."}` |
| `tool_call` | Worker → Redis | `{"tool": "get_schema", "arguments": {...}}` |
| `tool_result` | Worker → Redis | `{"tool": "get_schema", "result": "..."}` |
| `sql_generated` | Worker → Redis | `{"sql": "SELECT ..."}` |
| `done` | Worker → Redis | `{}` |
| `error` | Worker → Redis | `{"message": "错误信息"}` |

### 4.4 安全模型

| 维度 | 策略 |
|------|------|
| Feature Flag | `AI_AGENT` / `AI_AGENT_NL2SQL` 双重开关 |
| 认证 | `@protect(allow_browser_login=True)` — 支持浏览器 Cookie |
| RBAC | 工具执行继承当前用户权限（`override_user`） |
| SQL 安全 | `SQLScript.has_mutation()` 检查，禁止 DDL/DML |
| 数据库访问 | `security_manager.can_access_database()` 权限校验 |
| 速率限制 | 最多 120 次轮询（60 秒超时） |
| 流隔离 | 每个 `channel_id` 独立的 Redis Stream，1000 条上限 |

---

## 五、API 接口

### 5.1 `POST /api/v1/ai/chat/`

**请求体：**
```json
{
    "message": "查询birth_names表每年男孩和女孩的出生人数",
    "database_id": 1,
    "schema_name": "public",
    "agent_type": "nl2sql",
    "session_id": "ai-1712345678-abcd1234"
}
```

**响应 (200)：**
```json
{
    "channel_id": "abc123def456..."
}
```

### 5.2 `GET /api/v1/ai/events/`

**查询参数：**
- `channel_id` (必填) — 来自 `/chat/` 的响应
- `last_id` (可选) — 上次轮询返回的游标

**响应 (200)：**
```json
{
    "events": [
        {"id": "1712345678000-0", "type": "text_chunk", "data": {"content": "让我"}},
        {"id": "1712345678001-0", "type": "text_chunk", "data": {"content": "查看"}},
        {"id": "1712345678002-0", "type": "tool_call", "data": {"tool": "get_schema"}},
        {"id": "1712345678003-0", "type": "tool_result", "data": {"result": "..."}},
        {"id": "1712345678004-0", "type": "done", "data": {}}
    ],
    "last_id": "1712345678004-0"
}
```

---

## 六、配置参考

### 6.1 Feature Flags

```python
FEATURE_FLAGS = {
    "AI_AGENT": True,        # 总开关
    "AI_AGENT_NL2SQL": True, # Phase 1 NL2SQL
}
```

### 6.2 LLM 配置

```python
AI_LLM_DEFAULT_PROVIDER = "openai"

AI_LLM_PROVIDERS = {
    "openai": {
        "api_key_env": "LM_STUDIO_API_KEY",   # 环境变量名（非明文 Key）
        "model": "zai-org/glm-4.7-flash",
        "temperature": 0.0,
        "max_tokens": 4096,
        "base_url": "http://host.docker.internal:1234/v1",
    },
}
```

### 6.3 Agent 配置

```python
AI_AGENT_MAX_TURNS = 10                   # 最大推理轮数
AI_AGENT_TIMEOUT = 120                    # Celery 超时（秒）
AI_AGENT_MAX_CONTEXT_ROUNDS = 20          # 对话历史保留轮数
AI_AGENT_STREAM_CHANNEL_PREFIX = "ai-agent-"  # Redis Stream 前缀
```

### 6.4 Celery 配置

```python
class CeleryConfig:
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
        "superset.ai.tasks",   # ← 新增
    )
```

---

## 七、测试记录

### 7.1 API 直接测试

```bash
# 通过 Docker 容器内 curl 测试
curl -X POST http://localhost:8088/api/v1/ai/chat/ \
  -H "Content-Type: application/json" \
  -H "Cookie: session=xxx" \
  -H "X-CSRFToken: xxx" \
  -d '{"message": "查询birth_names表每年男孩和女孩的出生人数", "database_id": 1}'
```

**结果：** Agent 成功完成完整 NL2SQL 流程：
1. 调用 `get_schema()` 列出所有表
2. 调用 `get_schema(table_name="birth_names")` 获取列信息
3. 调用 `execute_sql()` 采样数据确认
4. 生成正确的 SQL：`SELECT EXTRACT(YEAR FROM ds), SUM(num_boys), SUM(num_girls) FROM birth_names GROUP BY 1 ORDER BY 1 DESC`
5. 执行 SQL 验证，返回 9 行结果（2000-2008 年）

### 7.2 Web UI 测试 (Playwright MCP)

```
步骤：
1. 导航到 http://localhost:9000/sqllab/
2. 点击闪电按钮打开 AI Chat Drawer
3. 输入 "查询birth_names表每年男孩和女孩的出生人数"
4. 按 Enter 发送
5. 等待 20 秒

结果：AI 返回完整中文解释 + SQL 代码块 + 数据分析摘要
"Copy to SQL Lab" 按钮可正常使用
```

### 7.3 已修复的问题

| 问题 | 根因 | 修复 |
|------|------|------|
| `httpx ModuleNotFoundError` | httpx 不在 Superset 依赖中 | 已加入项目依赖和锁定文件 |
| GetSchemaTool 查询 information_schema | PostgreSQL 默认返回第一个 schema | 优先选择 `public` schema |
| schema_name 未传递给工具 | NL2SQLAgent 存储但未传递 | 添加 `default_schema` 参数 |
| LLM 猜测表名失败 | get_schema 未提供可用表列表 | 表不存在时返回可用表列表 |
| 前端 401 "Missing Authorization Header" | FAB 5.0.0 `@protect()` 默认不支持 Cookie | 添加 `allow_browser_login=True` |
| 流式文本重复 | React 18 Strict Mode 双重执行 updater | 使用 `useRef` 替代嵌套 setState |
| `_increment_id("0")` 失败 | 初始游标无法解析 | 特殊处理 `"0"` → 返回 `"-"` |

---

## 八、已知限制

1. **Anthropic/Ollama 提供商** — 仅有骨架代码，未完整实现
2. **对话历史** — 仅保存在 Redis 内存中（TTL 1 小时），重启丢失
3. **无审计日志** — 未记录用户交互和工具调用
4. **无速率限制** — 未实现 `AI_AGENT_RATE_LIMIT` 配置项
5. **仅支持 SQL Lab** — 尚未集成到其他页面
