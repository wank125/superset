# Phase 14：Chart / Dashboard 多轮修改

> 生成日期：2026-04-12
> 依赖：Phase 11（多轮对话基础）、Phase 13（Metric Catalog）

---

## 一、目标

| 目标 | 当前状态 | Phase 14 后 |
|------|---------|------------|
| Chart 追问修改 | ❌ 每次重新创建 | ✅ "把这个图改成折线图" 直接改已有图表 |
| Dashboard 追问 | ❌ 无跨请求历史 | ✅ "第 2 个图加一个过滤条件" 定位并修改 |
| StateGraph 历史 | ❌ 每次全新 initial_state | ✅ 读取上一轮创建的 chart_id/dashboard_id |
| 图表 ID 引用 | ❌ 无图表引用机制 | ✅ 前端传入 `reference_chart_id`，Agent 修改而非新建 |

---

## 二、背景

### 当前 StateGraph 无状态问题

```python
# graph/runner.py 当前 — 每次全新状态
initial_state = {
    "request": message,
    "request_id": uuid.uuid4(),   # 新 request_id
    ...
    # 没有任何上一轮信息
}
```

前端用户：
```
轮1: "帮我做一个销售趋势折线图"
AI: [创建了 chart_id=847，viz_type=echarts_timeseries_line]

轮2: "改成柱状图"
AI 当前行为: 重新执行完整管线，创建 chart_id=848（新图表）
用户期望:    修改 chart_id=847 的 viz_type
```

---

## 三、设计方案

### 总体策略

在 `parse_request` 之前增加一个**意图分类**节点 `classify_intent`，判断本次请求是：

- `new` — 新建图表/dashboard（走现有完整管线）
- `modify` — 修改已有图表（走简化修改路径）
- `reference` — 引用已有图表进行分析（暂 Phase 15，本期不实现）

```
用户请求
    ↓
[P0] classify_intent
    ├── new    → parse_request → search_dataset → ... → create_chart
    └── modify → load_existing → modify_chart_params → update_chart
```

---

### 3.1 跨请求状态管理

#### 写回 ConversationContext

`run_graph()` 执行完成后，将关键结果写入 `ConversationContext`：

```python
# graph/runner.py — run_graph() 末尾
from superset.ai.agent.context import ConversationContext

ctx = ConversationContext(user_id=user_id, session_id=session_id)

# 收集本轮结果
created_charts = final_state.get("created_charts", [])
created_chart = final_state.get("created_chart")
if created_chart:
    created_charts = [created_chart]

created_dashboard = final_state.get("created_dashboard")

# 保存用户消息
ctx.add_message("user", message)

# 保存 assistant 摘要
summary_parts = [f"已完成：{state.get('goal', {}).get('task', '分析')}"]
for chart in created_charts:
    chart_id = chart.get("chart_id")
    slice_name = chart.get("slice_name", "")
    viz_type = chart.get("viz_type", "")
    summary_parts.append(f"  图表 #{chart_id}：{slice_name}（{viz_type}）")
if created_dashboard:
    summary_parts.append(
        f"  仪表板 #{created_dashboard.get('dashboard_id')}：{created_dashboard.get('title')}"
    )

ctx.add_message("assistant", "\n".join(summary_parts))

# 存储结构化数据（tool_summary 类型）
ctx.add_tool_summary("graph_result", json.dumps({
    "created_charts": created_charts,
    "created_dashboard": created_dashboard,
    "request_id": request_id,
}))
```

#### 读取 ConversationContext

`run_graph()` 执行前读取历史：

```python
# graph/runner.py — run_graph() 开始
ctx = ConversationContext(user_id=user_id, session_id=session_id)
history = ctx.get_history()

# 从历史中提取上一轮创建的图表/仪表板
previous_charts = _extract_previous_charts(history)
previous_dashboard = _extract_previous_dashboard(history)

initial_state = {
    "request": message,
    ...
    "previous_charts": previous_charts,       # 新增
    "previous_dashboard": previous_dashboard, # 新增
    "history_summary": _compress_history(history, max_rounds=3),  # 新增
}
```

```python
# graph/runner.py — 辅助函数
def _extract_previous_charts(history: list[dict]) -> list[dict]:
    """从 tool_summary 条目中提取上一轮创建的图表列表."""
    for entry in reversed(history):
        if entry.get("role") == "tool_summary" and entry.get("tool") == "graph_result":
            try:
                data = json.loads(entry["content"])
                return data.get("created_charts", [])
            except (json.JSONDecodeError, KeyError):
                pass
    return []
```

---

### 3.2 ConversationContext 扩展

`add_tool_summary()` 方法（Phase 11 规划，Phase 14 实现）：

```python
# agent/context.py — 扩展 add_tool_summary
def add_tool_summary(self, tool_name: str, content: str) -> None:
    """Record structured tool result for next-turn context.

    Stored with role='tool_summary' in history.
    Excluded from LLM messages but available for state reconstruction.
    """
    history = self.get_history()
    history.append({
        "role": "tool_summary",
        "tool": tool_name,
        "content": content,
    })
    # tool_summary 不计入轮数（只有 user/assistant 对计入）
    max_messages = self._max_rounds * 2
    user_assistant_count = sum(
        1 for h in history if h["role"] in ("user", "assistant")
    )
    if user_assistant_count > max_messages:
        # 裁剪时保留 tool_summary 中最近的一条
        ...
    self._cache().set(self._key, json.dumps(history), timeout=_CONTEXT_TTL)
```

---

### 3.3 新增节点：classify_intent

在父图 `builder.py` 中，在 `parse_request` 之前插入 `classify_intent`：

```python
# graph/nodes_parent.py — 新增节点

CLASSIFY_INTENT_PROMPT = """\
Classify this request as 'new' or 'modify'.

'modify' examples:
  - "改成折线图" / "change to line chart"
  - "加一个过滤" / "add filter"
  - "把第一个图改一下" / "update the first chart"
  - "那个饼图换成柱状图"

'new' examples:
  - "帮我做一个销售趋势图"
  - "创建 birth_names 的分析"

Respond ONLY: {{"intent": "new"}} or {{"intent": "modify"}}

Request: {request}
Previous context: {context_summary}
"""

def classify_intent(
    state: DashboardState,
) -> Command[Literal["parse_request", "load_existing_chart"]]:
    has_previous = bool(state.get("previous_charts") or state.get("previous_dashboard"))

    # 快速规则判断（避免 LLM 开销）
    request = state.get("request", "").lower()
    modify_keywords = ["改", "换", "修改", "更新", "change", "update", "modify", "switch"]
    if not has_previous or not any(kw in request for kw in modify_keywords):
        return Command(goto="parse_request")

    # 有上一轮历史且请求含修改关键词 → 调 LLM 确认
    try:
        result = llm_call_json(CLASSIFY_INTENT_PROMPT.format(
            request=state["request"][:300],
            context_summary=state.get("history_summary", "")[:200],
        ))
        intent = result.get("intent", "new")
    except (ValueError, Exception):
        intent = "new"  # 失败时 fallback 到新建

    if intent == "modify":
        return Command(goto="load_existing_chart")
    return Command(goto="parse_request")
```

---

### 3.4 新增节点：load_existing_chart

```python
# graph/nodes_parent.py — 新增节点

def load_existing_chart(
    state: DashboardState,
) -> Command[Literal["apply_chart_modification", "__end__"]]:
    """Load the most recent chart from context for modification."""
    previous_charts = state.get("previous_charts", [])

    if not previous_charts:
        return Command(
            update={"last_error": {"type": "no_previous_chart",
                                   "message": "没有找到可修改的图表"}},
            goto="parse_request",  # 降级到新建
        )

    # 默认取最近的图表（或前端指定的 reference_chart_id）
    reference_id = state.get("reference_chart_id")
    if reference_id:
        target = next(
            (c for c in previous_charts if c.get("chart_id") == reference_id),
            previous_charts[-1],
        )
    else:
        target = previous_charts[-1]

    # 从 Superset DB 加载当前 form_data
    from superset.models.slice import Slice
    from superset import db

    slice_obj = db.session.query(Slice).get(target["chart_id"])
    if not slice_obj:
        return Command(
            update={"last_error": {"type": "chart_not_found",
                                   "message": f"图表 #{target['chart_id']} 不存在"}},
            goto="parse_request",
        )

    return Command(
        update={
            "existing_chart": {
                "chart_id": slice_obj.id,
                "slice_name": slice_obj.slice_name,
                "viz_type": slice_obj.viz_type,
                "form_data": json.loads(slice_obj.params or "{}"),
                "datasource_id": slice_obj.datasource_id,
            },
        },
        goto="apply_chart_modification",
    )
```

---

### 3.5 新增节点：apply_chart_modification

```python
# graph/nodes_parent.py — 新增节点

MODIFY_PROMPT = """\
The user wants to modify an existing chart. Return ONLY valid JSON.

Existing chart:
  viz_type: {viz_type}
  slice_name: {slice_name}
  current params summary: {form_data_summary}

User request: {request}

Return the modifications to apply:
{{
  "viz_type": "<new viz_type or same>",
  "slice_name": "<new name or same>",
  "param_changes": {{
    "<key>": "<value>",   // only changed params
    ...
  }}
}}
"""

def apply_chart_modification(
    state: DashboardState,
) -> Command[Literal["update_chart", "__end__"]]:
    existing = state.get("existing_chart", {})
    form_data = existing.get("form_data", {})

    # 摘要 form_data 用于 prompt（避免 token 过多）
    form_summary = {
        k: form_data[k]
        for k in ("viz_type", "metrics", "groupby", "time_column", "filters")
        if k in form_data
    }

    prompt = MODIFY_PROMPT.format(
        viz_type=existing.get("viz_type"),
        slice_name=existing.get("slice_name"),
        form_data_summary=json.dumps(form_summary)[:500],
        request=state["request"][:300],
    )

    try:
        changes = llm_call_json(prompt)
    except ValueError as exc:
        return Command(
            update={"last_error": {"type": "modify_parse_error", "message": str(exc)}},
            goto="__end__",
        )

    # 合并变更到 form_data
    new_form_data = {**form_data}
    new_form_data.update(changes.get("param_changes", {}))
    new_viz_type = changes.get("viz_type", existing.get("viz_type"))
    new_slice_name = changes.get("slice_name", existing.get("slice_name"))

    return Command(
        update={
            "modification": {
                "chart_id": existing["chart_id"],
                "new_viz_type": new_viz_type,
                "new_slice_name": new_slice_name,
                "new_form_data": new_form_data,
            },
        },
        goto="update_chart",
    )
```

---

### 3.6 新增节点：update_chart

```python
# graph/nodes_parent.py — 新增节点

def update_chart(
    state: DashboardState,
) -> Command[Literal["__end__"]]:
    """Apply modifications to existing chart in Superset."""
    mod = state.get("modification", {})
    chart_id = mod.get("chart_id")

    from superset import db
    from superset.models.slice import Slice

    slice_obj = db.session.query(Slice).get(chart_id)
    if not slice_obj:
        return Command(
            update={"last_error": {"type": "chart_not_found"}},
            goto="__end__",
        )

    slice_obj.viz_type = mod.get("new_viz_type", slice_obj.viz_type)
    slice_obj.slice_name = mod.get("new_slice_name", slice_obj.slice_name)
    slice_obj.params = json.dumps(mod.get("new_form_data", {}))
    db.session.commit()

    updated_chart = {
        "chart_id": slice_obj.id,
        "slice_name": slice_obj.slice_name,
        "viz_type": slice_obj.viz_type,
        "explore_url": f"/explore/?slice_id={slice_obj.id}",
        "message": f"已更新图表 #{chart_id}",
        "action": "updated",   # 区别于 "created"
    }

    return Command(
        update={"created_chart": updated_chart},
        goto="__end__",
    )
```

---

### 3.7 StateGraph 构建变更

```python
# graph/builder.py — build_chart_graph() 扩展

def build_chart_graph() -> StateGraph:
    builder = StateGraph(DashboardState)

    # 新增节点
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("load_existing_chart", load_existing_chart)
    builder.add_node("apply_chart_modification", apply_chart_modification)
    builder.add_node("update_chart", update_chart)

    # 原有节点
    builder.add_node("parse_request", parse_request)
    builder.add_node("search_dataset", search_dataset)
    ... # 其余节点不变

    # 新入口：classify_intent（替换原来直接从 parse_request 开始）
    builder.set_entry_point("classify_intent")

    # 修改路径
    builder.add_edge("load_existing_chart", "apply_chart_modification")
    builder.add_edge("apply_chart_modification", "update_chart")
    builder.add_edge("update_chart", END)

    # 原有路径（从 classify_intent 路由到 parse_request）
    builder.add_conditional_edges(
        "classify_intent",
        lambda s: s.get("_goto"),
        {"parse_request": "parse_request", "load_existing_chart": "load_existing_chart"},
    )
    ...
```

---

## 四、state.py 新增字段

```python
# state.py — DashboardState 新增
class DashboardState(TypedDict, total=False):
    ...
    # Phase 14 新增
    previous_charts: list[dict[str, Any]]    # 上一轮创建的图表列表
    previous_dashboard: dict[str, Any] | None  # 上一轮创建的仪表板
    history_summary: str | None              # 压缩的对话历史（供 parse_request 参考）
    reference_chart_id: int | None           # 前端指定要修改的图表 ID
    existing_chart: dict[str, Any] | None    # load_existing_chart 加载的图表详情
    modification: dict[str, Any] | None      # apply_chart_modification 计算的变更
```

---

## 五、前端变更

### 请求参数扩展

```typescript
interface AiChatRequest {
  message: string;
  database_id: number;
  agent_type: 'nl2sql' | 'chart' | 'dashboard' | 'debug';
  session_id: string;
  reference_chart_id?: number;  // Phase 14 新增：指定要修改的图表
}
```

### 图表卡片 UI

在 AI 返回的图表卡片上，新增"修改"交互：

```
┌─────────────────────────────────────────────┐
│  销售趋势折线图              chart_id: 847   │
│  [图表内容]                                  │
│                                              │
│  [打开] [修改这个图表]                        │
└─────────────────────────────────────────────┘
```

用户点击"修改这个图表"后：
1. 输入框 hint 变为"描述你想怎么改这个图表..."
2. 下次发送消息携带 `reference_chart_id: 847`

---

## 六、改动文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `superset/ai/agent/context.py` | 修改 | 新增 `add_tool_summary()` 方法 |
| `superset/ai/graph/runner.py` | 修改 | 执行前读取历史，执行后写回 context |
| `superset/ai/graph/state.py` | 修改 | `DashboardState` 新增 6 个字段 |
| `superset/ai/graph/nodes_parent.py` | 修改 | 新增 4 个节点：`classify_intent / load_existing_chart / apply_chart_modification / update_chart` |
| `superset/ai/graph/builder.py` | 修改 | 入口改为 `classify_intent`，新增修改路径的节点和边 |
| 前端对话组件 | 修改 | 图表卡片新增"修改"按钮，发送时携带 `reference_chart_id` |

---

## 七、测试用例

### 单元测试

**文件**：`tests/unit_tests/ai/test_chart_modification.py`

```python
class TestClassifyIntent:
    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_new_request_without_history(self, mock_llm):
        state = {"request": "创建销售趋势图", "previous_charts": []}
        result = classify_intent(state)
        assert result.goto == "parse_request"
        mock_llm.assert_not_called()  # 无历史时不调 LLM

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_modify_with_history(self, mock_llm):
        mock_llm.return_value = {"intent": "modify"}
        state = {
            "request": "改成折线图",
            "previous_charts": [{"chart_id": 847}],
        }
        result = classify_intent(state)
        assert result.goto == "load_existing_chart"

    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_llm_failure_falls_back_to_new(self, mock_llm):
        mock_llm.side_effect = ValueError("error")
        state = {
            "request": "改成柱状图",
            "previous_charts": [{"chart_id": 847}],
        }
        result = classify_intent(state)
        assert result.goto == "parse_request"  # 失败 fallback 到新建


class TestApplyChartModification:
    @patch("superset.ai.graph.nodes_parent.llm_call_json")
    def test_viz_type_change(self, mock_llm):
        mock_llm.return_value = {
            "viz_type": "echarts_timeseries_line",
            "slice_name": "销售趋势折线图",
            "param_changes": {},
        }
        state = {
            "existing_chart": {
                "chart_id": 847,
                "viz_type": "echarts_timeseries_bar",
                "slice_name": "销售趋势图",
                "form_data": {"viz_type": "echarts_timeseries_bar"},
            },
            "request": "改成折线图",
        }
        result = apply_chart_modification(state)
        assert result.goto == "update_chart"
        assert result.update["modification"]["new_viz_type"] == "echarts_timeseries_line"
```

### E2E 验证场景

```
轮1: POST /chat/ {message: "帮我做华东销售趋势柱状图", session_id: "test-s1"}
  验证：返回 chart_created 事件，chart_id=847

轮2: POST /chat/ {message: "改成折线图", session_id: "test-s1"}
  验证：
  - 事件类型含 "chart_updated"（action="updated"）
  - chart_id 仍为 847（不是新建）
  - 数据库中 chart_id=847 的 viz_type 变为 echarts_timeseries_line

轮3: POST /chat/ {message: "加上 2024 年的过滤", session_id: "test-s1"}
  验证：chart_id=847 的 form_data 中新增时间过滤条件
```

---

## 八、局限性与说明

| 限制 | 说明 |
|------|------|
| 修改范围限于 viz_type 和 form_data | 不支持修改数据集（需重新建图）|
| 不支持仪表板内单图的定向修改 | "把 dashboard 里第 2 个图改成折线图" 为 Phase 15 范围 |
| LLM 修改准确率 | 复杂 form_data（如 echarts 自定义配置）LLM 可能改出错，需 repair 兜底 |
| classify_intent 误判 | 若 LLM 把新建请求误判为修改，会尝试加载不存在的图表，通过 fallback 降级到新建 |

---

## 九、后续规划

Phase 14 实现**单图表修改**。后续（Phase 15）扩展：
- Dashboard 内多图表的选择性修改
- "清除对话历史"接口（`DELETE /api/v1/ai/session/{session_id}`）
- 图表修改历史的 undo 功能（基于 form_data 版本栈）
