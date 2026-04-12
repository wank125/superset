# Phase 11：多轮对话与结果自动解读

> 生成日期：2026-04-12
> 依赖：Phase 8（StateGraph）、Phase 9（前端事件）、Phase 10（E2E 测试）

---

## 一、目标

| 目标 | 当前状态 | Phase 11 后 |
|------|---------|------------|
| NL2SQL 跨轮追问 | ❌ 每次独立，历史丢失 | ✅ 同一 session 内可持续追问修改 SQL |
| 上下文 SQL 记忆 | ❌ 历史只存 assistant 文本 | ✅ 上轮 SQL 写入对话历史，LLM 下轮可参考 |
| 数据结果解读 | ❌ 只返回图表，不解读 | ✅ 查询后自动生成一句话洞察 |
| 模糊请求处理 | ❌ 不知道表名就报错 | 🔜 Phase 12 处理 |

---

## 二、背景

### 当前多轮缺陷

**问题1：session_id 未绑定**

前端每次请求若不携带 `session_id`，`tasks.py` 会 fallback 到 `channel_id`（每次随机 UUID），导致 `ConversationContext` 每次使用不同 Redis key，历史无法串联：

```python
# tasks.py 当前
session_id = kwargs.get("session_id", channel_id)  # ← fallback 是新 UUID
```

**问题2：工具执行细节不进历史**

`base.py` 只在 ReAct 最终返回时存储 `assistant` 的文本回复，`execute_sql` 的 SQL 语句和执行结果均不进入历史。第二轮对话时 LLM 无法知道上轮执行了什么 SQL：

```python
# base.py 当前 — 只存最终文本
full_response = "".join(assistant_content_parts)
self._context.add_message("assistant", full_response)
```

**问题3：StateGraph 路径完全无历史**

`graph/runner.py` 中的 `initial_state` 每次从零开始构造，`ConversationContext` 完全未被使用。

---

## 三、详细设计

### 3.1 session_id 绑定（前端 + 后端）

#### 前端改动

在对话组件层面生成固定 `session_id`，整个对话窗口生命周期内复用：

```typescript
// 对话组件 mount 时生成
const [sessionId] = useState(() => crypto.randomUUID());

// 每次发送消息时携带
const response = await fetch('/api/v1/ai/chat/', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: userInput,
    database_id: databaseId,
    agent_type: agentType,
    session_id: sessionId,   // ← 每次带上，不改变
  }),
});
```

**session_id 生命周期**：
- 新建聊天窗口 → 生成新 UUID
- 同一窗口内所有消息 → 复用同一 UUID
- 用户点击"清除对话" → 前端发送 `DELETE /api/v1/ai/session/{session_id}` + 生成新 UUID

#### 后端改动

移除 `tasks.py` 中的 fallback，使 `session_id` 为必需参数：

```python
# tasks.py — 修改
session_id = kwargs.get("session_id")
if not session_id:
    # 兼容旧客户端：生成并记录警告
    session_id = channel_id
    logger.warning(
        "session_id not provided, using channel_id=%s; "
        "multi-turn context will not work across requests",
        channel_id,
    )
```

---

### 3.2 SQL 摘要写入历史（NL2SQL / ReAct 路径）

在 `base.py` 的 `run()` 末尾，把本轮执行的所有 SQL 语句追加到 assistant 消息里：

```python
# base.py — run() 内，在 tool_calls 执行阶段收集 SQL
sql_executed: list[str] = []  # 在循环外初始化

# ... 在 tool 执行循环内：
for tc in tool_calls_acc:
    result = self._tools[tc["name"]].run(tc["arguments"])
    # 记录 execute_sql 的语句
    if tc["name"] == "execute_sql":
        sql = tc["arguments"].get("sql", "").strip()
        if sql:
            sql_executed.append(sql)
    ...
```

```python
# base.py — run() 末尾，存储时追加 SQL 摘要
full_response = "".join(assistant_content_parts)
if sql_executed:
    sql_block = "\n\n---\n[本轮执行的SQL]\n" + "\n\n".join(
        f"```sql\n{s}\n```" for s in sql_executed
    )
    full_response += sql_block

self._context.add_message("assistant", full_response)
```

**效果**：下一轮 `get_history()` 返回的 assistant 消息包含上轮 SQL，LLM 能直接参考并修改。

---

### 3.3 结果自然语言解读（StateGraph 路径）

在 `analyze_result` 节点末尾新增一次 LLM 调用，生成一句话洞察：

#### state.py 变更

```python
# state.py — ResultSummary 新增字段
class ResultSummary(TypedDict):
    ...
    insight: str | None  # LLM 生成的一句话洞察，可为 None
```

#### llm_helpers.py 新增

```python
# graph/llm_helpers.py — 新增 llm_call_text
def llm_call_text(prompt: str, max_tokens: int = 200) -> str:
    """Call LLM and return plain text (no JSON parsing)."""
    provider = get_provider()
    chunks = list(provider.chat_stream(
        [LLMMessage(role="user", content=prompt)],
        tools=None,
        max_tokens=max_tokens,
    ))
    return "".join(c.content or "" for c in chunks).strip()
```

#### nodes_child.py 变更

```python
# analyze_result 节点末尾
INSIGHT_PROMPT = """\
基于以下数据特征，用{lang}写一句话说明最关键的发现（20字以内）：
  行数：{row_count}
  数值列：{numeric_cols}
  时间列：{datetime_col}
  低基数维度：{low_card_cols}
  数据样本（前3行）：{sample}

只输出一句话，不要解释，不要分点。
"""

# analyze_result 末尾追加
insight: str | None = None
if row_count > 0 and numeric_cols:
    try:
        sample_rows = rows[:3] if rows else []
        lang = state.get("chart_intent", {}).get("user_language", "zh") or "zh"
        insight_prompt = INSIGHT_PROMPT.format(
            lang="中文" if lang == "zh" else "English",
            row_count=row_count,
            numeric_cols=numeric_cols[:3],
            datetime_col=datetime_col,
            low_card_cols=low_card_cols[:3],
            sample=sample_rows,
        )
        insight = llm_call_text(insight_prompt)
    except Exception as exc:
        logger.warning("analyze_result insight generation failed: %s", exc)
        insight = None

summary["insight"] = insight
```

#### runner.py 变更

在 `_emit_node_events` 函数中，当节点为 `analyze_result` 且有 `insight` 时，发送新事件：

```python
# runner.py — _emit_node_events 内
if node_name == "analyze_result":
    insight = node_output.get("query_result_summary", {}).get("insight")
    if insight:
        yield AgentEvent(
            type="insight_generated",
            data={"insight": insight},
        )
```

#### events.py 变更

```python
# agent/events.py — 新增事件类型
AgentEventType = Literal[
    ...
    "insight_generated",  # Phase 11 新增：数据洞察文字
]
```

---

## 四、改动文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `superset/ai/tasks.py` | 修改 | session_id fallback 改为 warning |
| `superset/ai/agent/base.py` | 修改 | 收集 sql_executed 列表，存入历史 |
| `superset/ai/graph/state.py` | 修改 | ResultSummary 新增 `insight` 字段 |
| `superset/ai/graph/llm_helpers.py` | 修改 | 新增 `llm_call_text()` |
| `superset/ai/graph/nodes_child.py` | 修改 | `analyze_result` 末尾调用 LLM 生成洞察，输出 `insight` |
| `superset/ai/graph/runner.py` | 修改 | `_emit_node_events` 新增 `insight_generated` 事件 |
| `superset/ai/agent/events.py` | 修改 | 新增 `insight_generated` 事件类型 |
| 前端对话组件 | 修改 | 生成并持久化 `session_id`，每次请求携带 |
| 前端事件处理 | 修改 | 订阅 `insight_generated` 事件，展示洞察文字 |

---

## 五、数据流

### NL2SQL 多轮对话流

```
请求 1（session_id=abc）：
  user: "统计各地区销售额"
    ↓ ReAct 执行 execute_sql
    ↓ SQL: SELECT region, SUM(amount) FROM orders GROUP BY region
    ↓ 结果：华北 320万 / 华南 280万 / 华东 510万
  ConversationContext.add_message("assistant",
    "各地区销售额如下...\n\n---\n[本轮执行的SQL]\n```sql\nSELECT region...\n```")
  Redis: ai:ctx:1:abc = [{user: "统计..."}, {assistant: "...SQL..."}]

请求 2（session_id=abc）：
  user: "华东最高，帮我按月份 breakdown"
  messages = [system, user#1, assistant#1（含SQL）, user#2]
    ↓ LLM 看到上轮 SQL "SELECT region..."
    ↓ 生成：SELECT month, SUM(amount) FROM orders
            WHERE region='华东' GROUP BY month
```

### StateGraph 洞察事件流

```
plan_query → validate_sql → execute_query → analyze_result
                                                  ↓
                                          [Code] suitability_flags 推导
                                          [LLM]  一句话洞察生成（< 1s）
                                                  ↓
                                          insight_generated 事件 → 前端
                                                  ↓
                                          select_chart → ...
```

---

## 六、测试用例

### 单元测试

**文件**：`tests/unit_tests/ai/test_multi_turn.py`

```python
class TestSqlSummaryInHistory:
    def test_sql_appended_to_assistant_message(self):
        """execute_sql 工具执行后，sql 被追加到 assistant 历史"""
        ...

    def test_second_turn_receives_previous_sql(self):
        """第二轮请求时，messages 中包含上轮的 SQL 摘要"""
        ...


class TestInsightGeneration:
    @patch("superset.ai.graph.nodes_child.llm_call_text")
    def test_insight_emitted_when_data_exists(self, mock_llm):
        mock_llm.return_value = "华东大区销售额最高，占比 38%"
        state = {"query_result_raw": "region|amount\n---\nEast|510"}
        result = analyze_result(state)
        assert result.update["query_result_summary"]["insight"] is not None

    @patch("superset.ai.graph.nodes_child.llm_call_text")
    def test_insight_none_on_empty_data(self, mock_llm):
        state = {"query_result_raw": ""}
        result = analyze_result(state)
        assert result.update["query_result_summary"]["insight"] is None

    @patch("superset.ai.graph.nodes_child.llm_call_text")
    def test_insight_failure_is_swallowed(self, mock_llm):
        """LLM 生成洞察失败不应影响主流程"""
        mock_llm.side_effect = Exception("timeout")
        state = {"query_result_raw": "col|val\n---\n1|2"}
        result = analyze_result(state)
        # 主流程继续，insight 为 None
        assert result.goto == "select_chart"
        assert result.update["query_result_summary"]["insight"] is None
```

### E2E 验证场景

**测试 1：NL2SQL 多轮追问**

```
1. POST /chat/ {message: "查各地区销售额", session_id: "test-001"}
   验证：events 包含 done，text_chunk 含 SQL

2. POST /chat/ {message: "华东的按月份分组", session_id: "test-001"}
   验证：生成 SQL 包含 WHERE region='华东'，不需要重新调用 get_schema
```

**测试 2：洞察事件**

```
1. POST /chat/ {message: "各性别出生人数", agent_type: "chart", session_id: "test-002"}
   验证：events 包含 insight_generated，insight 字段为非空字符串
```

---

## 七、前端 UI 设计

### 洞察展示

`insight_generated` 事件触发时，在图表卡片上方展示：

```
┌─────────────────────────────────────────┐
│ 💡 华东大区销售额最高，占全国总额 38%       │
└─────────────────────────────────────────┘
[图表]
```

### 多轮对话气泡

上轮的 SQL 在 assistant 气泡中以折叠代码块展示，默认收起：

```
AI: 各地区销售额如下：华北 320万…
    ▶ 执行的 SQL（点击展开）
```

---

## 八、风险与限制

| 风险 | 说明 | 缓解 |
|------|------|------|
| 洞察 LLM 调用增加延迟 | 每个图表多 1 次 LLM 调用（~1s） | 洞察生成异步化，失败不阻断主流程 |
| SQL 历史膨胀 context | 多轮后 context 包含大量 SQL 文本 | `get_max_context_rounds` 默认 20 轮，SQL 截断至 500 字符 |
| StateGraph 路径暂不支持真正多轮 | Phase 11 只对 NL2SQL 实现完整多轮，chart/dashboard 路径见 Phase 14 | 文档说明当前限制 |
