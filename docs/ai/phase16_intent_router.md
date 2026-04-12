# Phase 16：统一意图路由（Unified Intent Router）

> 生成日期：2026-04-12
> 依赖：Phase 11-15（各 Agent 已实现）

---

## 一、目标

用户使用一个对话框，AI 自动判断意图，路由到合适的 Agent，无需手动选择模式。

```
之前（Phase 11-15）：
  用户必须选择：[NL2SQL | 图表 | 仪表板 | 平台助手]

Phase 16 后：
  用户直接输入，AI 自动路由：
  "查一下销售数据"     → NL2SQLAgent
  "帮我做个折线图"     → ChartAgent (StateGraph)
  "做个综合 dashboard" → DashboardAgent (StateGraph)
  "有没有失败的报告"   → CopilotAgent
```

---

## 二、当前代码架构分析

### 路由入口

```
POST /api/v1/ai/chat/   →   tasks.py:run_agent_task()
                                ↓
                     agent_type = kwargs["agent_type"]
                                ↓
          ┌──────────────────────────────┐
          │  if agent_type in {chart, dashboard}  │
          │      → run_graph() (StateGraph)       │
          │  else:                               │
          │      → create_agent_runner()         │
          │         → NL2SQLAgent / DebugAgent   │
          └──────────────────────────────┘
```

### 当前问题

`agent_type` 由**前端传入**，是用户手动选择的结果。Phase 16 要做的是：在 `tasks.py` 最顶部插入路由层，将 `agent_type="auto"` 替换为实际类型。

---

## 三、整体设计

### 3.1 新增 agent_type="auto"

```
前端：发送 agent_type="auto"（新增默认选项）
tasks.py：检测到 auto → 调用 IntentRouter → 得到实际 agent_type → 走原有路径
```

原有 `nl2sql / chart / dashboard / debug / copilot` 保持不变，向后兼容。

### 3.2 路由决策流

```
用户消息
    ↓
[Step 1] 上下文优先规则
   ├── 是延续词（"那个"、"再"、"继续"）+ 有 last_agent → 直接复用
   └── 否则继续
    ↓
[Step 2] 关键词快速匹配（O(n) 字符串查找，~0ms）
   ├── 命中且置信度 ≥ 0.75 → 直接路由
   └── 命中但置信度 < 0.75 或 未命中 → 继续
    ↓
[Step 3] LLM 精确分类（单次 LLM 调用，~0.5-1s）
   ├── 置信度 ≥ 0.5 → 路由到对应 Agent
   └── 置信度 < 0.5 → fallback 到 nl2sql
    ↓
最终 agent_type
```

---

## 四、详细实现

### 4.1 数据结构

```python
# superset/ai/router/__init__.py（新目录）
# superset/ai/router/types.py

from __future__ import annotations
from dataclasses import dataclass


AgentType = str  # "nl2sql" | "chart" | "dashboard" | "copilot" | "debug"

VALID_AGENT_TYPES: tuple[AgentType, ...] = (
    "nl2sql", "chart", "dashboard", "copilot", "debug",
)


@dataclass
class RouteDecision:
    agent: AgentType
    confidence: float          # 0.0 - 1.0
    method: str                # "context" | "keyword" | "llm" | "fallback"
    reason: str                # 调试用，不展示给用户


@dataclass
class RouterContext:
    last_agent: AgentType | None    # 上一轮用的 agent
    last_message: str | None        # 上一轮用户消息
    session_id: str
    user_id: int
```

---

### 4.2 关键词规则表

```python
# superset/ai/router/rules.py

from __future__ import annotations

# 每个 agent 的关键词，按"确定性"分高低两档
_RULES: dict[str, dict[str, list[str]]] = {
    "chart": {
        "high": [
            # 明确的图表创建意图
            "画一个图", "做一个图", "生成图表", "create chart",
            "折线图", "柱状图", "饼图", "散点图", "漏斗图",
            "echarts", "visualize", "可视化图表",
        ],
        "low": [
            # 可能是图表也可能是查询
            "趋势", "分布", "对比图", "图表", "chart",
            "看一下趋势", "展示",
        ],
    },
    "dashboard": {
        "high": [
            "仪表板", "dashboard", "看板", "创建仪表盘",
            "多个图表", "几张图", "综合分析页",
        ],
        "low": [
            "overview", "全景", "汇总页", "总览",
        ],
    },
    "copilot": {
        "high": [
            # 平台状态查询，明确指向 Superset 元数据
            "有没有失败的报告", "告警", "定时任务",
            "我有哪些权限", "我是什么角色", "report status",
            "schedule", "saved query", "保存的查询",
            "查询历史", "慢查询", "哪些图表", "多少个 dashboard",
            "谁有权限", "我能看到哪些",
        ],
        "low": [
            "报告", "告警状态", "权限", "角色",
            "图表列表", "仪表板列表",
        ],
    },
    "debug": {
        "high": [
            "报错", "sql error", "fix this sql", "修复这个 sql",
            "column does not exist", "syntax error",
            "帮我修复", "debug",
        ],
        "low": [
            "报错了", "出错了", "error",
        ],
    },
    # nl2sql 是 fallback，不需要关键词列表
}

# 延续词：用于上下文复用判断
_CONTINUATION_KEYWORDS = [
    "这个", "那个", "它", "再", "继续", "也", "还有", "另外",
    "修改", "改一下", "换成", "加上", "去掉",
    "this", "that", "it", "also", "and", "modify",
]


def keyword_route(message: str) -> tuple[str, float]:
    """
    Returns (agent_type, confidence).
    confidence: 1.0 → high certainty match, 0.65 → low certainty match,
                0.0 → no match (caller should try LLM)
    """
    msg_lower = message.lower()

    scores: dict[str, float] = {}

    for agent, rule in _RULES.items():
        high_hits = sum(1 for kw in rule["high"] if kw in msg_lower)
        low_hits = sum(1 for kw in rule["low"] if kw in msg_lower)
        if high_hits >= 1:
            scores[agent] = 0.90 + min(high_hits - 1, 2) * 0.02  # max 0.94
        elif low_hits >= 2:
            scores[agent] = 0.72
        elif low_hits == 1:
            scores[agent] = 0.60

    if not scores:
        return "nl2sql", 0.0  # 未命中，需要 LLM

    best_agent = max(scores, key=lambda a: scores[a])
    return best_agent, scores[best_agent]


def is_continuation(message: str) -> bool:
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _CONTINUATION_KEYWORDS)
```

---

### 4.3 LLM 分类器

```python
# superset/ai/router/llm_classifier.py

from __future__ import annotations
import logging
from superset.ai.graph.llm_helpers import llm_call_json

logger = logging.getLogger(__name__)

_CLASSIFIER_PROMPT = """\
Classify the user request to ONE agent type. Return ONLY valid JSON.

Agent types:
- "nl2sql":    User wants to query data from a database using SQL.
               Examples: "查销售额", "count users", "统计各地区数据"
- "chart":     User wants to create or visualize a chart.
               Examples: "画折线图", "做一个柱状图", "visualize trend"
- "dashboard": User wants to create a dashboard with multiple charts.
               Examples: "做一个仪表板", "create overview dashboard"
- "copilot":   User wants info about the Superset platform itself
               (not about the data IN the database, but about Superset's
               assets, permissions, report status, query history, etc.)
               Examples: "有哪些失败的报告", "我有什么权限", "查一下图表列表"
- "debug":     User wants to fix a broken SQL query.
               Examples: "这个 SQL 报错了", "fix: column not found"

Context:
  session_last_agent: {last_agent}
  session_last_message_preview: {last_message}

User message: {message}

Response format:
{{
  "agent": "nl2sql|chart|dashboard|copilot|debug",
  "confidence": 0.0-1.0,
  "reason": "one short sentence"
}}
"""


def llm_classify(
    message: str,
    last_agent: str | None,
    last_message: str | None,
) -> tuple[str, float, str]:
    """
    Returns (agent_type, confidence, reason).
    On any failure, returns ("nl2sql", 0.5, "fallback").
    """
    prompt = _CLASSIFIER_PROMPT.format(
        message=message[:400],
        last_agent=last_agent or "none",
        last_message=(last_message or "")[:100],
    )
    try:
        result = llm_call_json(prompt)
        agent = result.get("agent", "nl2sql")
        confidence = float(result.get("confidence", 0.5))
        reason = result.get("reason", "")
        if agent not in ("nl2sql", "chart", "dashboard", "copilot", "debug"):
            agent = "nl2sql"
        return agent, confidence, reason
    except Exception as exc:
        logger.warning("LLM intent classifier failed: %s", exc)
        return "nl2sql", 0.5, f"fallback due to error: {exc}"
```

---

### 4.4 IntentRouter 主类

```python
# superset/ai/router/router.py

from __future__ import annotations
import logging
from superset.ai.router.types import RouteDecision, RouterContext
from superset.ai.router.rules import keyword_route, is_continuation

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.75   # 关键词命中需达到此置信度才跳过 LLM
_LLM_MIN_CONFIDENCE = 0.50     # LLM 结果低于此置信度 → fallback to nl2sql


class IntentRouter:
    """Two-level intent router: keyword rules → LLM classifier.

    Design goals:
    1. Zero latency for clear cases (keyword match ≥ 0.75 confidence)
    2. High accuracy for ambiguous cases (LLM classification)
    3. Context-awareness (session history influences routing)
    4. Always returns a valid agent type (nl2sql as safe fallback)
    """

    def route(self, message: str, context: RouterContext) -> RouteDecision:
        # ── Step 1: Context continuation ─────────────────────────────
        if context.last_agent and is_continuation(message):
            logger.debug(
                "router: context_continuation last_agent=%s", context.last_agent
            )
            return RouteDecision(
                agent=context.last_agent,
                confidence=0.88,
                method="context",
                reason=f"Continues previous {context.last_agent} session",
            )

        # ── Step 2: Keyword fast path ─────────────────────────────────
        agent, confidence = keyword_route(message)
        logger.debug(
            "router: keyword agent=%s confidence=%.2f", agent, confidence
        )

        if confidence >= _CONFIDENCE_THRESHOLD:
            return RouteDecision(
                agent=agent,
                confidence=confidence,
                method="keyword",
                reason=f"Keyword match for {agent}",
            )

        # ── Step 3: LLM precise classification ───────────────────────
        from superset.ai.router.llm_classifier import llm_classify

        llm_agent, llm_confidence, llm_reason = llm_classify(
            message=message,
            last_agent=context.last_agent,
            last_message=context.last_message,
        )
        logger.debug(
            "router: llm agent=%s confidence=%.2f reason=%s",
            llm_agent, llm_confidence, llm_reason,
        )

        if llm_confidence >= _LLM_MIN_CONFIDENCE:
            return RouteDecision(
                agent=llm_agent,
                confidence=llm_confidence,
                method="llm",
                reason=llm_reason,
            )

        # ── Fallback ──────────────────────────────────────────────────
        logger.info(
            "router: low confidence (%.2f), fallback to nl2sql. message=%s",
            llm_confidence, message[:100],
        )
        return RouteDecision(
            agent="nl2sql",
            confidence=0.5,
            method="fallback",
            reason="Low confidence, defaulting to nl2sql",
        )
```

---

### 4.5 任务层集成

`tasks.py` 修改，在 `agent_type == "auto"` 时调用路由器：

```python
# tasks.py — run_agent_task() 开头新增

agent_type = kwargs.get("agent_type", "nl2sql")

# ── Phase 16: Intent routing ──────────────────────────────────────
if agent_type == "auto":
    from superset.ai.router.router import IntentRouter
    from superset.ai.router.types import RouterContext
    from superset.ai.agent.context import ConversationContext

    # 读取上一轮路由结果（存在 context 里）
    ctx = ConversationContext(user_id=user_id, session_id=session_id)
    history = ctx.get_history()
    last_meta = next(
        (h for h in reversed(history) if h.get("role") == "router_meta"),
        None,
    )
    router_ctx = RouterContext(
        last_agent=last_meta.get("agent") if last_meta else None,
        last_message=last_meta.get("message") if last_meta else None,
        session_id=session_id,
        user_id=user_id,
    )

    decision = IntentRouter().route(message=message, context=router_ctx)
    agent_type = decision.agent

    # 把路由决策存入历史（不计入轮数，不发给 LLM）
    ctx.add_router_meta(
        agent=decision.agent,
        confidence=decision.confidence,
        method=decision.method,
        message=message,
    )

    # 发送路由事件到前端（可选：让用户知道 AI 选了哪个模式）
    stream.publish_event(channel_id, AgentEvent(
        type="intent_routed",
        data={
            "agent": decision.agent,
            "confidence": round(decision.confidence, 2),
            "method": decision.method,
        },
    ))

    logger.info(
        "intent_routed agent=%s confidence=%.2f method=%s session=%s",
        decision.agent, decision.confidence, decision.method, session_id,
    )
# ── 后续继续原有路径（agent_type 已确定）──────────────────────────
```

---

### 4.6 ConversationContext 扩展

```python
# agent/context.py — 新增 add_router_meta / get_last_route

def add_router_meta(
    self,
    agent: str,
    confidence: float,
    method: str,
    message: str,
) -> None:
    """Store routing decision for next-turn context awareness.

    Stored with role='router_meta', excluded from LLM message list.
    Trimmed to keep only the most recent entry.
    """
    history = self.get_history()
    # 只保留最新一条 router_meta
    history = [h for h in history if h.get("role") != "router_meta"]
    history.append({
        "role": "router_meta",
        "agent": agent,
        "confidence": confidence,
        "method": method,
        "message": message[:200],
    })
    self._cache().set(self._key, json.dumps(history), timeout=_CONTEXT_TTL)
```

---

### 4.7 新增事件类型

```python
# agent/events.py — 新增 intent_routed
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
    "done",
    "error",
    "intent_routed",   # ← Phase 16 新增
]
```

---

### 4.8 API 层修改

`api.py` 新增对 `auto` 的支持并移除 agent_type 必须在已知列表的硬限制：

```python
# api.py — 修改 feature flag 检查
agent_type = body.get("agent_type", "auto")   # 默认从 "nl2sql" 改为 "auto"

# 允许 "auto" 通过，不再校验是否在已知列表
_GATED_AGENTS = {
    "chart": "AI_AGENT_CHART",
    "dashboard": "AI_AGENT_DASHBOARD",
    "debug": "AI_AGENT_DEBUG",
    "copilot": "AI_AGENT_COPILOT",
}
if agent_type in _GATED_AGENTS:
    flag = _GATED_AGENTS[agent_type]
    if not is_feature_enabled(flag):
        return self.response_400(message=f"{agent_type} agent not enabled.")
# "auto" 和 "nl2sql" 不需要额外 feature flag
```

---

## 五、前端变更

### 5.1 接口变更

```typescript
// 请求参数
interface AiChatRequest {
  message: string;
  database_id: number;
  session_id: string;
  agent_type?: string;   // 可选，默认 "auto"
}
```

### 5.2 新增 intent_routed 事件处理

```typescript
// 事件处理
case "intent_routed":
  setCurrentAgentMode(event.data.agent);   // 更新 UI 状态指示器
  // 可选：显示小标签 "正在以图表模式处理..."
  break;
```

### 5.3 UI 设计

原来的模式选择下拉框改为带"自动"默认选项的轻量指示器：

```
┌─────────────────────────────────────────┐
│ 模式：[🤖 自动 ▼]                        │
│  └── 实际使用：图表模式（AI 自动判断）     │←  intent_routed 事件触发更新
└─────────────────────────────────────────┘

下拉选项：
  ✦ 自动（默认）
  ─────────────
  🔢 SQL 查询
  📊 图表生成
  📋 仪表板
  🏠 平台助手
  🔧 SQL 调试
```

---

## 六、改动文件清单

| 文件 | 类型 | 内容 |
|------|------|------|
| `superset/ai/router/__init__.py` | 新建 | 包初始化 |
| `superset/ai/router/types.py` | 新建 | `RouteDecision`, `RouterContext` 数据类 |
| `superset/ai/router/rules.py` | 新建 | 关键词规则表 + `keyword_route()` + `is_continuation()` |
| `superset/ai/router/llm_classifier.py` | 新建 | LLM 分类器 prompt + `llm_classify()` |
| `superset/ai/router/router.py` | 新建 | `IntentRouter.route()` 主流程 |
| `superset/ai/tasks.py` | 修改 | `auto` 路由逻辑（~30行新增） |
| `superset/ai/agent/context.py` | 修改 | `add_router_meta()` 方法 |
| `superset/ai/agent/events.py` | 修改 | 新增 `intent_routed` 事件类型 |
| `superset/ai/api.py` | 修改 | 默认 `agent_type="auto"`，gated agent 检查重构 |
| 前端对话组件 | 修改 | `intent_routed` 事件处理，模式指示器 UI |

---

## 七、测试用例

### 7.1 单元测试

**文件**：`tests/unit_tests/ai/test_intent_router.py`

```python
class TestKeywordRoute:
    def test_chart_high_confidence(self):
        agent, conf = keyword_route("帮我做一个折线图")
        assert agent == "chart"
        assert conf >= 0.90

    def test_dashboard_explicit(self):
        agent, conf = keyword_route("创建一个仪表板，包含3个图表")
        assert agent == "dashboard"
        assert conf >= 0.90

    def test_copilot_report_failure(self):
        agent, conf = keyword_route("有没有失败的报告？")
        assert agent == "copilot"
        assert conf >= 0.90

    def test_debug_sql_error(self):
        agent, conf = keyword_route("这个 SQL 报错了：column not found")
        assert agent == "debug"
        assert conf >= 0.90

    def test_nl2sql_fallback(self):
        agent, conf = keyword_route("帮我了解一下情况")
        assert agent == "nl2sql"
        assert conf == 0.0  # 未命中，需要 LLM

    def test_ambiguous_trend(self):
        """'趋势' 单词置信度应低于阈值，触发 LLM"""
        _, conf = keyword_route("看看销售趋势")
        assert conf < 0.75


class TestContextContinuation:
    def test_continuation_reuses_last_agent(self):
        ctx = RouterContext(
            last_agent="chart",
            last_message="做个折线图",
            session_id="s1",
            user_id=1,
        )
        decision = IntentRouter().route("改成柱状图", ctx)
        assert decision.agent == "chart"
        assert decision.method == "context"

    def test_no_continuation_without_keywords(self):
        ctx = RouterContext(
            last_agent="chart",
            last_message="做个折线图",
            session_id="s1",
            user_id=1,
        )
        decision = IntentRouter().route("查一下销售数据", ctx)
        assert decision.agent != "chart"  # 不应延续


class TestRouterFallback:
    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_failure_falls_back_to_nl2sql(self, mock_llm):
        mock_llm.side_effect = ValueError("timeout")
        decision = IntentRouter().route(
            "帮我分析一下这个情况",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "nl2sql"
        assert decision.method == "fallback"

    @patch("superset.ai.router.llm_classifier.llm_call_json")
    def test_llm_low_confidence_falls_back(self, mock_llm):
        mock_llm.return_value = {"agent": "copilot", "confidence": 0.3, "reason": "unsure"}
        decision = IntentRouter().route(
            "看一下",
            RouterContext(None, None, "s1", 1),
        )
        assert decision.agent == "nl2sql"
        assert decision.method == "fallback"
```

### 7.2 路由准确率基线测试（20 个场景）

| # | 输入 | 预期 agent | 预期方法 |
|---|------|-----------|---------|
| 1 | "统计各地区销售额" | nl2sql | keyword/fallback |
| 2 | "SELECT * FROM orders" | nl2sql | keyword |
| 3 | "查一下最近 7 天的数据" | nl2sql | keyword/llm |
| 4 | "做一个折线图" | chart | keyword |
| 5 | "柱状图展示各性别数量" | chart | keyword |
| 6 | "可视化一下趋势" | chart | llm |
| 7 | "创建一个仪表板" | dashboard | keyword |
| 8 | "做3个图表做成仪表板" | dashboard | keyword |
| 9 | "有没有失败的报告" | copilot | keyword |
| 10 | "我有什么权限" | copilot | keyword |
| 11 | "列出所有图表" | copilot | keyword |
| 12 | "这个 SQL 报错了" | debug | keyword |
| 13 | "帮我修复 column not found" | debug | keyword |
| 14 | "分析一下数据" | nl2sql | llm |
| 15 | "看看销售情况" | nl2sql | llm |
| 16 | "改成折线图（上轮=chart）" | chart | context |
| 17 | "再加一个过滤（上轮=nl2sql）" | nl2sql | context |
| 18 | "帮我了解一下概况" | nl2sql/copilot | llm |
| 19 | "（空字符串）" | nl2sql | fallback |
| 20 | "随机乱码 xyz123" | nl2sql | fallback |

**目标准确率 ≥ 90%（20 个场景中 18 个正确）**

---

## 八、性能影响

| 场景 | 额外延迟 | 频率 |
|------|---------|-----|
| 关键词命中（L1） | 0ms | ~60% 请求 |
| LLM 分类（L2） | ~500-800ms | ~35% 请求 |
| 回退（L3） | 0ms | ~5% 请求 |

LLM 分类可与 `AiStreamManager.publish_event` 的初始化并行（前端此时显示"正在思考中..."），用户感知延迟约 300ms。

---

## 九、Feature Flag

```python
# superset/config.py
FEATURE_FLAGS = {
    "AI_AGENT_AUTO_ROUTE": False,   # Phase 16 开关，关闭时 auto → nl2sql
}
```

```python
# tasks.py — guard
if agent_type == "auto" and not is_feature_enabled("AI_AGENT_AUTO_ROUTE"):
    agent_type = "nl2sql"   # 安全降级
```

---

## 十、后续规划

| 优化 | 时机 |
|------|------|
| 收集路由日志，统计准确率 | Phase 16 上线后 1 个月 |
| 基于日志微调关键词规则 | 准确率 < 85% 时 |
| 用户纠错：点击"换一个模式"反馈 | Phase 17 |
| 用微调小模型替代 LLM 分类器 | 请求量 > 10万/天 时考虑 |
