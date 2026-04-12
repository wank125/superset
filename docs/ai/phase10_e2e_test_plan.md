# Phase 10：AI Agent E2E API 测试计划

> 生成日期：2026-04-12
> 目标：通过 HTTP API 端到端测试评估 AI Agent 系统各模式的效果和稳定性

---

## 一、测试背景

Phase 8-9 已完成，AI Agent 系统支持 4 种模式（nl2sql / chart / dashboard / debug），后端通过 Redis Streams 发送 11 种事件类型。需要通过 HTTP API 对整个系统进行端到端测试，评估各模式的实际效果。

### 系统架构

```
测试脚本 (Python requests)
    ↓ POST /api/v1/ai/chat/  (message + database_id + agent_type)
Superset Flask API
    ↓ dispatch Celery task
Celery Worker (StateGraph / LangChain)
    ↓ Redis Streams xadd
Superset Events API
    ↓ GET /api/v1/ai/events/?channel_id=xxx&last_id=yyy
测试脚本收集事件 → 自动评估 → 输出报告
```

### 执行路径

| Agent 类型 | 执行路径 | 主要事件 |
|---|---|---|
| nl2sql | LangChain ReAct | tool_call, tool_result, text_chunk, done |
| chart | StateGraph 管线 | thinking, sql_generated, data_analyzed, chart_created, done |
| dashboard | StateGraph 管线 | thinking, sql_generated, data_analyzed, chart_created×N, dashboard_created, done |
| debug | LangChain ReAct | tool_call, tool_result, text_chunk, done |

---

## 二、测试脚本设计

### 文件位置

`tests/ai/test_e2e_agent.py` — 单文件 Python 脚本

### 依赖

- `requests` — HTTP 请求
- `time`, `json`, `sys`, `dataclasses` — 标准库
- 无需额外安装（Superset Docker 环境中已有 requests）

### 脚本结构

```python
# 1. Config (dataclass)        — BASE_URL, USERNAME, PASSWORD, DATABASE_ID, TIMEOUT
# 2. AIAgentTestClient         — 认证 + 发送 + 轮询
# 3. classify_events()         — 按 type 分组事件
# 4. 10 个评估函数             — 每个用例的 pass/fail 逻辑
# 5. run_single_test()         — 执行单个用例
# 6. main()                    — 运行全部 + 输出报告
```

### 认证流程

```python
session = requests.Session()
resp = session.post(f"{BASE_URL}/login/", data={
    "username": USERNAME,
    "password": PASSWORD,
})
# session.cookies 包含 session cookie，后续请求自动携带
```

### 事件轮询逻辑

```python
def poll_events(session, channel_id, timeout):
    all_events = []
    last_id = "0"
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = session.get(f"{BASE_URL}/api/v1/ai/events/", params={
            "channel_id": channel_id,
            "last_id": last_id,
        })
        data = resp.json()
        events = data.get("events", [])
        last_id = data.get("last_id", last_id)
        all_events.extend(events)

        # 检查终止事件
        if any(e["type"] in ("done", "error") for e in events):
            break

        time.sleep(1)  # 1 秒轮询间隔

    return all_events
```

---

## 三、10 个测试用例

### 用例 1：NL2SQL 简单查询

| 项目 | 值 |
|---|---|
| **名称** | `nl2sql_simple` |
| **模式** | nl2sql |
| **输入** | "查询birth_names表前10行数据" |
| **超时** | 60s |

**预期行为**：Agent 调用 get_schema 获取表结构，生成 `SELECT ... FROM birth_names LIMIT 10`，调用 execute_sql 执行。

**评估标准**：
- [x] 终态为 `done`
- [x] 至少一次 `get_schema` tool_call
- [x] 至少一次 `execute_sql` tool_call
- [x] SQL 引用 `birth_names` 表
- [x] execute_sql 结果不含 "Error"

---

### 用例 2：NL2SQL 聚合查询

| 项目 | 值 |
|---|---|
| **名称** | `nl2sql_aggregation` |
| **模式** | nl2sql |
| **输入** | "统计birth_names每年男孩和女孩的总数" |
| **超时** | 60s |

**预期行为**：生成含 GROUP BY 和 SUM 的聚合 SQL。

**评估标准**：
- [x] 终态为 `done`
- [x] 至少一次 `execute_sql` tool_call
- [x] SQL 含 `GROUP BY`
- [x] SQL 含聚合函数（`SUM` / `COUNT`）
- [x] execute_sql 结果不含 "Error"

---

### 用例 3：NL2SQL 模糊输入处理

| 项目 | 值 |
|---|---|
| **名称** | `nl2sql_ambiguous` |
| **模式** | nl2sql |
| **输入** | "人生的意义是什么？" |
| **超时** | 60s |

**预期行为**：Agent 优雅处理非数据库问题，不崩溃，给出合理解释。

**评估标准**：
- [x] 终态为 `done`（不崩溃/超时）
- [x] 不生成幻觉 SQL（如 `SELECT * FROM meaning_of_life`）
- [x] 文本回复非空（>10 字符）

---

### 用例 4：Chart 柱状图

| 项目 | 值 |
|---|---|
| **名称** | `chart_bar` |
| **模式** | chart |
| **输入** | "用柱状图展示birth_names各性别的出生总数" |
| **超时** | 120s |

**预期行为**：StateGraph 管线完成搜索数据集 → 生成 SQL → 分析数据 → 选择柱状图类型 → 创建图表。

**评估标准**：
- [x] 终态为 `done`
- [x] `chart_created` 事件存在
- [x] chart_created 数据含 `chart_id`、`viz_type`、`explore_url`
- [x] `viz_type` 含 bar 关键字

---

### 用例 5：Chart 趋势折线图（智能选型）

| 项目 | 值 |
|---|---|
| **名称** | `chart_trend` |
| **模式** | chart |
| **输入** | "用折线图展示birth_names出生人数的年度趋势" |
| **超时** | 120s |

**预期行为**：智能选型应选择 timeseries 类型（line/area）。

**评估标准**：
- [x] 终态为 `done`
- [x] `chart_created` 事件存在
- [x] `viz_type` 为 timeseries 类型（`echarts_timeseries_line` / `echarts_timeseries_bar` 等）
- [x] `sql_generated` 事件含 GROUP BY 年份
- [x] `data_analyzed` 事件存在

---

### 用例 6：Chart 饼图

| 项目 | 值 |
|---|---|
| **名称** | `chart_pie` |
| **模式** | chart |
| **输入** | "用饼图展示birth_names按性别的比例分布" |
| **超时** | 120s |

**预期行为**：选择 pie 类型，SQL 按 gender 分组。

**评估标准**：
- [x] 终态为 `done`
- [x] `chart_created` 事件存在
- [x] `viz_type` 为 `pie`
- [x] `data_analyzed` 事件存在

---

### 用例 7：Dashboard 多图表仪表板

| 项目 | 值 |
|---|---|
| **名称** | `dashboard_multi` |
| **模式** | dashboard |
| **输入** | "创建birth_names仪表板：1) 性别分布饼图 2) 年度趋势折线图 3) 总记录数大数字" |
| **超时** | 180s |

**预期行为**：StateGraph 父图规划 3 张图表，子图循环创建，最后组装仪表板。

**评估标准**：
- [x] 终态为 `done`
- [x] ≥2 个 `chart_created` 事件（3 中至少成功 2 个）
- [x] `dashboard_created` 事件存在
- [x] dashboard_created 含 `dashboard_id`、`dashboard_url`、`chart_count ≥ 2`

---

### 用例 8：Debug SQL 错误修复

| 项目 | 值 |
|---|---|
| **名称** | `debug_fix` |
| **模式** | debug |
| **输入** | "SQL报错：column 'gender_typ' does not exist。原SQL：SELECT gender_typ, COUNT(*) FROM birth_names GROUP BY gender_typ。请修复。" |
| **超时** | 90s |

**预期行为**：Debug agent 调用 get_schema 查看表结构，找到正确列名，生成修复 SQL 并执行验证。

**评估标准**：
- [x] 终态为 `done`
- [x] `get_schema` 被调用
- [x] `execute_sql` 被调用（验证修复）
- [x] 修复后 execute_sql 结果不含 "Error"
- [x] 文本回复解释了修复内容

---

### 用例 9：边界 — 无效 Agent 类型

| 项目 | 值 |
|---|---|
| **名称** | `edge_invalid_type` |
| **模式** | "invalid_type" |
| **输入** | "Show me data" |
| **超时** | 10s |

**预期行为**：API 直接返回 400，不派发 Celery 任务。

**评估标准**：
- [x] HTTP 状态码为 400（不是 500）
- [x] 响应体含错误信息

---

### 用例 10：边界 — 空消息

| 项目 | 值 |
|---|---|
| **名称** | `edge_empty_message` |
| **模式** | nl2sql |
| **输入** | "" |
| **超时** | 10s |

**预期行为**：Schema 校验失败，API 返回 400。

**评估标准**：
- [x] HTTP 状态码为 400
- [x] 响应体含错误信息

---

## 四、评估维度

| 维度 | 权重 | 说明 |
|---|---|---|
| **完成率** | 30% | 终态为 `done` 而非 `error` |
| **SQL 正确性** | 30% | 引用正确表名/列名、语法正确、执行无错 |
| **工具调用合理性** | 15% | 预期工具被调用、顺序正确 |
| **图表类型匹配** | 15% | viz_type 符合用户意图 |
| **响应延迟** | 10% | 仅记录参考，不影响 pass/fail |

### 图表类型期望映射

| 用户意图 | 期望 viz_type |
|---|---|
| 柱状图/比较 | `echarts_timeseries_bar` |
| 折线图/趋势 | `echarts_timeseries_line`, `echarts_area` |
| 饼图/分布 | `pie` |
| 大数字/KPI | `big_number_total`, `big_number` |
| 表格/明细 | `table` |

### 响应时间记录

- `t_start`: POST /chat/ 的时间
- `t_first_event`: 收到第一个事件的时间
- `t_terminal`: 收到 done/error 的时间
- `total_duration = t_terminal - t_start`
- `time_to_first_event = t_first_event - t_start`

---

## 五、输出格式

### 控制台输出（彩色）

```
=============================================================================
  AI Agent E2E API Test Report
  Base URL: http://localhost:8088 | Database: 1 | Time: 2026-04-12
=============================================================================

  1/10  nl2sql_simple          [PASS]   8.1s   SQL OK, execute OK
  2/10  nl2sql_aggregation     [PASS]  12.4s   GROUP BY + SUM OK
  3/10  nl2sql_ambiguous       [PASS]   6.3s   Handled gracefully
  4/10  chart_bar              [PASS]  22.1s   viz: echarts_timeseries_bar
  5/10  chart_trend            [FAIL]  45.3s   viz: pie (expected timeseries)
  6/10  chart_pie              [PASS]  18.9s   viz: pie
  7/10  dashboard_multi        [PASS]  78.5s   Charts: 3/3, Dashboard OK
  8/10  debug_fix              [PASS]  18.7s   Fix: gender_typ → gender
  9/10  edge_invalid_type      [PASS]   0.1s   400 returned
 10/10  edge_empty_message     [PASS]   0.1s   400 returned

=============================================================================
  PASS 9/10 (90%)
  nl2sql:  3/3  |  chart:  2/3  |  dashboard: 1/1  |  debug: 1/1  |  edge: 2/2
  Avg latency: 23.5s
=============================================================================
```

### JSON 日志

写入 `test_results_{timestamp}.json`，结构：

```json
{
  "meta": {
    "timestamp": "2026-04-12T14:30:00",
    "base_url": "http://localhost:8088",
    "database_id": 1,
    "passed": 9,
    "failed": 1
  },
  "tests": [
    {
      "name": "nl2sql_simple",
      "agent_type": "nl2sql",
      "message": "查询birth_names表前10行数据",
      "passed": true,
      "duration_seconds": 8.1,
      "time_to_first_event": 2.3,
      "event_count": 12,
      "event_type_counts": {"thinking": 3, "tool_call": 4, ...},
      "evaluation_reasons": ["SQL references birth_names", "execute OK"],
      "events": [...]
    }
  ]
}
```

---

## 六、运行方式

### 前置条件

1. Docker Compose 运行中（superset + worker + redis + db）
2. LM Studio 已加载 GLM-4.7-Flash（端口 1234）
3. 示例数据已加载（birth_names 表存在于 database_id=1）
4. AI 功能已启用（superset_config.py 中 FEATURE_FLAGS）

### 运行命令

```bash
python tests/ai/test_e2e_agent.py \
  --base-url http://localhost:8088 \
  --username admin \
  --password admin \
  --database-id 1
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--base-url` | `http://localhost:8088` | Superset 服务地址 |
| `--username` | `admin` | 登录用户名 |
| `--password` | `admin` | 登录密码 |
| `--database-id` | `1` | 目标数据库 ID |
| `--timeout` | `180` | 默认超时（秒），dashboard 用例自动使用 180s |
| `--output-dir` | `.` | JSON 日志输出目录 |
| `--test` | （全部） | 指定运行的测试用例名，逗号分隔 |

---

## 七、注意事项

1. **幂等性**：CreateChartTool 有 10 分钟复用窗口，重复运行同一用例会返回复用结果
2. **LLM 不确定性**：GLM-4.7-Flash 输出非确定性，同一用例多次运行结果可能不同
3. **Dashboard 超时**：多图表仪表板可能需要 60-90s，设置 180s 超时
4. **两种执行路径**：nl2sql/debug 走 LangChain ReAct，chart/dashboard 走 StateGraph，事件类型不同
5. **Celery Worker 必须运行**：chat 端点只派发任务，实际执行在 worker 中
