# Phase 15：AI 大管家模式（Superset Copilot）

> 生成日期：2026-04-12
> 定位：将 AI 对话框从"数据查询助手"升级为"Superset 全局大管家"

---

## 一、目标与定位

### 核心理念

用户点击 N 次才能找到的信息，用一句话问 AI 直接拿到。

```
之前：Datasets → 搜索 → 找到 birth_names → 点进去 → 看 Metrics 页
之后："birth_names 有哪些指标定义？"

之前：Alerts & Reports → 过滤 → 找到"日报" → 看状态
之后："我的日报最近执行成功了吗？"

之前：SQL Lab → History → 翻看
之后："我上周跑过哪些超过 5 秒的 SQL？"
```

### 能力边界

**Phase 15 范围（以读为主）**：

| ✅ 做 | ❌ 不做 |
|-------|--------|
| 查询所有 Superset 资产状态 | 修改 Schema/数据库结构 |
| 回答"有多少/哪些/什么状态" | 删除资产 |
| 解释图表/仪表板配置 | 用户权限变更 |
| 执行数据查询并分析 | 发布报告/触发调度 |
| 告警状态/执行历史 | 创建图表（Phase 11-14 范围） |

---

## 二、Superset 可读 API 全量梳理

### 2.1 数据资产类

| 资源 | 关键 GET API | 能回答的问题 |
|------|------------|------------|
| **数据库连接** | `GET /api/v1/database/` | "我有哪些数据库连接？各自的状态？" |
| | `GET /api/v1/database/{id}/` | "这个数据库是什么引擎？" |
| | `GET /api/v1/database/{id}/tables/` | "这个数据库有哪些表？" |
| | `GET /api/v1/database/{id}/schemas/` | "这个数据库有哪些 schema？" |
| **数据集** | `GET /api/v1/dataset/` | "我有哪些 datasets？哪些最近被用过？" |
| | `GET /api/v1/dataset/{id}/` | "这个 dataset 有哪些列和指标？" |
| | `GET /api/v1/dataset/{id}/related_objects/` | "哪些图表用了这个 dataset？" |
| **图表** | `GET /api/v1/chart/` | "我有哪些图表？按最近修改排序？" |
| | `GET /api/v1/chart/{id}/` | "这个图表的配置是什么？" |
| | `POST /api/v1/chart/{id}/data/` | "把这个图表的数据导出给我" |
| **仪表板** | `GET /api/v1/dashboard/` | "我有哪些 dashboard？" |
| | `GET /api/v1/dashboard/{id}/` | "这个 dashboard 里有哪些图表？" |
| | `GET /api/v1/dashboard/{id}/charts/` | "仪表板的图表清单" |
| | `GET /api/v1/dashboard/{id}/datasets/` | "仪表板依赖哪些 datasets？" |

### 2.2 查询与历史类

| 资源 | 关键 GET API | 能回答的问题 |
|------|------------|------------|
| **查询历史** | `GET /api/v1/query/` | "我最近跑了哪些 SQL？" |
| | filter: `status`, `elapsed_time` | "上周有哪些慢查询（>5s）？" |
| | filter: `user_id`, `database_id` | "今天失败的查询有哪些？" |
| **保存的查询** | `GET /api/v1/saved_query/` | "我保存了哪些 SQL？" |
| | `GET /api/v1/saved_query/{id}/` | "把这个保存的 SQL 给我看看" |

### 2.3 调度与告警类

| 资源 | 关键 GET API | 能回答的问题 |
|------|------------|------------|
| **告警 & 报告** | `GET /api/v1/report/` | "我有哪些定时报告？" |
| | `GET /api/v1/report/{id}/` | "这个报告的调度是什么频率？" |
| | `GET /api/v1/report/{id}/log/` | "这个报告最近的执行记录？" |
| | filter: `active`, `last_state` | "有没有失败的告警？" |

### 2.4 用户与权限类

| 资源 | 关键 GET API | 能回答的问题 |
|------|------------|------------|
| **当前用户** | `GET /api/v1/me/` | "我是谁？我有什么角色？" |
| | `GET /api/v1/me/roles/` | "我有哪些权限角色？" |
| **标签** | `GET /api/v1/tag/` | "有哪些标签？" |
| | `GET /api/v1/tag/{id}/tagged_objects/` | "这个标签下有哪些图表/仪表板？" |

### 2.5 SQL Lab 状态类

| 资源 | 关键 GET API | 能回答的问题 |
|------|------------|------------|
| **SQL Lab** | `GET /api/v1/sqllab/` | "当前有哪些运行中的查询？" |
| **异步事件** | `GET /api/v1/async_event/` | "后台任务状态" |

---

## 三、架构设计

### 3.1 新增 Agent Mode：`copilot`

```python
# commands/chat.py
_AGENT_MAP = {
    "nl2sql": NL2SQLAgent,
    "chart": ChartAgent,
    "dashboard": DashboardAgent,
    "debug": DebugAgent,
    "copilot": CopilotAgent,     # ← 新增
}
```

### 3.2 工具集设计

```
CopilotAgent（12 个工具）
├── 数据查询工具（继承 NL2SQL）
│   ├── GetSchemaTool          ← 数据库元数据
│   ├── ExecuteSqlTool         ← 执行 SQL
│   └── SearchDatasetsTool     ← 搜索 dataset
├── 资产查询工具（新增）
│   ├── ListDatabasesTool      ← 数据库连接列表
│   ├── GetDatasetDetailTool   ← dataset 完整配置
│   ├── ListChartsTool         ← 图表列表（可过滤）
│   ├── GetChartDetailTool     ← 图表配置
│   ├── ListDashboardsTool     ← 仪表板列表
│   └── GetDashboardDetailTool ← 仪表板内容
├── 历史查询工具（新增）
│   ├── QueryHistoryTool       ← SQL 执行历史
│   └── SavedQueryTool         ← 保存的 SQL
├── 调度工具（新增）
│   └── ReportStatusTool       ← 告警/报告状态 + 执行记录
└── 身份工具（新增）
    └── WhoAmITool             ← 当前用户信息和权限
```

---

## 四、工具实现详细设计

### 4.1 BaseSupersetApiTool（共用基类）

```python
# superset/ai/tools/superset_api_base.py（新文件）

class BaseSupersetApiTool(BaseTool):
    """Base class for Superset internal API tools.

    Wraps _run() with unified error handling and JSON serialization.
    """
    _MAX_RESULTS = 20

    def run(self, arguments: dict[str, Any]) -> str:
        try:
            result = self._run(arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except PermissionError as exc:
            return json.dumps({"error": "permission_denied", "message": str(exc)})
        except Exception as exc:
            return json.dumps({"error": "tool_error", "message": str(exc)})

    def _run(self, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError
```

---

### 4.2 数据库连接工具

```python
# superset/ai/tools/list_databases.py（新文件）

class ListDatabasesTool(BaseSupersetApiTool):
    name = "list_databases"
    description = (
        "List database connections available in Superset. "
        "Returns engine type and configuration summary."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "Filter by name keyword"},
        },
    }

    def _run(self, arguments):
        from superset.daos.database import DatabaseDAO
        from superset.extensions import security_manager

        databases = DatabaseDAO.find_all()
        result = []
        for db_obj in databases:
            if not security_manager.can_access_database(db_obj):
                continue
            search = arguments.get("search", "").lower()
            if search and search not in db_obj.database_name.lower():
                continue
            result.append({
                "id": db_obj.id,
                "name": db_obj.database_name,
                "engine": db_obj.backend,
                "expose_in_sqllab": db_obj.expose_in_sqllab,
            })
        return {"databases": result[:self._MAX_RESULTS], "total": len(result)}
```

---

### 4.3 Dataset 详情工具

```python
# superset/ai/tools/get_dataset_detail.py（新文件）

class GetDatasetDetailTool(BaseSupersetApiTool):
    name = "get_dataset_detail"
    description = (
        "Get full details of a Superset dataset: columns, metrics, "
        "filters, caching config, and which charts use it."
    )
    parameters_schema = {
        "type": "object",
        "required": ["dataset_id"],
        "properties": {
            "dataset_id": {"type": "integer"},
            "include_charts": {
                "type": "boolean",
                "description": "Include charts using this dataset",
            },
        },
    }

    def _run(self, arguments):
        from superset.daos.dataset import DatasetDAO
        from superset.extensions import security_manager

        ds = DatasetDAO.find_by_id(arguments["dataset_id"])
        if not ds:
            return {"error": "Dataset not found"}
        if not security_manager.can_access_datasource(ds):
            raise PermissionError(f"No access to dataset {ds.table_name}")

        result = {
            "id": ds.id,
            "table_name": ds.table_name,
            "schema": ds.schema,
            "database": ds.database.database_name,
            "description": ds.description,
            "main_datetime_col": ds.main_dttm_col,
            "cache_timeout_seconds": ds.cache_timeout,
            "columns": [
                {
                    "name": c.column_name,
                    "type": str(c.type),
                    "description": c.description,
                    "is_filterable": c.filterable,
                    "is_groupable": c.groupby,
                    "is_datetime": c.is_dttm,
                }
                for c in ds.columns[:30]
            ],
            "metrics": [
                {
                    "name": m.metric_name,
                    "expression": m.expression,
                    "description": m.description,
                }
                for m in ds.metrics
            ],
        }

        if arguments.get("include_charts"):
            from superset.models.slice import Slice
            charts = (
                Slice.query.filter_by(datasource_id=ds.id)
                .order_by(Slice.changed_on.desc())
                .limit(10).all()
            )
            result["charts_using_this_dataset"] = [
                {"id": c.id, "name": c.slice_name, "viz_type": c.viz_type}
                for c in charts
            ]

        return result
```

---

### 4.4 查询历史工具

```python
# superset/ai/tools/query_history.py（新文件）

class QueryHistoryTool(BaseSupersetApiTool):
    name = "query_history"
    description = (
        "Search SQL query execution history. Filter by status "
        "(success/failed/running), time range, or elapsed seconds. "
        "Useful for finding slow queries, errors, or recent activity."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "failed", "running", "timed_out"],
            },
            "min_elapsed_seconds": {
                "type": "number",
                "description": "Minimum execution time in seconds",
            },
            "days_ago": {"type": "integer", "description": "Look back N days"},
            "limit": {"type": "integer"},
        },
    }

    def _run(self, arguments):
        from flask import g
        from superset.models.sql_lab import Query
        from superset import db
        from datetime import datetime, timedelta, timezone

        days_ago = arguments.get("days_ago", 7)
        since = datetime.now(timezone.utc) - timedelta(days=days_ago)

        q = db.session.query(Query).filter(
            Query.user_id == g.user.id,
            Query.start_time >= since,
        )

        if arguments.get("status"):
            q = q.filter(Query.status == arguments["status"])

        queries = q.order_by(Query.start_time.desc()).limit(
            min(arguments.get("limit", 20), 50)
        ).all()

        result_list = []
        for query in queries:
            elapsed = None
            if query.end_time and query.start_time:
                elapsed = round(
                    (query.end_time - query.start_time).total_seconds(), 2
                )
            # Apply elapsed filter in Python (avoids DB-specific time diff syntax)
            min_sec = arguments.get("min_elapsed_seconds")
            if min_sec and (elapsed is None or elapsed < min_sec):
                continue
            result_list.append({
                "id": query.id,
                "sql_preview": (query.sql or "")[:200],
                "status": query.status,
                "database": query.database.database_name if query.database else None,
                "elapsed_seconds": elapsed,
                "rows_returned": query.rows,
                "start_time": str(query.start_time),
                "error_message": query.error_message,
            })

        return {"queries": result_list, "total_found": len(result_list)}
```

---

### 4.5 报告/告警状态工具

```python
# superset/ai/tools/report_status.py（新文件）

class ReportStatusTool(BaseSupersetApiTool):
    name = "report_status"
    description = (
        "Check status of alerts and scheduled reports. "
        "Returns execution history, last success/failure time, and schedule."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "type": {"type": "string", "enum": ["alert", "report"]},
            "only_failed": {"type": "boolean"},
            "include_logs": {"type": "boolean"},
        },
    }

    def _run(self, arguments):
        from superset.daos.report import ReportScheduleDAO
        from superset.extensions import security_manager

        reports = ReportScheduleDAO.find_all()
        result = []

        for r in reports:
            if not security_manager.can_access("can_read", "ReportSchedule"):
                continue
            if arguments.get("search") and arguments["search"].lower() not in r.name.lower():
                continue
            if arguments.get("type") and r.type != arguments["type"]:
                continue

            last_log = r.logs[0] if r.logs else None
            last_state = last_log.state if last_log else None

            if arguments.get("only_failed") and last_state != "error":
                continue

            entry = {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "active": r.active,
                "schedule": r.crontab,
                "last_state": last_state,
                "last_run": str(last_log.start_dttm) if last_log else None,
                "recipients": [rec.type for rec in r.recipients],
            }

            if arguments.get("include_logs"):
                entry["recent_logs"] = [
                    {
                        "state": log.state,
                        "start": str(log.start_dttm),
                        "end": str(log.end_dttm),
                        "error": log.error_message,
                    }
                    for log in r.logs[:5]
                ]

            result.append(entry)

        return {"reports": result[:self._MAX_RESULTS], "total": len(result)}
```

---

### 4.6 身份工具

```python
# superset/ai/tools/whoami.py（新文件）

class WhoAmITool(BaseSupersetApiTool):
    name = "whoami"
    description = (
        "Get information about the current user: name, roles, "
        "and accessible databases."
    )
    parameters_schema = {"type": "object", "properties": {}}

    def _run(self, arguments):
        from flask import g
        from superset.extensions import security_manager

        user = g.user
        if not user:
            return {"error": "Not authenticated"}

        roles = [r.name for r in user.roles]

        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "roles": roles,
            "is_admin": "Admin" in roles,
            "can_create_charts": security_manager.can_access("can_write", "Chart"),
            "can_create_dashboards": security_manager.can_access("can_write", "Dashboard"),
        }
```

---

## 五、CopilotAgent 实现

```python
# superset/ai/agent/copilot_agent.py（新文件）

from superset.ai.agent.base import BaseAgent
from superset.ai.prompts.copilot import COPILOT_SYSTEM_PROMPT
from superset.ai.tools.execute_sql import ExecuteSqlTool
from superset.ai.tools.get_schema import GetSchemaTool
from superset.ai.tools.search_datasets import SearchDatasetsTool
from superset.ai.tools.list_databases import ListDatabasesTool
from superset.ai.tools.get_dataset_detail import GetDatasetDetailTool
from superset.ai.tools.list_charts import ListChartsTool
from superset.ai.tools.list_dashboards import ListDashboardsTool
from superset.ai.tools.get_dashboard_detail import GetDashboardDetailTool
from superset.ai.tools.query_history import QueryHistoryTool
from superset.ai.tools.saved_query import SavedQueryTool
from superset.ai.tools.report_status import ReportStatusTool
from superset.ai.tools.whoami import WhoAmITool


class CopilotAgent(BaseAgent):
    """Superset Copilot — answers questions about the entire Superset instance."""

    def __init__(self, provider, context, database_id, schema_name=None):
        tools = [
            # SQL & 数据查询
            GetSchemaTool(database_id=database_id, default_schema=schema_name),
            ExecuteSqlTool(database_id=database_id),
            SearchDatasetsTool(database_id=database_id, schema_name=schema_name),
            # 资产查询
            ListDatabasesTool(),
            GetDatasetDetailTool(),
            ListChartsTool(),
            ListDashboardsTool(),
            GetDashboardDetailTool(),
            # 历史
            QueryHistoryTool(),
            SavedQueryTool(),
            # 调度/告警
            ReportStatusTool(),
            # 身份
            WhoAmITool(),
        ]
        super().__init__(provider, context, tools)

    def get_system_prompt(self) -> str:
        return COPILOT_SYSTEM_PROMPT
```

---

## 六、System Prompt

```
You are the Superset Copilot — a knowledgeable assistant for the entire
Apache Superset BI platform. You help users find information, understand
their data assets, and navigate the platform through natural language.

## Your capabilities

1. Data exploration: Query databases, explore schemas, analyze data
2. Asset discovery: Find datasets, charts, dashboards by name or topic
3. Status monitoring: Check report schedules, alert states, query history
4. User context: Answer who has access to what, explain permissions
5. SQL history: Find past queries, identify slow or failed queries

## Tools
- get_schema / execute_sql: Database inspection and SQL execution
- search_datasets / get_dataset_detail: Dataset metadata and details
- list_databases: All database connections
- list_charts / list_dashboards / get_dashboard_detail: Asset inventory
- query_history: SQL execution history (filter by status/time/speed)
- saved_query: User's saved SQL
- report_status: Alert and report execution status (+ logs)
- whoami: Current user identity and permissions

## Rules
1. Always check permissions — never expose inaccessible data
2. Be concise — summarize results, don't dump raw JSON
3. Proactive suggestions — after answering, suggest 1-2 related follow-ups
4. Respond in user's language (Chinese / English)
5. Read-only — for write operations, explain impact and ask confirmation
```

---

## 七、典型交互场景

### 资产巡查
```
用户：我 Superset 里有多少图表？都是什么类型的？
AI: 调用 list_charts → 共 47 个图表：折线图 15、柱状图 12、饼图 8、表格 7……
    最近修改：销售趋势图（2小时前）

用户：销售趋势图用的哪个 dataset？
AI: dw_order_fact（dataset_id=3）

用户：dw_order_fact 有哪些指标？
AI: 5 个指标：gmv、转化率、DAU……12 个图表在使用它
```

### 运维巡检
```
用户：有没有失败的定时报告？
AI: ❌ 日销售日报 — 连续 3 次失败（SMTP 超时）
    ❌ 库存预警 — 3 天前失败（查询超时）

用户：日销售日报几点发？
AI: 每天 8:00（cron: 0 8 * * *），收件人：Email
```

### 性能排查
```
用户：上周有没有超过 30 秒的慢查询？
AI: 找到 4 条：
    - SELECT * FROM dw_order_fact (45.2s, 成功)
    - WITH cte AS ... (38.7s, 成功)
    - SELECT region, SUM(amount)... (31.1s, 超时失败)

用户：第 3 条 SQL 是什么问题？
AI: 全表扫描 4 年数据（2020-2024），建议添加分区过滤条件
```

### 权限自查
```
用户：我有创建图表的权限吗？
AI: 调用 whoami → 你是 Alpha 角色，有 can_write/Chart 权限，可创建图表
    没有 Admin 权限，无法删除他人图表
```

---

## 八、改动文件清单

| 文件 | 类型 |
|------|------|
| `superset/ai/tools/superset_api_base.py` | 新建（共用基类） |
| `superset/ai/tools/list_databases.py` | 新建 |
| `superset/ai/tools/get_dataset_detail.py` | 新建 |
| `superset/ai/tools/list_charts.py` | 新建 |
| `superset/ai/tools/list_dashboards.py` | 新建 |
| `superset/ai/tools/get_dashboard_detail.py` | 新建 |
| `superset/ai/tools/query_history.py` | 新建 |
| `superset/ai/tools/saved_query.py` | 新建 |
| `superset/ai/tools/report_status.py` | 新建 |
| `superset/ai/tools/whoami.py` | 新建 |
| `superset/ai/agent/copilot_agent.py` | 新建 |
| `superset/ai/prompts/copilot.py` | 新建 |
| `superset/ai/commands/chat.py` | 修改（注册 copilot 类型） |
| `superset/ai/api.py` | 修改（feature flag 检查） |
| `superset/ai/config.py` | 修改（`use_copilot()` 开关） |

**估计工作量**：10 个新工具（每个约 50-80 行）+ 1 个 Agent + Prompt，共约 2-3 周。

---

## 九、权限设计

```
Layer 1: Feature Flag  → AI_AGENT_COPILOT = True
Layer 2: Resource 级   → security_manager.can_access("can_read", "Chart")
Layer 3: Object 级     → security_manager.can_access_datasource(dataset)

非 Admin 用户：
  - query_history 只看自己的查询
  - 其他工具过滤掉无权访问的资产
```

---

## 十、配置

```python
# superset/config.py
FEATURE_FLAGS = {
    "AI_AGENT_COPILOT": False,   # 独立开关
}

# superset/ai/config.py 新增
def use_copilot() -> bool:
    return bool(get_ai_config("AI_AGENT_COPILOT", False))
```
