# Phase 17：图表生成澄清机制（Clarification Loop）

> 生成日期：2026-04-12
> 前置依赖：Phase 8（StateGraph）、Phase 16（意图路由）

---

## 一、问题背景

### 当前缺陷

用户说"**画一个折线图**"时，系统缺少关键信息：

| 缺失信息 | 当前行为 | 理想行为 |
|---------|---------|---------|
| 未指定数据集（多个候选）| 自动选评分最高候选 | 询问用户选哪个 |
| 未指定时间范围 | LLM 推断/不加过滤 | 询问"最近一周还是一个月？" |
| 未指定维度/指标 | LLM 猜测 | 询问"按地区还是按品类分组？" |
| 数据集评分全为 0 | 返回错误，任务结束 | 告知并引导用户输入正确表名 |

### 当前代码痕迹

```python
# nodes_parent.py:338
# Auto-select the best match (interrupt requires checkpointer
# which isn't available in the stream-only run_graph path)
```

Phase 8 因无 checkpointer 而放弃了 `interrupt()` 方案。  
**Phase 8 checkpointer 已在 Phase 8 补全**，技术阻塞已消除，但 Celery 长时阻塞问题仍需规避。

---

## 二、方案选型

### 方案 A：前端多轮（本 Phase 实现） ✅

图执行到模糊处，发出 `clarify` 事件后**正常结束**任务。前端渲染选项，用户选择后**发起新的完整请求**（携带补充信息）。

```
Turn 1: "画一个折线图"
  → StateGraph 执行到 select_dataset
  → 发 clarify 事件（附候选列表）
  → done 事件，Task 结束

前端显示选项卡: [birth_names] [sales_fact] [order_detail]

Turn 2: 用户点击 "sales_fact"
  → 前端自动拼: "画一个折线图，使用 sales_fact 数据集"
  → 新 Task，正常执行
```

**优点**：无架构改动，Celery Task 不阻塞，实现简单  
**缺点**：每次澄清需要一次完整 Task 启动（LLM 调用 × N 个节点）

### 方案 B：interrupt() + Resume（Phase 17+ 长期目标）

```
Task 暂停 → checkpointer 保存 State → 发 awaiting_input 事件
→ 新 API: POST /api/v1/ai/resume/ { channel_id, answer }
→ 图从断点继续
```

**工作量约 2 周，留作后续迭代。**

---

## 三、触发时机设计

澄清在以下 3 个场景触发：

### 场景 1：数据集模糊（多候选，最高分相同）

```
"画一个折线图"
  → search_dataset 返回 [birth_names, sales_fact, orders]
  → select_dataset 评分: 全为 0（无关键词匹配）
  → 触发 clarify
```

**当前代码行为**：score=0 直接返回错误 `goto="__end__"`  
**Phase 17 改为**：发 clarify 事件，等用户选择

### 场景 2：数据集不存在

```
"用 abc_table 画图"
  → search_dataset 返回 []
  → 触发 clarify（列出可用数据集）
```

### 场景 3：信息严重不足（LLM 判断，较复杂，可选实现）

```
"画一个图"（无任何限定）
  → parse_request 后 goal 中无 dataset_hint, metrics, dimensions
  → clarify: "请描述你想分析什么数据？"
```

**Phase 17 优先实现场景 1 和 2，场景 3 列为可选。**

---

## 四、详细实现

### 4.1 新增事件类型

```python
# superset/ai/agent/events.py

AgentEventType = Literal[
    "text_chunk",
    "tool_call",
    "tool_result",
    "thinking",
    "sql_generated",
    "chart_created",
    "dashboard_created",
    "data_analyzed",
    "insight_generated",
    "intent_routed",
    "clarify",            # ← Phase 17 新增
    "done",
    "error",
]
```

`clarify` 事件数据格式：

```python
{
    "type": "clarify",
    "data": {
        "question": "请选择要分析的数据集：",
        "clarify_type": "dataset_selection",  # dataset_selection | general
        "options": [                           # 可选列表（None 时为开放输入）
            {"label": "birth_names", "value": "birth_names", "description": "出生统计数据"},
            {"label": "sales_fact",  "value": "sales_fact",  "description": "销售事实表"},
        ],
        "context": {                           # 供下一轮请求补全的上下文
            "original_request": "画一个折线图",
            "answer_prefix": "画一个折线图，使用数据集 {value}",
        }
    }
}
```

---

### 4.2 State 扩展

```python
# graph/state.py — DashboardState 新增字段

class DashboardState(TypedDict, total=False):
    # ... 现有字段 ...

    # Phase 17: 澄清状态
    clarify_question: str | None       # 触发澄清的问题文本
    clarify_type: str | None           # "dataset_selection" | "general"
    clarify_options: list[dict] | None # 候选项列表
    answer_prefix: str | None          # 前端下一轮请求的模板
```

---

### 4.3 修改 `select_dataset` 节点

```python
# nodes_parent.py — select_dataset 修改

def select_dataset(
    state: DashboardState,
) -> Command[Literal["read_schema", "clarify_user", "__end__"]]:
    candidates = state.get("dataset_candidates") or []

    # 场景2：无候选
    if not candidates:
        target = (state.get("goal") or {}).get("target_table", "")
        all_datasets = _get_all_accessible_datasets(state["database_id"])

        return Command(
            update={
                "clarify_question": f"未找到数据集「{target}」，请从以下数据集中选择：",
                "clarify_type": "dataset_selection",
                "clarify_options": [
                    {"label": d, "value": d} for d in all_datasets[:10]
                ],
                "answer_prefix": f"{state['request']}，使用数据集 {{value}}",
            },
            goto="clarify_user",
        )

    # 单候选 → 直接选（不变）
    if len(candidates) == 1:
        # ... 原有逻辑 ...
        pass

    # 多候选 → 评分
    scored = _score_candidates(candidates, state)
    best_score, best = scored[0]

    # 场景1：最高分为0，无法自信选择 → 澄清
    if best_score == 0:
        options = [
            {
                "label": c.get("table_name", str(c)),
                "value": c.get("table_name", str(c)),
                "description": c.get("description", ""),
            }
            for c in candidates[:8]
        ]
        return Command(
            update={
                "clarify_question": "找到多个可能的数据集，请选择：",
                "clarify_type": "dataset_selection",
                "clarify_options": options,
                "answer_prefix": f"{state['request']}，使用数据集 {{value}}",
            },
            goto="clarify_user",
        )

    # 有信心的选择 → 直接选（score > 0，原有逻辑）
    # ... 原有 auto-select 逻辑 ...
```

---

### 4.4 新增 `clarify_user` 节点

```python
# nodes_parent.py — 新增节点

def clarify_user(
    state: DashboardState,
) -> Command[Literal["__end__"]]:
    """Publish a clarify event and end the graph gracefully.

    The frontend will render the options and re-submit
    a new request with the user's answer.
    """
    from superset.ai.streaming.manager import AiStreamManager
    from superset.ai.agent.events import AgentEvent

    channel_id = state.get("channel_id")
    if channel_id:
        stream = AiStreamManager()
        stream.publish_event(
            channel_id,
            AgentEvent(
                type="clarify",
                data={
                    "question": state.get("clarify_question", "请补充信息："),
                    "clarify_type": state.get("clarify_type", "general"),
                    "options": state.get("clarify_options"),
                    "context": {
                        "original_request": state.get("request", ""),
                        "answer_prefix": state.get("answer_prefix", ""),
                    },
                },
            ),
        )

    # 保存澄清状态到对话历史（供下一轮参考）
    return Command(
        update={
            "last_error": None,  # 不算错误，正常结束
        },
        goto="__end__",
    )
```

---

### 4.5 builder.py 注册新节点

```python
# builder.py — build_chart_graph() 和 build_dashboard_graph() 中新增

b.add_node("clarify_user", parent.clarify_user)   # ← 新增

# 边由 Command(goto="clarify_user") 动态控制，无需显式 add_edge
```

---

### 4.6 runner.py 处理 clarify 结束

```python
# runner.py — run_graph() 中添加 clarify 状态识别

for node_name, node_output in state_update.items():
    # 新增：检测 clarify 节点结束
    if node_name == "clarify_user":
        # clarify 事件已在节点内部发送，这里只记录状态
        clarify_issued = True
        continue
    # ...（原有逻辑）

# done 事件中标注是否为 clarify 结束
summary = ""
if clarify_issued:
    summary = f"[awaiting_clarification] {state.get('clarify_question', '')}"
else:
    summary = _build_summary(...)

yield AgentEvent(type="done", data={"summary": summary})
```

---

### 4.7 context.py：保存澄清轮次

`clarify` 后图正常结束，`tasks.py` 会把 summary 写入 Redis 历史：

```python
# tasks.py — 现有代码（无需修改）
if assistant_summary:
    ctx.add_message("assistant", assistant_summary)
# "[awaiting_clarification] 请选择数据集" 会写入历史
# 下一轮 LLM 可以看到上一轮是一个澄清请求
```

---

## 五、前端处理

### 5.1 监听 clarify 事件

```typescript
// 事件处理（伪代码）
case "clarify":
  setClarifyState({
    question: event.data.question,
    options: event.data.options,
    answerPrefix: event.data.context.answer_prefix,
    isOpen: true,
  });
  break;
```

### 5.2 渲染澄清 UI

```
AI: 找到多个可能的数据集，请选择：
┌─────────────────────────────────┐
│ ○ birth_names   出生统计数据     │
│ ○ sales_fact    销售事实表       │
│ ○ order_detail  订单明细         │
└─────────────────────────────────┘
[确认]
```

### 5.3 用户选择后自动发起新请求

```typescript
const handleClarifyAnswer = (value: string) => {
  const nextMessage = clarifyState.answerPrefix.replace("{value}", value);
  // nextMessage = "画一个折线图，使用数据集 sales_fact"
  sendMessage(nextMessage);  // 发起新请求
};
```

---

## 六、改动文件清单

| 文件 | 类型 | 改动内容 |
|------|------|---------|
| `agent/events.py` | 修改 | 新增 `"clarify"` 事件类型 |
| `graph/state.py` | 修改 | DashboardState 新增 4 个 clarify 字段 |
| `graph/nodes_parent.py` | 修改 | `select_dataset` 改路由 + 新增 `clarify_user` 节点 |
| `graph/builder.py` | 修改 | 注册 `clarify_user` 节点（2行）|
| `graph/runner.py` | 修改 | 识别 clarify 结束，summary 标注 |
| 前端对话组件 | 修改 | `clarify` 事件处理 + 选项渲染 + 自动构建下一请求 |

**后端工作量**：约 2-3 天  
**前端工作量**：约 1-2 天  
**总计**：约 1 周

---

## 七、测试用例

### 后端单元测试

```python
# tests/unit_tests/ai/test_clarify_flow.py

class TestSelectDatasetClarify:

    def test_zero_score_triggers_clarify(self, mock_state):
        """多候选全为零分时，应路由到 clarify_user"""
        state = {
            "dataset_candidates": [
                {"table_name": "birth_names"},
                {"table_name": "sales_fact"},
            ],
            "goal": {"target_table": "xyz"},  # 没有任何匹配
            "request": "画一个折线图",
            "database_id": 1,
            "channel_id": "ch1",
        }
        cmd = select_dataset(state)
        assert cmd.goto == "clarify_user"
        assert cmd.update["clarify_type"] == "dataset_selection"
        assert len(cmd.update["clarify_options"]) == 2

    def test_empty_candidates_triggers_clarify(self, mock_state):
        """无候选时，应路由到 clarify_user 并列出全部可用数据集"""
        state = {
            "dataset_candidates": [],
            "goal": {"target_table": "not_exist"},
            "request": "画一个折线图",
            "database_id": 1,
            "channel_id": "ch1",
        }
        cmd = select_dataset(state)
        assert cmd.goto == "clarify_user"

    def test_high_score_skips_clarify(self, mock_state):
        """精确匹配时，应直接路由到 read_schema"""
        state = {
            "dataset_candidates": [
                {"table_name": "birth_names", "datasource_id": 1},
            ],
            "goal": {"target_table": "birth_names"},
            "database_id": 1,
            "channel_id": "ch1",
        }
        cmd = select_dataset(state)
        assert cmd.goto == "read_schema"

    def test_clarify_user_publishes_event(self, mock_stream):
        """clarify_user 节点应发送 clarify 事件"""
        state = {
            "clarify_question": "请选择数据集",
            "clarify_type": "dataset_selection",
            "clarify_options": [{"label": "a", "value": "a"}],
            "answer_prefix": "画图，使用数据集 {value}",
            "request": "画一个折线图",
            "channel_id": "ch1",
        }
        clarify_user(state)
        event = mock_stream.published_events[0]
        assert event.type == "clarify"
        assert event.data["options"][0]["value"] == "a"
```

### E2E 场景验证

| 输入 | 预期流程 | 预期结果 |
|------|---------|---------|
| "画一个折线图"（库里有3个数据集，无关键词）| 正常执行 → clarify 事件 → done | 前端出现数据集选项卡 |
| "画一个折线图，用 birth_names"（精确匹配）| 正常执行，无 clarify | 图表创建成功 |
| "画一个折线图，用 不存在的表"（空结果）| clarify 事件 + 可用列表 | 前端提示并列出可用数据集 |
| 选择数据集后 → 新请求"画一个折线图，使用数据集 birth_names"| 精确匹配，无 clarify | 图表创建成功 |

---

## 八、未来扩展（Phase 17+）

### 方案 B：interrupt() + Resume

在澄清点数量多、场景复杂（不只是数据集）时，方案 A 会产生大量重复的 LLM 节点调用。届时可升级到 interrupt() 模式：

```python
# nodes_parent.py — 未来版本
from langgraph.types import interrupt

def select_dataset(state):
    if best_score == 0:
        # 暂停图执行，等用户回复
        answer = interrupt({
            "question": "请选择数据集",
            "options": options,
        })
        # answer 是用户回复的内容，图从这里继续
        selected = {"table_name": answer}
        return Command(update={"selected_dataset": selected}, goto="read_schema")
```

需新增：
- `POST /api/v1/ai/resume/` API
- 前端 `awaiting_input` 事件处理
- tasks.py resume 路径
