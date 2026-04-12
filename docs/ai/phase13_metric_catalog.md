# Phase 13：业务指标语义层（Metric Catalog）

> 生成日期：2026-04-12
> 依赖：Phase 12（列业务注释）

---

## 一、目标

| 目标 | 当前状态 | Phase 13 后 |
|------|---------|------------|
| 业务词汇理解 | ❌ "GMV"、"转化率"无法映射到列 | ✅ Metric Catalog 定义业务指标，LLM 直接使用 |
| 复合指标计算 | ❌ 只能用 SUM/COUNT 等简单聚合 | ✅ 支持 CASE WHEN、多列运算等复合 SQL 表达式 |
| 指标与表绑定 | ❌ 无绑定关系 | ✅ 指标关联到适用的表，避免在错误的表上计算 |
| 指标管理界面 | ❌ 无 | ✅ Superset 管理界面或 YAML 文件两种管理方式 |

---

## 二、背景

### 问题

当前 `plan_query` 节点的 LLM 只能看到列名和 `saved_metrics`（Superset 简单指标如 `SUM(num)`）。面对业务请求：

```
用户：查 GMV
LLM 看到：metric_cols = [amount, revenue, price, discount, tax]
LLM 猜测：SELECT SUM(amount) ...  ← 可能正确，也可能错
```

**根本问题**：没有从业务术语（GMV）到 SQL 表达式的映射层。

### 与 Phase 12 的区别

| Phase 12 | Phase 13 |
|---------|---------|
| 列的业务注释（`description`）——"这列叫什么" | 业务指标定义——"这个业务 KPI 怎么算" |
| 存在 Superset `TableColumn.description` 字段 | 存在独立的 Metric Catalog（JSON/DB） |
| 粒度：单列 | 粒度：多列复合计算 |

---

## 三、Metric Catalog 设计

### 3.1 数据结构

```python
# superset/ai/metric_catalog.py（新文件）

from __future__ import annotations
from typing import Any

# 每个指标条目
class MetricDef(TypedDict):
    sql: str                     # SQL 表达式（可包含 CASE WHEN、子查询等）
    tables: list[str]            # 适用的表名列表（支持 * 通配）
    description: str             # 中文/英文说明
    aliases: list[str]           # 用户可能使用的别名/同义词
    aggregation: str             # "sum" | "avg" | "count" | "ratio" | "custom"
    unit: str | None             # 单位（"元" | "%" | "人次"），可为 None


METRIC_CATALOG: dict[str, MetricDef] = {
    "gmv": {
        "sql": "SUM(CASE WHEN status IN ('paid','completed') THEN amount ELSE 0 END)",
        "tables": ["orders", "dw_order_fact", "order_*"],
        "description": "已完成订单的商品金额合计（Gross Merchandise Volume）",
        "aliases": ["商品交易总额", "成交总额", "销售额"],
        "aggregation": "sum",
        "unit": "元",
    },
    "conversion_rate": {
        "sql": "COUNT(CASE WHEN status='paid' THEN 1 END) * 1.0 / NULLIF(COUNT(*), 0)",
        "tables": ["orders", "dw_order_fact"],
        "description": "支付完成的订单占所有创建订单的比率",
        "aliases": ["转化率", "支付转化", "支付率"],
        "aggregation": "ratio",
        "unit": "%",
    },
    "dau": {
        "sql": "COUNT(DISTINCT user_id)",
        "tables": ["user_events", "page_views", "sessions"],
        "description": "日活跃用户数",
        "aliases": ["日活", "日活用户", "活跃用户数", "DAU"],
        "aggregation": "count",
        "unit": "人",
    },
    "arpu": {
        "sql": "SUM(amount) * 1.0 / NULLIF(COUNT(DISTINCT user_id), 0)",
        "tables": ["orders"],
        "description": "每用户平均收入",
        "aliases": ["人均消费", "客单价", "ARPU"],
        "aggregation": "ratio",
        "unit": "元",
    },
    "new_user_ratio": {
        "sql": (
            "COUNT(CASE WHEN DATE(first_order_date) = DATE(created_at) THEN 1 END)"
            " * 1.0 / NULLIF(COUNT(DISTINCT user_id), 0)"
        ),
        "tables": ["orders"],
        "description": "当日新用户（首次下单）占总用户的比例",
        "aliases": ["新用户占比", "新客率"],
        "aggregation": "ratio",
        "unit": "%",
    },
}
```

---

### 3.2 存储方式选择

提供两种管理方式，可共存：

**方式 A：YAML 文件（推荐初期）**

```yaml
# superset/ai/metric_catalog.yaml（不进代码，在运行时加载）
gmv:
  sql: "SUM(CASE WHEN status IN ('paid','completed') THEN amount ELSE 0 END)"
  tables: ["orders", "dw_order_fact"]
  description: "已完成订单的商品金额合计"
  aliases: ["商品交易总额", "销售额"]
  unit: "元"
```

优点：业务方可直接编辑，无需重新部署代码。

**方式 B：Superset 数据库（推荐中长期）**

新建 `ai_metric_definitions` 表：

```python
# superset/models/ai_metrics.py（新文件）
class AiMetricDefinition(Model):
    __tablename__ = "ai_metric_definitions"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    sql_expression = Column(Text, nullable=False)
    applicable_tables = Column(Text)  # JSON 数组
    description = Column(Text)
    aliases = Column(Text)            # JSON 数组
    unit = Column(String(20))
    created_by_fk = Column(Integer, ForeignKey("ab_user.id"))
    created_on = Column(DateTime)
```

Phase 13 实现方式 A（YAML），方式 B 在 Phase 3 进阶阶段建设。

---

### 3.3 Metric Catalog 加载与查询接口

```python
# superset/ai/metric_catalog.py

import functools
import yaml
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent / "metric_catalog.yaml"


@functools.lru_cache(maxsize=1)
def load_metric_catalog() -> dict[str, MetricDef]:
    """Load metric catalog from YAML file (cached, reload on restart)."""
    if _CATALOG_PATH.exists():
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return METRIC_CATALOG  # fallback to inline defaults


def find_metrics_for_table(table_name: str) -> dict[str, MetricDef]:
    """Return metrics applicable to the given table name."""
    catalog = load_metric_catalog()
    result = {}
    for name, defn in catalog.items():
        tables = defn.get("tables", [])
        if any(
            (t == table_name) or
            (t.endswith("*") and table_name.startswith(t[:-1]))
            for t in tables
        ):
            result[name] = defn
    return result


def match_user_intent_to_metrics(
    user_request: str,
    table_name: str,
) -> dict[str, MetricDef]:
    """Match user request keywords to applicable metrics."""
    applicable = find_metrics_for_table(table_name)
    request_lower = user_request.lower()
    matched = {}
    for name, defn in applicable.items():
        # 检查指标名和所有别名是否出现在请求中
        all_names = [name] + defn.get("aliases", []) + [defn.get("description", "")]
        if any(alias.lower() in request_lower for alias in all_names if alias):
            matched[name] = defn
    return matched
```

---

### 3.4 注入 plan_query 流程

#### state.py 变更

```python
# state.py — SchemaSummary 新增
class SchemaSummary(TypedDict):
    ...
    business_metrics: dict[str, Any]   # 适用于当前表的业务指标定义
```

#### nodes_parent.py 变更

`read_schema` 节点末尾注入 business_metrics：

```python
# nodes_parent.py — read_schema 末尾
from superset.ai.metric_catalog import find_metrics_for_table

business_metrics = find_metrics_for_table(raw["table_name"])
summary["business_metrics"] = {
    name: {
        "sql": defn["sql"],
        "description": defn.get("description", ""),
        "aliases": defn.get("aliases", []),
        "unit": defn.get("unit"),
    }
    for name, defn in business_metrics.items()
}
```

#### nodes_child.py 变更

`PLAN_QUERY_PROMPT` 新增业务指标区块：

```python
# nodes_child.py — PLAN_QUERY_PROMPT
PLAN_QUERY_PROMPT = """\
Plan a SQL query for a chart. Return ONLY valid JSON.

Table: {table_name}
Intent: {analysis_intent} — {slice_name}
{sql_hint}

Available columns:
  time: {datetime_cols}
  dimensions: {dimension_cols}
  metrics: {metric_cols}
  saved metrics: {saved_metrics}

Column descriptions:
{column_descriptions_block}

Business metric definitions (PREFER THESE when user mentions business KPIs):
{business_metrics_block}

{error_hint}
...
"""

# plan_query() 函数内
biz_metrics = summary.get("business_metrics", {})
biz_block = "\n".join(
    f"  {name}: {m['description']}\n    SQL: {m['sql']}"
    for name, m in list(biz_metrics.items())[:8]
) or "  (no business metrics defined for this table)"
```

**LLM 看到的 prompt 示例**：

```
Business metric definitions (PREFER THESE when user mentions business KPIs):
  gmv: 已完成订单的商品金额合计
    SQL: SUM(CASE WHEN status IN ('paid','completed') THEN amount ELSE 0 END)
  conversion_rate: 支付转化率
    SQL: COUNT(CASE WHEN status='paid' THEN 1 END) * 1.0 / NULLIF(COUNT(*), 0)
  dau: 日活跃用户数
    SQL: COUNT(DISTINCT user_id)
```

---

### 3.5 plan_dashboard 的业务指标 hint 注入

`plan_dashboard` 节点的 prompt 中注入活跃业务指标，帮助 LLM 生成更有业务价值的图表计划：

```python
# nodes_parent.py — plan_dashboard prompt 扩展
biz_metrics_hint = ""
if summary.get("business_metrics"):
    biz_metrics_hint = (
        "Available business metrics for this table: "
        + ", ".join(summary["business_metrics"].keys())
    )

prompt = PLAN_DASHBOARD_PROMPT.format(
    ...
    business_metrics_hint=biz_metrics_hint,
)
```

---

## 四、改动文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `superset/ai/metric_catalog.py` | 新建 | 内置指标定义，YAML 加载，查询接口 |
| `superset/ai/metric_catalog.yaml` | 新建 | YAML 格式的业务指标配置文件 |
| `superset/ai/graph/state.py` | 修改 | `SchemaSummary` 新增 `business_metrics` |
| `superset/ai/graph/nodes_parent.py` | 修改 | `read_schema` 注入 `business_metrics`；`plan_dashboard` prompt 扩展 |
| `superset/ai/graph/nodes_child.py` | 修改 | `PLAN_QUERY_PROMPT` 新增 `business_metrics_block` |
| `requirements/ai.txt` | 修改 | 新增 `pyyaml`（Superset 已依赖，确认版本） |

---

## 五、数据流

```
用户："查一下 GMV"
parse_request → goal.target_table = "orders"
search_dataset → 命中 orders 表
read_schema:
  columns = [amount, status, user_id, region, ...]
  business_metrics = find_metrics_for_table("orders")
  → {
      "gmv": {"sql": "SUM(CASE WHEN status IN ('paid'...)...", "description": "..."},
      "conversion_rate": {...},
      ...
    }
plan_query:
  LLM 看到 business_metrics_block:
    gmv: ...
      SQL: SUM(CASE WHEN status IN ('paid','completed') THEN amount ELSE 0 END)
  LLM 生成:
    {
      "metric_expr": "SUM(CASE WHEN status IN ('paid','completed') THEN amount ELSE 0 END)",
      "dimensions": [],
      "limit": 1
    }
validate_sql → _compile_sql → SELECT SUM(CASE WHEN ...) AS metric FROM orders LIMIT 1
execute_query → 结果：1280万
analyze_result → insight: "GMV 本月 1,280 万元"
```

---

## 六、测试用例

### 单元测试

**文件**：`tests/unit_tests/ai/test_metric_catalog.py`

```python
class TestMetricCatalog:
    def test_find_metrics_for_exact_table(self):
        metrics = find_metrics_for_table("orders")
        assert "gmv" in metrics
        assert "conversion_rate" in metrics

    def test_find_metrics_via_wildcard(self):
        """order_* 通配符匹配"""
        metrics = find_metrics_for_table("order_detail")
        # 如果 gmv 配置了 tables: ["order_*"]
        assert "gmv" in metrics

    def test_no_metrics_for_unrelated_table(self):
        metrics = find_metrics_for_table("birth_names")
        assert len(metrics) == 0

    def test_match_user_intent_by_alias(self):
        matched = match_user_intent_to_metrics("查一下GMV和转化率", "orders")
        assert "gmv" in matched
        assert "conversion_rate" in matched

    def test_match_user_intent_chinese_alias(self):
        matched = match_user_intent_to_metrics("销售额多少", "orders")
        assert "gmv" in matched  # "销售额" 是 gmv 的 alias


class TestPlanQueryWithBusinessMetrics:
    @patch("superset.ai.graph.nodes_child.llm_call_json")
    def test_business_metric_sql_used(self, mock_llm):
        """当 LLM 选择 business metric 时，生成正确的复合 SQL"""
        mock_llm.return_value = {
            "metric_expr": "SUM(CASE WHEN status IN ('paid') THEN amount ELSE 0 END)",
            "dimensions": ["region"],
            "time_field": None,
            "limit": 200,
        }
        summary = _make_schema_summary(
            business_metrics={
                "gmv": {
                    "sql": "SUM(CASE WHEN status IN ('paid') THEN amount ELSE 0 END)",
                    "description": "GMV",
                }
            }
        )
        state = {
            "chart_intent": _make_chart_intent(analysis_intent="comparison"),
            "schema_summary": summary,
            "last_error": None,
        }
        result = plan_query(state)
        assert result.goto == "validate_sql"
        # SQL 表达式含 CASE WHEN
        assert "CASE WHEN" in result.update["sql_plan"]["metric_expr"]
```

---

## 七、运维与维护

### 指标定义更新流程

1. 修改 `superset/ai/metric_catalog.yaml`
2. 重启 Celery Worker（因为 `lru_cache` 在 worker 进程级别）
3. 若使用 DB 方式（Phase 3B），则通过管理界面更新，实时生效

### 指标定义规范

每个指标条目建议遵守：
- `sql` 必须是纯 SELECT 表达式，不含表名（表名由 `_compile_sql` 提供）
- `sql` 中引用的列名必须在目标表中存在
- `aliases` 尽量覆盖中英文、缩写、口语表达

### 冲突处理

当 Metric Catalog 中的指标 SQL 与 Superset `saved_metrics` 同名时，优先使用 Metric Catalog（因为 Catalog 包含业务过滤条件，语义更准确）。

---

## 八、后续规划

Phase 13 实现**静态 YAML 配置**的 Metric Catalog。

后续（Phase 3B，预计第4-5个月）考虑：
- Superset 管理界面新增"AI 业务指标"管理入口
- 支持从 `SqlaTable.metrics`（已有指标）一键导入为 AI 指标
- 支持指标版本管理（变更历史记录）
