# Phase 12：数据集智能发现与列业务注释

> 生成日期：2026-04-12
> 依赖：Phase 8（StateGraph）、Phase 11（多轮对话）

---

## 一、目标

| 目标 | 当前状态 | Phase 12 后 |
|------|---------|------------|
| 表名精确匹配 | ❌ 只有等值匹配，名称不同直接 not_found | ✅ LIKE 模糊 + difflib 相似度多级 fallback |
| 列业务含义 | ❌ 只有列名和类型，LLM 不知道业务语义 | ✅ 列的 `description` 字段注入 LLM 上下文 |
| 中文表名/列名 | ❌ 无特殊处理 | ✅ 中文 alias 搜索支持 |
| 搜索候选数量 | 几乎只有 1 个结果 | ✅ 返回最多 5 个候选，`select_dataset` 节点评分选优 |

---

## 二、背景

### 当前表名搜索的问题

`SearchDatasetsTool.run()` 执行的是精确等值匹配：

```python
# search_datasets.py 当前
query = db.session.query(SqlaTable).filter(
    SqlaTable.database_id == self._database_id,
    SqlaTable.table_name == table_name,   # ← 精确匹配
)
```

**典型失败场景**：

| 用户说 | LLM 猜测 target_table | 实际表名 | 结果 |
|--------|----------------------|---------|------|
| "分析订单情况" | "订单" | `dw_order_fact` | ❌ not_found |
| "查 order data" | "order" | `orders_2024` | ❌ not_found |
| "用 birth names 表" | "birth names" | `birth_names` | ❌ not_found（空格） |
| "销售流水" | "销售流水" | `sales_transaction` | ❌ not_found |

### 当前列信息的问题

`read_schema` 节点只读取列的技术属性，忽略 `description` 字段：

```python
# nodes_parent.py 当前 — 没有 description
columns = [
    {"name": col.column_name, "type": str(col.type),
     "groupable": col.groupby, "is_dttm": col.is_dttm}
    for col in table.columns
]
```

结果：`plan_query` 的 LLM 看到一堆列名如 `amt`、`qty`、`cnt`，无法判断哪个是"GMV"对应的列。

---

## 三、详细设计

### 3.1 多级表名搜索

将 `SearchDatasetsTool.run()` 改为四级搜索策略，按精确度降序：

```python
# search_datasets.py 新版搜索逻辑

def _search_table(
    self,
    table_name: str,
    accessible: list[SqlaTable],
) -> list[SqlaTable]:
    """Four-level fuzzy search, returns ranked candidates."""
    name_lower = table_name.lower().strip()

    # Level 1: Exact match
    exact = [t for t in accessible if t.table_name.lower() == name_lower]
    if exact:
        return exact[:1]

    # Level 2: Exact match on description / verbose_name
    by_desc = [
        t for t in accessible
        if t.description and name_lower in t.description.lower()
    ]
    if by_desc:
        return by_desc[:3]

    # Level 3: LIKE substring match (table_name contains keyword)
    substring = [
        t for t in accessible
        if name_lower in t.table_name.lower()
    ]
    if substring:
        return substring[:5]

    # Level 4: difflib similarity ≥ 0.4
    from difflib import SequenceMatcher
    scored = [
        (SequenceMatcher(None, name_lower, t.table_name.lower()).ratio(), t)
        for t in accessible
    ]
    scored = [(r, t) for r, t in scored if r >= 0.4]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:5]]
```

**返回格式变化**：

```python
# 当前：找不到时返回 not_found + available_datasets（全量）
# Phase 12 后：找不到时返回 not_found + 按相似度排序的候选

{
  "status": "not_found",
  "message": "未找到精确匹配，以下是最相近的数据集",
  "search_strategy": "similarity",    # 新增：说明使用了哪种搜索策略
  "available_datasets": [
    {"table_name": "dw_order_fact", "match_score": 0.67, "description": "订单事实表"},
    {"table_name": "order_detail",  "match_score": 0.52, "description": null},
    {"table_name": "order_items",   "match_score": 0.48, "description": null},
  ]
}
```

---

### 3.2 列业务注释注入

#### search_datasets.py 变更

在返回列信息时，包含 `description`：

```python
# search_datasets.py — 列信息构建，新增 description
columns = [
    {
        "name": col.column_name,
        "type": str(col.type),
        "groupable": col.groupby,
        "filterable": col.filterable,
        "is_dttm": col.is_dttm,
        "description": col.description or None,   # ← 新增
        "verbose_name": col.verbose_name or None,  # ← 新增（Superset 已有字段）
    }
    for col in table.columns
]
```

#### state.py 变更

```python
# state.py — SchemaSummary 新增
class SchemaSummary(TypedDict):
    ...
    column_descriptions: dict[str, str]   # col_name → description
    column_verbose_names: dict[str, str]  # col_name → verbose_name（中文别名）
```

#### nodes_parent.py 变更

`read_schema` 节点提取 `description` 和 `verbose_name`：

```python
# nodes_parent.py — read_schema 新增
column_descriptions = {
    col["name"]: col["description"]
    for col in columns
    if col.get("description")
}
column_verbose_names = {
    col["name"]: col["verbose_name"]
    for col in columns
    if col.get("verbose_name")
}

summary["column_descriptions"] = column_descriptions
summary["column_verbose_names"] = column_verbose_names
```

#### nodes_child.py 变更

在 `plan_query` prompt 中注入列业务描述：

```python
# nodes_child.py — PLAN_QUERY_PROMPT 新增
PLAN_QUERY_PROMPT = """\
Plan a SQL query for a chart. Return ONLY valid JSON.

Table: {table_name}
Intent: {analysis_intent} — {slice_name}
{sql_hint}

Available columns:
  time columns: {datetime_cols}
  dimension columns: {dimension_cols}
  metric columns: {metric_cols}
  saved metrics: {saved_metrics}

Column business descriptions (use these to map user intent to column names):
{column_descriptions_block}

{error_hint}
...
"""

# plan_query() 函数内
col_desc = summary.get("column_descriptions", {})
col_verbose = summary.get("column_verbose_names", {})
# 合并：优先 description，其次 verbose_name
all_desc = {**col_verbose, **col_desc}
col_desc_lines = "\n".join(
    f"  {col}: {desc}" for col, desc in list(all_desc.items())[:15]
) or "  (no business descriptions available)"

prompt = PLAN_QUERY_PROMPT.format(
    ...
    column_descriptions_block=col_desc_lines,
)
```

**LLM 看到的 prompt 示例**：

```
Column business descriptions:
  amt: 订单金额（含税，人民币元）
  qty: 商品件数
  uid: 用户唯一标识
  region: 大区名称（华北/华南/华东/西南/西北）
  status: 订单状态（pending/paid/refunded/cancelled）
```

---

### 3.3 搜索候选数量扩展与评分

当 `search_dataset` 节点返回多个候选时，`select_dataset` 节点需要更好的评分：

```python
# nodes_parent.py — select_dataset 评分扩展
for c in candidates:
    name = (c.get("table_name") or "").lower()
    desc = (c.get("description") or "").lower()
    score = c.get("match_score", 0) * 60   # 相似度得分（0-60）

    # 额外加分规则
    if name == target:
        score += 40    # 精确匹配加满分
    elif name.startswith(target):
        score += 25
    elif target in name:
        score += 15
    if target in desc:
        score += 10    # description 中包含目标词

    scored.append((score, c))
```

---

## 四、改动文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `superset/ai/tools/search_datasets.py` | 修改 | 四级搜索策略，列信息包含 `description`/`verbose_name` |
| `superset/ai/graph/state.py` | 修改 | `SchemaSummary` 新增 `column_descriptions`、`column_verbose_names` |
| `superset/ai/graph/nodes_parent.py` | 修改 | `read_schema` 提取列注释；`select_dataset` 评分扩展 |
| `superset/ai/graph/nodes_child.py` | 修改 | `PLAN_QUERY_PROMPT` 注入 `column_descriptions_block` |

---

## 五、数据流

### 模糊表名搜索流

```
用户："分析一下订单数据"
parse_request → goal.target_table = "订单数据"
search_dataset → SearchDatasetsTool.run({"table_name": "订单数据"})
  Level 1: 精确匹配 "订单数据" → 无
  Level 2: description 含 "订单数据" → 命中 dw_order_fact（description: "订单事实表"）? 无
  Level 3: "订单数据".lower() in table_name → 无
  Level 4: difflib → dw_order_fact(0.52), order_detail(0.48), order_items(0.41)
  返回: {"status": "not_found", "available_datasets": [3个候选]}
select_dataset → 评分选优 → dw_order_fact（得分最高）
  → "datasource_id" 不在候选中 → goto search_dataset（用精确表名重新搜索）
search_dataset → SearchDatasetsTool.run({"table_name": "dw_order_fact"})
  Level 1: 精确匹配 → 命中！
  返回: {"status": "found", "datasource_id": 3, "columns": [...]}
read_schema → 构建 SchemaSummary（含 column_descriptions）
plan_query → LLM 看到列注释，生成语义正确的 SQL
```

### 列注释对 SQL 生成的影响

```
之前（无注释）：
  LLM 看到：metric_cols: [amt, qty, cnt, revenue]
  生成：SELECT gender, SUM(amt) ...   ← 随机猜测

之后（有注释）：
  LLM 看到：
    amt: 订单金额（含税，人民币元）
    cnt: 订单笔数
  生成：SELECT region, SUM(amt) AS 销售额 ...  ← 正确理解
```

---

## 六、测试用例

### 单元测试

**文件**：`tests/unit_tests/ai/test_dataset_search.py`

```python
class TestSearchDatasetsFuzzy:
    def test_exact_match_level1(self):
        """精确匹配 Level 1"""
        ...

    def test_description_match_level2(self):
        """description 中包含关键词 Level 2"""
        table = MockTable(table_name="dw_fact", description="订单事实表")
        result = search(accessible=[table], query="订单")
        assert result[0].table_name == "dw_fact"

    def test_substring_match_level3(self):
        """子串匹配 Level 3"""
        table = MockTable(table_name="orders_2024", description=None)
        result = search(accessible=[table], query="order")
        assert result[0].table_name == "orders_2024"

    def test_similarity_match_level4(self):
        """difflib 相似度 Level 4"""
        table = MockTable(table_name="birth_names", description=None)
        result = search(accessible=[table], query="birth names")  # 有空格
        assert result[0].table_name == "birth_names"

    def test_no_match_returns_empty(self):
        """完全不相关返回空列表"""
        table = MockTable(table_name="birth_names", description=None)
        result = search(accessible=[table], query="xyz_irrelevant_table")
        assert len(result) == 0


class TestColumnDescriptionsInjection:
    def test_read_schema_extracts_descriptions(self):
        """read_schema 节点正确提取列注释"""
        state = {
            "selected_dataset": {
                "datasource_id": 1,
                "table_name": "orders",
                "columns": [
                    {"name": "amt", "type": "DECIMAL", "description": "订单金额",
                     "verbose_name": "金额", "groupable": False, "is_dttm": False},
                ],
                "metrics": [],
                "main_datetime_column": None,
            }
        }
        result = read_schema(state)
        summary = result.update["schema_summary"]
        assert summary["column_descriptions"]["amt"] == "订单金额"
        assert summary["column_verbose_names"]["amt"] == "金额"
```

### E2E 验证场景

**测试 1：模糊表名命中**

```
POST /chat/ {message: "分析 birth names 的数据", agent_type: "chart"}
验证：chart_created 事件出现（即使表名有空格也能找到 birth_names）
```

**测试 2：列注释改善 SQL**

```
前置：给 birth_names.num 列加注释 "出生人数"
POST /chat/ {message: "查出生人数最多的年份", agent_type: "nl2sql"}
验证：生成 SQL 中包含 num 列（而非随机猜测其他数值列）
```

---

## 七、Superset 管理界面提示

为了让 Phase 12 的列注释功能发挥最大效果，需要引导数据管理员在 Superset 数据集编辑界面填写列注释：

**路径**：Datasets → 编辑数据集 → Columns 标签页 → Description 列

建议添加一个 UI 提示横幅（可选改动）：

```
💡 填写列的 Description 可帮助 AI 更准确地理解和查询数据
```

---

## 八、后续规划（Phase 12 之后）

Phase 12 实现的是**基于规则的模糊搜索**，属于工程优化。

更进一步的**语义向量搜索**（Phase 3C in roadmap）需要：
- `sentence-transformers` 或 OpenAI embedding API
- pgvector 扩展或 Redis Vector 存储
- 数据集/列的向量化索引，`SqlaTable` post_save 信号触发更新

该能力预计在 Phase 15 作为独立阶段实施。
