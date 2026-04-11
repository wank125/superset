# Phase 7：LangChain 重构 — 从手写 ReAct 到 LangGraph Agent

## 背景

Phase 1–6 构建了一套完整的 AI Agent 系统，基于**手写 ReAct 循环**（`BaseAgent.run()`）。当前架构如下：

```
API (api.py) → Celery Task (tasks.py) → AiChatCommand (chat.py)
    → _AGENT_MAP[agent_type] → BaseAgent.run() (ReAct loop)
        → BaseLLMProvider.chat_stream() → httpx → LLM API
        → BaseTool.run() → tool result → next LLM call
```

### 当前痛点

| 痛点 | 现状 | LangChain 解决方案 |
|---|---|---|
| ReAct 循环手写维护 | `base.py` 209 行，含流控/工具调用/安全防护 | `create_react_agent()` 内置 |
| 对话记忆自定义 | `ConversationContext` 手写 Redis 存取 | `RedisChatMessageHistory` + `RunnableWithMessageHistory` |
| 工具调用 JSON 手拼 | `openai_provider.py` 手动累积 streaming deltas | LangChain `ChatOpenAI` 自动处理 |
| LLM Provider 各自实现 | `OpenAIProvider` 232 行，含 streaming 累积逻辑 | `ChatOpenAI` + `init_chat_model` 统一 |
| Agent 创建分散 | `chat.py` + `tasks.py` 重复实例化 | `runner.py` 统一工厂 |

### 已知问题（Phase 6 实测发现）

| 问题 | 根因 | Phase 7 解决方案 |
|---|---|---|
| 并发/跳步调用（一轮多个 tool_calls） | 模型返回多个 tool_calls，执行顺序不可控 | `parallel_tool_calls=False` + 第一工具强制 |
| 重复工具调用卡住 | 模型陷入循环，反复调用同一个工具 | `ToolCallRepetitionGuard` 检测并纠错 |
| DashboardAgent 建图后不建仪表板 | ReAct 自由推理，模型可能跳过最后一步 | 拆到 Phase 8（StateGraph 强制顺序） |

### 预期收益

- **减少 ~500 行** 手写 LLM 交互代码（provider 3 个 → 1 个 LangChain wrapper）
- **零改动迁移**：Feature Flag 控制，默认关闭
- **Thinking 支持**：GLM-5.1 `reasoning_content` 通过 `GLMChatOpenAI` 自动提取
- **工具调用安全**：单工具强制 + 重复检测，防止模型行为失控
- **生态兼容**：后续可直接接入 LangSmith tracing、LangGraph Studio

## 架构设计

### 新架构 vs 旧架构

```
                    ┌──────────────┐
  旧：              │  api.py      │
                    │  (不变)       │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  tasks.py    │
                    │  (不变)       │
                    └──────┬───────┘
                           │
               ┌───────────▼────────────┐
               │  runner.py  (新增)      │◄── Feature Flag 分支
               │  create_agent_runner()  │
               └───┬────────────────┬───┘
                   │                │
          Flag=True│                │Flag=False
                   ▼                ▼
         ┌─────────────────────┐   ┌──────────────┐
         │ LangChain            │   │ Legacy        │
         │ AgentRunner          │   │ AiChatCommand │
         │ + 单工具强制          │   │ (旧路径)       │
         │ + 工具重复检测        │   │               │
         └─────────────────────┘   └──────────────┘
```

### 核心组件关系

```
superset/ai/
├── agent/
│   ├── base.py                 # 保留（Legacy 路径）
│   ├── langchain/              # 新增目录
│   │   ├── __init__.py
│   │   ├── runner.py           # AgentRunner — 统一入口
│   │   ├── llm.py              # GLMChatOpenAI + get_langchain_llm()
│   │   ├── memory.py           # LangChainMemoryAdapter — Redis 适配
│   │   ├── tools.py            # tool_adapter() — BaseTool → StructuredTool
│   │   ├── callbacks.py        # SafeguardCallbackHandler — 文本安全防护
│   │   ├── guard.py            # ToolCallRepetitionGuard — 工具重复检测（新增）
│   │   └── prompts.py          # prompt_adapter() — system prompt 注入
│   ├── chart_agent.py          # 保留（Legacy 路径）
│   ├── dashboard_agent.py      # 保留（Legacy 路径）
│   ├── nl2sql_agent.py         # 保留（Legacy 路径）
│   └── debug_agent.py          # 保留（Legacy 路径）
├── runner.py                   # 新增 — Feature Flag 工厂
├── commands/chat.py            # 改动 — 调用 runner.py
├── tasks.py                    # 改动 — 调用 runner.py
└── config.py                   # 改动 — 新增配置项
```

## 文件级详细设计

---

### 1. `superset/ai/runner.py`（新增，~60 行）

**职责**：Feature Flag 分支，返回统一接口 `run_agent()` 函数。

```python
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
"""Unified agent runner — dispatches to legacy or LangChain path."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from superset.ai.agent.events import AgentEvent


def create_agent_runner(
    agent_type: str,
    database_id: int,
    schema_name: str | None,
    user_id: int,
    session_id: str,
) -> AgentRunner:
    """Factory: return the appropriate runner based on feature flag.

    Returns an object with a ``run(message: str) -> Iterator[AgentEvent]``
    method — callers don't need to know which implementation is active.
    """
    from superset.ai.config import use_langchain
    if use_langchain():
        from superset.ai.agent.langchain.runner import LangChainAgentRunner
        return LangChainAgentRunner(
            agent_type=agent_type,
            database_id=database_id,
            schema_name=schema_name,
            user_id=user_id,
            session_id=session_id,
        )
    else:
        from superset.ai.commands.chat import LegacyAgentRunner
        return LegacyAgentRunner(
            agent_type=agent_type,
            database_id=database_id,
            schema_name=schema_name,
            user_id=user_id,
            session_id=session_id,
        )


class AgentRunner:
    """Abstract base — both paths implement this interface."""
    def run(self, message: str) -> Iterator[AgentEvent]:
        raise NotImplementedError
```

**关键设计**：
- `use_langchain()` 从 `current_app.config` 读取 `AI_AGENT_USE_LANGCHAIN`，默认 `False`
- 返回的对象只有 `.run(message)` 方法，与旧路径完全同接口
- LangChain 相关 import 全部延迟到 `use_langchain() == True` 分支内，不影响旧路径启动

---

### 2. `superset/ai/agent/langchain/runner.py`（新增，~120 行）

**职责**：LangChain/LangGraph Agent 的核心运行器。

```python
# 核心逻辑
class LangChainAgentRunner:
    """Run an agent using LangGraph's create_react_agent."""

    def __init__(self, agent_type, database_id, schema_name,
                 user_id, session_id):
        self._agent_type = agent_type
        self._database_id = database_id
        self._schema_name = schema_name
        self._user_id = user_id
        self._session_id = session_id

    def run(self, message: str) -> Iterator[AgentEvent]:
        llm = get_langchain_llm()
        tools = self._get_langchain_tools()
        memory = self._get_memory()
        prompt = self._get_system_prompt()

        agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=prompt,
        )

        config = {
            "configurable": {"session_id": self._session_id},
            "callbacks": [SafeguardCallbackHandler()],
            "recursion_limit": get_max_turns(),
        }

        # Append user message to memory BEFORE invoking
        memory.add_user_message(message)

        # stream_mode=["messages", "updates"]
        #   "messages" → (AIMessageChunk, metadata) → text_chunk events
        #   "updates"  → complete tool call/result snapshots
        for mode, chunk in agent.stream(
            {"messages": memory.get_messages()},
            config=config,
            stream_mode=["messages", "updates"],
        ):
            for event in self._translate_event(mode, chunk):
                yield event

        # Persist assistant response to memory AFTER completion
        # (LangGraph does not auto-write to external memory)
        full_response = ...  # accumulated from stream
        memory.add_ai_message(full_response)

    def _get_langchain_tools(self) -> list[StructuredTool]:
        """Build LangChain tools for the current agent type."""
        from superset.ai.agent.langchain.tools import tool_adapter
        native_tools = self._get_native_tools()  # BaseTool list
        return [tool_adapter(t) for t in native_tools]

    def _get_native_tools(self) -> list[BaseTool]:
        """Return BaseTool instances (same logic as current agents)."""
        _TOOL_MAP = {
            "nl2sql": [GetSchemaTool, ExecuteSqlTool],
            "chart": [GetSchemaTool, ExecuteSqlTool, AnalyzeDataTool,
                      SearchDatasetsTool, CreateChartTool],
            "debug": [GetSchemaTool, ExecuteSqlTool],
            "dashboard": [GetSchemaTool, ExecuteSqlTool, AnalyzeDataTool,
                          SearchDatasetsTool, CreateChartTool, CreateDashboardTool],
        }
        tool_classes = _TOOL_MAP.get(self._agent_type, _TOOL_MAP["nl2sql"])
        return [
            cls(database_id=self._database_id, ...)
            for cls in tool_classes
        ]

    def _get_memory(self) -> LangChainMemoryAdapter:
        """Build memory adapter backed by existing Redis keys."""
        from superset.ai.agent.langchain.memory import LangChainMemoryAdapter
        return LangChainMemoryAdapter(
            user_id=self._user_id,
            session_id=self._session_id,
        )

    def _get_system_prompt(self) -> ChatPromptTemplate:
        """Return a ChatPromptTemplate with the agent's system prompt."""
        from superset.ai.agent.langchain.prompts import prompt_adapter
        return prompt_adapter(self._agent_type, self._schema_name)
```

**事件翻译逻辑**（`_translate_event`）：

| LangGraph stream 模式 | chunk 内容 | 输出 AgentEvent |
|---|---|---|
| `"messages"` + `AIMessageChunk` | `.content` 非空 | `type="text_chunk"` |
| `"messages"` + `AIMessageChunk` | `.tool_call_chunks` 非空 | `type="tool_call"`（仅第一个） |
| `"messages"` + `ToolMessage` | tool result | `type="tool_result"` |
| `"updates"` | agent 节点完成 | 无需单独事件 |
| 流结束 | — | `type="done"` |

**工具调用安全**（在 `_translate_event` 中执行）：
1. 如果 `AIMessage.tool_calls` 包含多个调用，只执行第一个（`parallel_tool_calls=False` 的兜底）
2. 通过 `ToolCallRepetitionGuard.check()` 检测连续重复，超限时 yield error 事件

---

### 3. `superset/ai/agent/langchain/llm.py`（新增，~90 行）

**职责**：创建 LangChain LLM 实例，处理 GLM-5.1 特殊字段，强制单工具调用。

```python
"""LangChain LLM configuration for AI Agent."""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, AIMessageChunk

from superset.ai.config import (
    get_default_provider_name,
    get_llm_providers,
)

logger = logging.getLogger(__name__)


class GLMChatOpenAI(ChatOpenAI):
    """Extended ChatOpenAI that captures GLM reasoning_content.

    ZhiPu's thinking models (GLM-4, GLM-5.x) return a
    ``reasoning_content`` field alongside standard ``content``.
    The standard ChatOpenAI silently discards it; this subclass
    captures it and emits it as an additional metadata field.
    """

    def _stream_chunk_to_message_chunk(
        self, chunk: dict, **kwargs: Any
    ) -> AIMessageChunk:
        message = super()._stream_chunk_to_message_chunk(chunk, **kwargs)
        reasoning = chunk.get("reasoning_content") or (
            chunk.get("choices", [{}])[0]
            .get("delta", {})
            .get("reasoning_content")
        )
        if reasoning:
            message.additional_kwargs["reasoning_content"] = reasoning
        return message


def get_langchain_llm() -> ChatOpenAI:
    """Build a LangChain ChatOpenAI from Superset's AI config."""
    provider_name = get_default_provider_name()
    providers = get_llm_providers()
    cfg = providers.get(provider_name, {})

    api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
    import os
    api_key = os.environ.get(api_key_env, "")

    llm_cls = GLMChatOpenAI if "glm" in cfg.get("model", "").lower() else ChatOpenAI

    return llm_cls(
        model=cfg.get("model", "gpt-4o"),
        api_key=api_key,
        base_url=cfg.get("base_url"),
        temperature=cfg.get("temperature", 0.0),
        max_tokens=cfg.get("max_tokens", 4096),
        streaming=True,
        model_kwargs={
            "parallel_tool_calls": False,  # 强制一次只返回一个 tool_call
        },
    )
```

**关键决策**：
- `parallel_tool_calls=False`：通过 `model_kwargs` 传入，GLM OpenAI 兼容层支持此参数。
  如果模型不支持（如某些本地模型），该参数会被静默忽略，不报错。
- 第二道防线在 `runner.py` 的 `_translate_event` 中处理：如果仍返回多个 tool_calls，只执行第一个
- `GLMChatOpenAI` 继承 `ChatOpenAI`，仅覆盖 streaming chunk 解析
- `reasoning_content` 放入 `additional_kwargs`，由 `SafeguardCallbackHandler` 决定是否转发
- 非 GLM 模型（OpenAI、本地 Ollama）直接使用 `ChatOpenAI`

---

### 4. `superset/ai/agent/langchain/memory.py`（新增，~70 行）

**职责**：适配现有 `ConversationContext` Redis 键到 LangChain 消息列表。

```python
"""Memory adapter — bridges Superset ConversationContext ↔ LangChain messages."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage, BaseMessage,
)

from superset.ai.agent.context import ConversationContext

logger = logging.getLogger(__name__)


class LangChainMemoryAdapter:
    """Provides LangChain message lists from existing Redis-backed context.

    Reuses the same Redis keys as ConversationContext:
        ``ai:ctx:{user_id}:{session_id}``

    This ensures the LangChain path reads the same conversation history
    as the legacy path, allowing seamless switching via feature flag.
    """

    def __init__(self, user_id: int, session_id: str) -> None:
        self._ctx = ConversationContext(user_id=user_id, session_id=session_id)

    def get_messages(self) -> list[BaseMessage]:
        """Load history from Redis and convert to LangChain messages."""
        history = self._ctx.get_history()
        messages: list[BaseMessage] = []
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system":
                messages.append(SystemMessage(content=content))
            elif role == "tool":
                messages.append(ToolMessage(content=content))
        return messages

    def add_user_message(self, content: str) -> None:
        self._ctx.add_message("user", content)

    def add_ai_message(self, content: str) -> None:
        self._ctx.add_message("assistant", content)

    def clear(self) -> None:
        self._ctx.clear()
```

**关键设计**：
- **复用 Redis 键**：`ai:ctx:{user_id}:{session_id}`，LangChain 路径和 Legacy 路径共享数据
- **手动 add_user/add_ai**：LangGraph 的 `create_react_agent` 不自动写入外部 memory，
  因此 `runner.py` 在调用前后手动同步
- **不使用 `RunnableWithMessageHistory`**：那个 wrapper 假设自己管理全部消息状态，
  与我们的 Redis 键格式和 TTL 逻辑冲突。直接读/写 `ConversationContext` 更安全

---

### 5. `superset/ai/agent/langchain/tools.py`（新增，~60 行）

**职责**：将现有 `BaseTool` 实例转为 LangChain `StructuredTool`。

```python
"""Tool adapter — wraps Superset BaseTool as LangChain StructuredTool."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from superset.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _schema_to_pydantic(schema: dict[str, Any]) -> type[BaseModel]:
    """Convert a JSON Schema dict to a Pydantic model class.

    LangChain's StructuredTool requires a Pydantic model for args_schema.
    """
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        field_type = {
            "string": (str, ...),
            "integer": (int, ...),
            "number": (float, ...),
            "boolean": (bool, ...),
            "array": (list, ...),
            "object": (dict, ...),
        }.get(prop.get("type", "string"), (str, ...))

        if name not in required:
            field_type = (field_type[0] | None, None)  # make optional

        field_definitions[name] = field_type

    return create_model("ToolArgs", **field_definitions)


def tool_adapter(native_tool: BaseTool) -> StructuredTool:
    """Wrap a Superset BaseTool as a LangChain StructuredTool."""
    args_schema = _schema_to_pydantic(native_tool.parameters_schema)

    def _run(**kwargs: Any) -> str:
        return native_tool.run(kwargs)

    return StructuredTool.from_function(
        func=_run,
        name=native_tool.name,
        description=native_tool.description,
        args_schema=args_schema,
    )
```

**关键设计**：
- `BaseTool.parameters_schema` 已经是 JSON Schema 格式，直接转为 Pydantic model
- `StructuredTool.from_function` 是零侵入包装，不需要修改任何现有 tool 代码
- Tool 的 `run()` 方法返回 `str`，与 LangChain 的预期一致

---

### 6. `superset/ai/agent/langchain/callbacks.py`（新增，~80 行）

**职责**：在 LangChain/LangGraph 上下文中恢复安全防护（字符限制、重复检测）。

```python
"""Safeguard callbacks for LangChain agent execution."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


class SafeguardCallbackHandler(BaseCallbackHandler):
    """Enforce per-turn safety limits in the LangChain execution path.

    Mirrors the safeguards in BaseAgent:
      - _MAX_STREAM_CHARS = 10_000
      - _MAX_REPETITIONS = 8 (30-char tail)
    """

    _MAX_STREAM_CHARS = 10_000
    _MAX_REPETITIONS = 8
    _TAIL_LEN = 30

    def __init__(self) -> None:
        self._turn_chars = 0
        self._turn_text = ""
        self._stopped = False

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        """Reset per-turn counters when a new LLM call starts."""
        self._turn_chars = 0
        self._turn_text = ""
        self._stopped = False

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Check per-token safety limits."""
        if self._stopped:
            return

        self._turn_chars += len(token)
        self._turn_text += token

        if self._turn_chars > self._MAX_STREAM_CHARS:
            logger.warning("Stream exceeded %d chars, stopping", self._MAX_STREAM_CHARS)
            self._stopped = True
            raise StopIteration("Response too long, stopped early.")

        if len(self._turn_text) >= 200:
            tail = self._turn_text[-self._TAIL_LEN:]
            if self._turn_text.count(tail) >= self._MAX_REPETITIONS:
                logger.warning("Detected repetitive output, stopping")
                self._stopped = True
                raise StopIteration("Detected repetitive output, stopped.")

    @property
    def stopped(self) -> bool:
        return self._stopped
```

**关键设计**：
- `on_llm_start` 重置计数器（等同于 BaseAgent 中的 per-turn reset）
- `on_llm_new_token` 在每个 token 上检查限制
- `StopIteration` 异常会中断 LangChain 的 token 迭代器，但不会杀死整个 agent
- 与 BaseAgent 的安全参数完全一致

---

### 7. `superset/ai/agent/langchain/prompts.py`（新增，~50 行）

**职责**：从现有 agent 的 `get_system_prompt()` 构建 LangChain `ChatPromptTemplate`。

```python
"""Prompt adapter — builds ChatPromptTemplate from existing agent prompts."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from superset.ai.agent.chart_agent import ChartAgent
from superset.ai.agent.dashboard_agent import DashboardAgent
from superset.ai.agent.debug_agent import DebugAgent
from superset.ai.agent.nl2sql_agent import NL2SQLAgent


_AGENT_PROMPT_BUILDERS = {
    "nl2sql": NL2SQLAgent,
    "chart": ChartAgent,
    "debug": DebugAgent,
    "dashboard": DashboardAgent,
}


def prompt_adapter(
    agent_type: str,
    schema_name: str | None = None,
) -> ChatPromptTemplate:
    """Build a ChatPromptTemplate using the existing agent's system prompt.

    Reuses the exact same prompt text (including dynamic chart_type_table
    and chart_type_details injection) — zero duplication.
    """
    agent_cls = _AGENT_PROMPT_BUILDERS.get(agent_type, NL2SQLAgent)

    # Create a temporary agent instance just to get the system prompt
    # (provider and context are not needed for prompt generation)
    class _DummyProvider:
        pass
    class _DummyContext:
        def get_history(self): return []

    agent = agent_cls.__new__(agent_cls)
    agent._schema_name = schema_name

    system_text = agent.get_system_prompt()

    return ChatPromptTemplate.from_messages([
        ("system", system_text),
        ("placeholder", "{messages}"),
    ])
```

**关键设计**：
- **零重复**：直接复用现有 agent 的 `get_system_prompt()` 方法
- 不需要单独维护两套 prompt
- `__new__` + 属性注入避免触发 `__init__`（不需要真实 provider/context）

---

### 8. `superset/ai/agent/langchain/guard.py`（新增，~60 行）

**职责**：检测连续重复的工具调用，防止模型陷入工具循环。

```python
"""Tool call repetition guard — prevents infinite tool loops."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolCallRepetitionGuard:
    """Detects consecutive repetitive tool calls.

    If the same tool is called consecutively more than *max_consecutive*
    times, returns True to signal that a correction should be injected.

    This guard operates at the runner's event translation layer — it
    observes tool_call events, not LLM tokens, so it catches loops
    that SafeguardCallbackHandler (token-level) cannot.
    """

    def __init__(self, max_consecutive: int = 3) -> None:
        self._history: list[str] = []
        self._max = max_consecutive

    def check(self, tool_name: str) -> bool:
        """Record a tool call. Returns True if repetition limit exceeded."""
        self._history.append(tool_name)
        if len(self._history) >= self._max:
            tail = self._history[-self._max:]
            if len(set(tail)) == 1:  # all same tool
                return True
        return False

    def reset(self) -> None:
        """Clear history (called at the start of each agent run)."""
        self._history.clear()
```

**集成方式**（在 `runner.py` 的 `_translate_event` 中）：

```python
# runner.py 中的使用
class LangChainAgentRunner:
    def __init__(self, ...):
        ...
        self._tool_guard = ToolCallRepetitionGuard(max_consecutive=3)

    def run(self, message):
        self._tool_guard.reset()
        ...

    def _translate_event(self, mode, chunk):
        ...
        # 在检测到 tool_call 时检查重复
        if is_tool_call:
            if self._tool_guard.check(tool_name):
                logger.warning(
                    "Tool '%s' called %d times consecutively, injecting correction",
                    tool_name, self._tool_guard._max,
                )
                yield AgentEvent(
                    type="error",
                    data={"message": f"Tool '{tool_name}' repeated too many times, skipping."},
                )
                return
            yield AgentEvent(type="tool_call", data={...})
```

**关键设计**：
- 与 `SafeguardCallbackHandler` 互补：callback 检测文本重复，guard 检测工具重复
- `max_consecutive=3`：与 Phase 6 实测中观察到的循环模式匹配（模型通常在 2-4 次后陷入重复）
- 不阻止工具执行（LangGraph 的 ToolNode 是自动的），而是通过 error 事件通知前端
- **第二道防线**：如果重复检测触发后模型仍然继续重复，`recursion_limit` 会在达到最大轮次时强制停止

---

### 9. `superset/ai/commands/chat.py`（改动，+20 行）

添加 `LegacyAgentRunner` 包装类，使旧路径与新路径接口一致。

```python
# 在现有文件底部添加

class LegacyAgentRunner:
    """Wraps the existing (pre-LangChain) agent instantiation logic.

    Provides the same ``run(message) -> Iterator[AgentEvent]`` interface
    as ``LangChainAgentRunner``, so ``runner.py`` can dispatch uniformly.
    """

    def __init__(
        self,
        agent_type: str,
        database_id: int,
        schema_name: str | None,
        user_id: int,
        session_id: str,
    ) -> None:
        self._agent_type = agent_type
        self._database_id = database_id
        self._schema_name = schema_name
        self._user_id = user_id
        self._session_id = session_id

    def run(self, message: str) -> Iterator[AgentEvent]:
        agent_cls = _AGENT_MAP.get(self._agent_type)
        if agent_cls is None:
            raise ValueError(f"Unknown agent type: {self._agent_type}")

        with override_user(self._user_id):
            provider = get_provider()
            context = ConversationContext(
                user_id=self._user_id,
                session_id=self._session_id,
            )
            agent = agent_cls(
                provider=provider,
                context=context,
                database_id=self._database_id,
                schema_name=self._schema_name,
            )
            yield from agent.run(message)
```

---

### 10. `superset/ai/tasks.py`（改动，~15 行）

Celery task 改用 `runner.py` 统一入口：

```python
# 修改 run_agent_task 函数
@celery_app.task(soft_time_limit=get_agent_timeout())
def run_agent_task(kwargs: dict[str, Any]) -> str:
    from superset.ai.agent.events import AgentEvent
    from superset.ai.runner import create_agent_runner
    from superset.ai.streaming.manager import AiStreamManager
    from superset.utils.core import override_user

    channel_id = kwargs["channel_id"]
    user_id = kwargs["user_id"]
    message = kwargs["message"]
    database_id = kwargs["database_id"]
    schema_name = kwargs.get("schema_name")
    agent_type = kwargs.get("agent_type", "nl2sql")
    session_id = kwargs.get("session_id", channel_id)

    stream = AiStreamManager()

    try:
        from superset.extensions import security_manager
        user = security_manager.get_user_by_id(user_id) if user_id else None
        with override_user(user):
            runner = create_agent_runner(
                agent_type=agent_type,
                database_id=database_id,
                schema_name=schema_name,
                user_id=user_id,
                session_id=session_id,
            )
            for event in runner.run(message):
                stream.publish_event(channel_id, event)
    except Exception as exc:
        logger.exception("AI agent task failed")
        stream.publish_event(
            channel_id,
            AgentEvent(type="error", data={"message": str(exc)}),
        )

    return channel_id
```

**变化**：
- 删除直接 import 具体 agent 类和 `_AGENT_MAP` 的代码
- 通过 `create_agent_runner()` 工厂获取 runner
- 其他（`AiStreamManager`、`override_user`、错误处理）完全不变

---

### 11. `superset/ai/config.py`（改动，+5 行）

新增 `use_langchain()` 配置读取：

```python
def use_langchain() -> bool:
    """Return True if LangChain agent path should be used."""
    return bool(get_ai_config("AI_AGENT_USE_LANGCHAIN", False))
```

---

### 12. `docker/pythonpath_dev/superset_config_docker.py`（改动，+3 行）

添加 Feature Flag 配置：

```python
FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "AI_AGENT": True,
    "AI_AGENT_NL2SQL": True,
    "AI_AGENT_CHART": True,
    "AI_AGENT_DEBUG": True,
    "AI_AGENT_DASHBOARD": True,
}

# 新增：LangChain 路径开关（默认关闭）
AI_AGENT_USE_LANGCHAIN = False
```

---

### 13. `requirements/` （改动）

新增 LangChain 依赖。根据实际使用的模块确定：

```
# requirements/additional.txt 或 requirements/ai.txt（新增）
langchain-core>=0.3.0
langchain-openai>=0.3.0
langgraph>=0.2.0
```

**不在 base requirements 中**：LangChain 是可选依赖，仅当 `AI_AGENT_USE_LANGCHAIN=True` 时才需要。
可以在 Docker image 中预装，但不会影响 `pip install apache-superset` 的核心依赖。

---

## 事件流对照

### 旧路径（Legacy）事件流

```
BaseAgent.run(message)
  ├─ text_chunk  {"content": "..."}     ← LLM streaming token
  ├─ text_chunk  {"content": "..."}
  ├─ tool_call   {"tool": "search_datasets", "args": {...}}
  ├─ tool_result {"tool": "search_datasets", "result": "..."}
  ├─ text_chunk  {"content": "..."}
  ├─ ...
  └─ done        {}
```

### 新路径（LangChain）事件流 — 完全一致

```
LangChainAgentRunner.run(message)
  ├─ text_chunk  {"content": "..."}     ← AIMessageChunk.content
  ├─ text_chunk  {"content": "..."}
  ├─ tool_call   {"tool": "search_datasets", "args": {...}}
  │                                      ← AIMessageChunk.tool_call_chunks
  ├─ tool_result {"tool": "search_datasets", "result": "..."}
  │                                      ← ToolMessage
  ├─ text_chunk  {"content": "..."}
  ├─ ...
  └─ done        {}
```

**前端完全不需要改动** — 事件格式和 SSE 轮询机制不变。

---

## 实施步骤

| 步骤 | 内容 | 文件 | 行数估算 |
|---|---|---|---|
| 1 | 添加 `use_langchain()` 到 config.py | `superset/ai/config.py` | +5 |
| 2 | 创建 `agent/langchain/` 目录 + `__init__.py` | 新建目录 | +1 |
| 3 | 实现 `llm.py` — GLMChatOpenAI + 单工具强制 + 工厂 | `agent/langchain/llm.py` | +90 |
| 4 | 实现 `memory.py` — Redis 适配器 | `agent/langchain/memory.py` | +70 |
| 5 | 实现 `tools.py` — BaseTool 适配器 | `agent/langchain/tools.py` | +60 |
| 6 | 实现 `callbacks.py` — 文本安全防护 | `agent/langchain/callbacks.py` | +80 |
| 7 | 实现 `prompts.py` — prompt 适配 | `agent/langchain/prompts.py` | +50 |
| 8 | 实现 `guard.py` — 工具调用重复检测 | `agent/langchain/guard.py` | +60 |
| 9 | 实现 `runner.py` — LangChain 执行器 | `agent/langchain/runner.py` | +130 |
| 10 | 实现 `runner.py` — Feature Flag 工厂 | `superset/ai/runner.py` | +60 |
| 11 | 添加 `LegacyAgentRunner` | `superset/ai/commands/chat.py` | +20 |
| 12 | 修改 `tasks.py` — 调用工厂 | `superset/ai/tasks.py` | ~15 |
| 13 | 添加依赖 | `requirements/` | +3 |
| 14 | 添加 Feature Flag 配置 | `superset_config_docker.py` | +3 |
| 15 | 单元测试 | `tests/unit_tests/ai/` | ~250 |

**总计**：新增 ~890 行，修改 ~40 行，0 行删除

---

## 测试方案

### 单元测试（不需要 LLM）

| 测试 | 验证内容 |
|---|---|
| `test_tool_adapter` | `BaseTool` → `StructuredTool` 转换正确，`args_schema` 匹配 |
| `test_memory_adapter` | Redis 键读写一致，空历史返回空列表 |
| `test_prompt_adapter` | 4 种 agent type 都能生成 ChatPromptTemplate |
| `test_safeguard_callback` | 超长输出和重复检测触发 StopIteration |
| `test_tool_repetition_guard` | 连续 3 次相同工具调用触发 `check() == True` |
| `test_runner_factory` | Feature Flag=False 返回 LegacyAgentRunner |
| `test_runner_factory_langchain` | Feature Flag=True 返回 LangChainAgentRunner |
| `test_glm_chat_openai` | reasoning_content 被正确捕获到 additional_kwargs |
| `test_single_tool_forcing` | `model_kwargs` 包含 `parallel_tool_calls: False` |

### 集成测试（需要 LLM）

| 测试场景 | 步骤 |
|---|---|
| NL2SQL 基础 | 发送 `"查看所有表"` → 返回 SQL |
| Chart 创建 | 发送 `"创建一个柱状图"` → text_chunk → tool_call → chart_created |
| Dashboard 创建 | 发送 `"创建仪表板"` → 多轮 tool_call → dashboard_created |
| Debug 修复 | 发送错误 SQL → 分析 → 修复 → 返回正确 SQL |

### 切换测试

1. 设置 `AI_AGENT_USE_LANGCHAIN=False` → 运行所有 4 种 agent → 全部正常
2. 设置 `AI_AGENT_USE_LANGCHAIN=True` → 运行所有 4 种 agent → 事件格式一致
3. 在 LangChain 路径中发送消息 → 切回 Legacy 路径 → 对话历史连续（共享 Redis 键）

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| LangChain 版本升级 breaking change | LangChain 生态更新频繁 | 锁定 `langchain-core>=0.3.0,<0.4.0` |
| `create_react_agent` 流式输出格式变化 | 事件翻译逻辑失效 | 抽象 `_translate_event` 方法，集中处理 |
| GLM-5.1 streaming 行为差异 | tool_call 解析错误 | `GLMChatOpenAI` 覆盖解析方法，与 Phase 6 修复逻辑一致 |
| 性能开销 | LangChain 多层 wrapper 增加延迟 | Feature Flag 允许随时回退到旧路径 |
| 依赖膨胀 | langchain + langgraph 引入大量子依赖 | 仅安装 `langchain-core` + `langchain-openai` + `langgraph`，不需要完整的 `langchain` |
| 模型忽略 `parallel_tool_calls=False` | 仍返回多个 tool_calls | 第二道防线：runner 层只执行第一个 tool_call，记录警告日志 |
| 工具重复检测误报 | 合理的重复调用被拦截 | `max_consecutive=3` 留出容错空间，且只记录 warning 不强制终止 |
| DashboardAgent 建图后不建仪表板 | ReAct 无法保证流程完整 | Phase 8 通过 StateGraph 强制顺序解决 |

---

## 回退方案

- `AI_AGENT_USE_LANGCHAIN = False` 即可完全回退
- 所有新代码在 `agent/langchain/` 目录下，不修改任何旧文件的核心逻辑
- `tasks.py` 的修改仅在调用入口处，旧路径的 import 仍保留
- Redis 键格式不变，历史数据完全兼容

---

## 未来扩展（Phase 7 不实施，仅记录）

### Phase 8 — DashboardAgent StateGraph

将 DashboardAgent 从 ReAct 改为强制顺序 StateGraph（`search → analyze → create_charts → create_dashboard`），每个节点是明确的有类型状态转换，模型只负责生成当前节点的参数。解决"建图后不建仪表板"的根本问题。

**Phase 7 风险补充**：

| 风险 | 说明 | Phase 8 修改建议 |
|---|---|---|
| 事件翻译层不能可靠拦截工具执行 | `create_react_agent`/ToolNode 可能已在 stream 事件前安排工具执行，`_translate_event` 更适合转发事件，不适合作为"只执行第一个工具"的控制点 | 在 StateGraph 节点内由代码显式调用工具，或自定义 ToolNode，在工具执行前拦截多工具调用 |
| 工具重复检测如果只在事件层告警，无法阻止副作用 | 重复 `create_chart` / `create_dashboard` 可能已经落库，事件层 `error` 只能通知前端，不能防止重复写入 | 将 `ToolCallRepetitionGuard` 放到工具执行前；对写操作节点增加幂等键、已创建资源列表和最大重试次数 |
| Dashboard 流程不能继续依赖自由 ReAct 推理 | 实测中模型会创建 chart 后直接结束，或重复调用 `create_dashboard`，即使 prompt 要求"必须创建 dashboard"也不稳定 | 用 StateGraph 固定节点顺序：`select_dataset` → `plan_charts` → `analyze_chart_data` → `create_charts` → `create_dashboard` → `finalize` |
| Dashboard slug 冲突会导致重复创建失败 | 多次测试使用相同标题时，`CreateDashboardCommand` 的 slug 唯一性校验会报错，模型重复重试也无法恢复 | `create_dashboard` 节点由代码生成唯一 slug；标题保留用户语义，slug 自动追加短 UUID 或时间戳 |
| Prompt 适配不应依赖 `__new__` 伪造 agent | Phase 7 的 `prompt_adapter()` 示例依赖 agent 内部字段，未来 prompt 若依赖 database/provider/context 会隐性失败 | 将 prompt 构建抽为纯函数，legacy agent 和 StateGraph 节点共用同一套 prompt builder |
| Memory 中的 tool message 信息不足 | 现有 `ConversationContext` 只保存 `role/content`，不能可靠恢复 LangChain `ToolMessage.tool_call_id` | StateGraph 不从历史恢复 tool messages；历史只保留 user/assistant，总线状态保存本次 run 的 typed state |
| `StopIteration` 中断 callback 不够稳 | LangChain callback 链里直接抛 `StopIteration` 可能被包装或破坏 stream 清理 | 使用自定义 `AgentStoppedError`，runner/graph 统一捕获并转为 `AgentEvent(error)` + `done` |
| LangChain 依赖安装位置不明确 | Feature Flag 打开后如果 Docker 镜像没安装 `langchain-core/langchain-openai/langgraph` 会直接 ImportError | 在 Phase 8 前明确依赖安装策略：开发镜像预装，生产镜像按 AI extra 安装，Feature Flag 默认关闭 |

**Phase 8 目标状态**：

1. 模型只生成当前节点所需参数，不直接决定全局流程是否继续。
2. 写操作由代码串行执行，资源 ID 写入 graph state，后续节点只能引用 state 中的 chart IDs。
3. `create_dashboard` 节点必须执行一次；失败时由代码决定是否更换 slug/参数重试。
4. 事件流保持与现有前端兼容：`text_chunk`、`tool_call`、`tool_result`、`error`、`done`。
5. DashboardAgent 的 LangGraph 路径单独 Feature Flag 控制，可独立于 Phase 7 ReAct LangChain 路径开启或回退。

- **LangSmith Tracing**：`SafeguardCallbackHandler` 继承 `BaseCallbackHandler`，天然支持 LangSmith 集成
- **多轮工具编排**：LangGraph 的 `StateGraph` 可以定义比 ReAct 更复杂的工具编排流程
- **Human-in-the-loop**：LangGraph 原生支持中断等待人工确认
- **多模型路由**：`init_chat_model()` 支持根据任务类型动态选择模型
