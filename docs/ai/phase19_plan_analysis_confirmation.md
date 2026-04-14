# Phase 19：显式计划确认层（Plan Analysis Confirmation）

> 生成日期：2026-04-14
> 依赖：Phase 8（StateGraph）、Phase 17（Clarification Loop）、Phase 18（Multi-Dataset）

---

## 一、目标

| 目标 | 当前状态 | Phase 19 后 |
|------|---------|------------|
| 语义纠偏 | ❌ 系统默默执行，错了不报错 | ✅ 执行前暴露关键假设，用户可纠偏 |
| 运行模式 | ❌ 只有直接执行 | ✅ direct mode（自动执行）+ plan mode（展示计划等待确认） |
| 风险检测 | ❌ 无置信度评估 | ✅ 6 项风险信号自动评分，低置信度自动降级到 plan mode |
| 用户信任 | ❌ "黑盒执行" | ✅ "分析助手"——先告诉用户准备怎么做 |

---

## 二、背景

### 问题：在错误语义上执行得很顺

当前流程的核心风险不是"执行不出来"，而是**语义理解偏了但系统不报错**：

```
用户："做一个销售分析仪表板"
Agent：选择 orders 表 → 3 张图表 → SQL → 图表 → Dashboard ✓

实际：用户想看的是"收缴率"（项目收缴/项目应收），不是"订单金额"
      orders 表并不包含收缴数据 → 整个 Dashboard 语义就是错的
      但 SQL 能跑、图表能建、Dashboard 能交付 → 无任何报错
```

### 现有规划的局限

| 层 | 节点 | 面向 | 可审阅 |
|---|------|------|--------|
| 请求解析 | `parse_request` | 系统内部 | ❌ |
| 图表规划 | `plan_dashboard` | 系统内部 | ❌ |
| SQL 规划 | `plan_query` | 系统内部 | ❌ |

三层规划都是**系统自说自话**，用户完全看不到系统"准备怎么干"，只能在结果出来后才发现理解偏了。

---

## 三、设计原则

1. **轻计划、强约束、按需确认** — 计划摘要不超过 5 行，不写成长文
2. **双模式运行** — direct mode（高置信度自动执行）+ plan mode（低置信度先展示计划）
3. **系统自动降级** — 不依赖用户手动开关，根据风险信号自动触发 plan mode
4. **复用确认模式** — 沿用 `clarify_user` 两轮模式，前端零改动
5. **零开销快速路径** — 高置信度场景下 `review_analysis` 只做评分计算，不调 LLM，不阻塞流程

---

## 四、架构设计

### 4.1 新节点位置

```
当前流程：
  read_schema → plan_dashboard → single_chart_subgraph ×N

新流程：
  read_schema → plan_dashboard → review_analysis ─┬→ single_chart_subgraph ×N  (direct mode)
                                                    └→ __end__                  (plan mode)
```

**为什么放在 `plan_dashboard` 之后：**

- 放太前（`parse_request` 之后）：系统连 schema 都没读，计划会太空
- 放太后（`single_chart_subgraph` 之后）：已经开始查数建图了，计划失去意义
- `plan_dashboard` 之后：系统已经知道用哪个 dataset、哪些列、生成几张什么图 → 此时信息量刚好够输出一份有意义的计划

### 4.2 置信度评分

6 个风险信号，纯代码计算（不调 LLM）：

| 信号 | 检测方式 | 分数 |
|------|---------|------|
| 数据集选择不确定 | `select_dataset` 中 best_score < 50 | +30 |
| 多主题混合 | `chart_intents` 中 ≥3 个不同 `analysis_intent` | +20 |
| Dashboard 模式 + 3+ 图 | `agent_mode=="dashboard"` && `len(chart_intents) >= 3` | +20 |
| 涉及派生/比例指标 | `business_metrics` 非空且被引用 | +15 |
| 无明确时间列 | `schema_summary.datetime_cols` 为空 | +10 |
| 多数据集模式 | `goal.multi_dataset == True` | +10 |

**评分规则：** 总分 ≥ 30 → 自动触发 plan mode；< 30 → direct mode。

### 4.3 计划输出格式

`analysis_plan` 事件的结构化数据：

```json
{
  "dataset": "birth_names",
  "dataset_reason": "精确匹配用户指定的表名",
  "metrics_dimensions": {
    "metrics": ["SUM(num) 出生人数"],
    "dimensions": ["gender 性别", "state 州"]
  },
  "time_range": "未指定（schema 含 ds 列，可按时间分析）",
  "charts": [
    {"index": 0, "title": "出生趋势图", "intent": "趋势分析", "viz": "折线图"},
    {"index": 1, "title": "性别分布", "intent": "构成分析", "viz": "饼图"}
  ],
  "assumptions_risks": [
    "假设 num 列为出生人数",
    "未指定时间范围，默认使用全量数据"
  ],
  "confidence": 0.65
}
```

文本回退（供无专用 UI 的前端）：

```
📋 分析计划

数据集：birth_names（精确匹配）
指标：SUM(num) 出生人数 | 维度：gender, state
时间：ds 列可用，未指定范围
图表（2 张）：
  1. 出生趋势图 — 趋势分析 — 折线图
  2. 性别分布 — 构成分析 — 饼图

⚠ 假设：假设 num 列为出生人数
💡 回复"确认执行"继续，或告诉我需要调整的地方
```

### 4.4 确认流程（两轮模式，复用现有模式）

**Turn 1（plan mode）：**

```
用户："做一个全面的数据分析"
  ↓
review_analysis → 置信度 0.55 → plan mode
  → 发布 analysis_plan 事件 + text_chunk 文本回退
  → goto="__end__"

用户看到计划摘要，回复"确认执行"或"改成按月统计"
```

**Turn 2（用户确认）：**

```
用户："确认执行"
  ↓
tasks.py 检测到 conversation_history 中有 analysis_plan 条目
  → 设 execution_mode="direct"
  → 正常执行 graph（review_analysis 检测到 direct 模式，直接跳过）
  ↓
plan_dashboard → single_chart_subgraph ×N → create_dashboard
```

---

## 五、State 扩展

```python
# superset/ai/graph/state.py — DashboardState 新增

# Phase 19: plan analysis confirmation
execution_mode: str | None            # "plan" | "direct" | None（None 时自动判断）
analysis_plan: dict[str, Any] | None  # review_analysis 输出的结构化计划
```

---

## 六、新增节点详细设计

### 6.1 review_analysis

```python
def review_analysis(
    state: DashboardState,
) -> Command[Literal["single_chart_subgraph", "__end__"]]:
    """Review analysis plan — decide direct execution or plan confirmation."""
    
    # 1. 确认后的第二轮：直接跳过
    if state.get("execution_mode") == "direct":
        return Command(goto="single_chart_subgraph")

    # 2. 计算置信度（纯代码，零 LLM 开销）
    confidence = _compute_confidence(state)
    
    # 3. 决定运行模式
    mode = state.get("execution_mode")  # API 参数手动指定
    if not mode:
        mode = "plan" if confidence < 0.7 else "direct"

    # 4. 构建结构化计划
    plan = _build_analysis_plan(state, confidence)

    if mode == "plan":
        # 发布计划事件，终止 graph 等待用户确认
        _publish_plan_event(state, plan)
        return Command(
            update={"analysis_plan": plan, "execution_mode": "plan"},
            goto="__end__",
        )

    # direct mode：继续执行
    return Command(
        update={"analysis_plan": plan, "execution_mode": "direct"},
        goto="single_chart_subgraph",
    )
```

### 6.2 _compute_confidence

```python
def _compute_confidence(state: DashboardState) -> float:
    """Compute confidence score from risk signals. Returns 0.0-1.0."""
    risk_score = 0.0
    
    # Signal 1: dataset selection uncertainty
    goal = state.get("goal", {})
    if goal.get("dataset_match_score", 100) < 50:
        risk_score += 30
    
    # Signal 2: multi-topic
    intents = state.get("chart_intents", [])
    unique_intents = len({i.get("analysis_intent") for i in intents})
    if unique_intents >= 3:
        risk_score += 20
    
    # Signal 3: dashboard + 3+ charts
    if state.get("agent_mode") == "dashboard" and len(intents) >= 3:
        risk_score += 20
    
    # Signal 4: derived/ratio metrics
    summary = state.get("schema_summary") or {}
    biz_metrics = summary.get("business_metrics", {})
    if biz_metrics:
        risk_score += 15
    
    # Signal 5: no time column
    if not summary.get("datetime_cols"):
        risk_score += 10
    
    # Signal 6: multi-dataset
    if goal.get("multi_dataset"):
        risk_score += 10
    
    # Map risk_score to confidence (0-100 → 1.0-0.0)
    confidence = max(0.0, 1.0 - risk_score / 100.0)
    return confidence
```

### 6.3 _build_analysis_plan

从已有 state 字段组装计划，不调 LLM：

```python
def _build_analysis_plan(state: DashboardState, confidence: float) -> dict:
    goal = state.get("goal", {})
    summary = state.get("schema_summary") or {}
    intents = state.get("chart_intents", [])
    
    return {
        "dataset": summary.get("table_name") or goal.get("target_table", ""),
        "dataset_reason": _get_dataset_reason(goal, summary),
        "metrics_dimensions": {
            "metrics": summary.get("metric_cols", [])[:5],
            "dimensions": summary.get("dimension_cols", [])[:5],
        },
        "time_range": _describe_time_range(summary),
        "charts": [
            {
                "index": i.get("chart_index", idx),
                "title": i.get("slice_name", ""),
                "intent": i.get("analysis_intent", ""),
                "viz": i.get("preferred_viz", ""),
                "target_table": i.get("target_table"),
            }
            for idx, i in enumerate(intents)
        ],
        "assumptions_risks": _extract_assumptions(goal, summary, intents),
        "confidence": round(confidence, 2),
    }
```

### 6.4 _publish_plan_event

复用 `AiStreamManager` 发布事件 + 文本回退：

```python
def _publish_plan_event(state: DashboardState, plan: dict) -> None:
    from superset.ai.agent.events import AgentEvent
    from superset.ai.streaming.manager import AiStreamManager
    
    channel_id = state.get("channel_id")
    if not channel_id:
        return
    
    stream = AiStreamManager()
    
    # 结构化事件（供未来专用 UI）
    stream.publish_event(channel_id, AgentEvent(type="analysis_plan", data=plan))
    
    # 文本回退（供当前前端渲染）
    text = _format_plan_text(plan)
    stream.publish_event(
        channel_id,
        AgentEvent(type="text_chunk", data={"content": text}),
    )
```

---

## 七、改动文件清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | `superset/ai/graph/state.py` | DashboardState 新增 `execution_mode`, `analysis_plan` |
| 2 | `superset/ai/graph/nodes_parent.py` | 新增 `review_analysis`, `_compute_confidence`, `_build_analysis_plan`, `_publish_plan_event` 及辅助函数 |
| 3 | `superset/ai/graph/builder.py` | 注册 `review_analysis`，`plan_dashboard` → `review_analysis` |
| 4 | `superset/ai/graph/runner.py` | `_NODE_PROGRESS` 加 `review_analysis`，`_emit_node_events` 处理 `review_analysis` |
| 5 | `superset/ai/agent/events.py` | 新增 `analysis_plan` 事件类型 |

**无需改动**：前端（通过 text_chunk 回退渲染）、tasks.py（确认后第二轮通过 conversation_history + execution_mode 检测，复用现有模式）、tools/（复用现有）。

---

## 八、向后兼容

- `execution_mode` 为 None 时自动评分，高置信度直接执行
- `review_analysis` 在 direct mode 下仅做一次 dict 组装 + 数值比较，不调 LLM，不阻塞
- 单表 + 单图 + 高置信度场景完全等价于当前流程
- 前端零改动

---

## 九、测试用例

### 单元测试

- `TestComputeConfidence`：6 个风险信号独立触发、叠加评分、边界值
- `TestReviewAnalysis`：direct mode 跳过、plan mode 发布事件并终止、execution_mode="direct" 跳过
- `TestBuildAnalysisPlan`：计划字段完整性、空 schema 处理、多数据集模式
- `TestFormatPlanText`：中文文本格式化

### Docker E2E 测试

1. 高置信度单图："用 birth_names 做 gender 分布饼图" → 直接执行
2. 低置信度 dashboard："做一个全面的数据分析" → 触发 plan mode
3. 确认继续："确认执行" → 跳过 review，正常执行
4. 手动指定：API 参数 `execution_mode: "plan"` → 强制 plan mode

---

## 十、局限性与后续

| 限制 | 说明 |
|------|------|
| Signal 1 需 select_dataset 传递 match_score | 当前 `select_dataset` 不将 best_score 写入 state，需补丁 |
| 确认后的第二轮没有"修改计划"能力 | 用户只能确认或重新发起请求 |
| 置信度阈值 0.7 是经验值 | 需根据实际使用调优 |
| 不支持部分确认（确认某些图、修改某些图） | 后续可扩展 |
