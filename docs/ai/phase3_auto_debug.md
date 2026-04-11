# Phase 3 设计文档：自动排错（Debug Agent）

## 1. 背景与目标

### 当前状态
- Phase 1/2 已实现 NL2SQL 和 Chart 创建
- SQL 执行失败时用户需要手动排查错误

### 目标
1. 新增 DebugAgent：SQL 执行失败时 AI 自动诊断并修复
2. 前端错误区域新增 "AI Fix" 按钮
3. 修复后的 SQL 可一键应用到编辑器

---

## 2. 架构设计

### 2.1 新增文件

```
superset/ai/
  agent/
    debug_agent.py          # DebugAgent — 2 工具链
  prompts/
    debug.py                # 排错 prompt（含常见错误类型对照表）
```

### 2.2 修改文件

| 文件 | 改动 |
|---|---|
| `superset/ai/tools/execute_sql.py` | 错误输出增强，调用 `extract_errors()` |
| `superset/ai/schemas.py` | 新增 `agent_type: "debug"` |
| `superset/ai/commands/chat.py` | 注册 DebugAgent |
| `superset/ai/api.py` | AI_AGENT_DEBUG flag 检查 |
| `superset/ai/agent/events.py` | `error_fixed` 事件 |
| 前端 `ResultSet/index.tsx` | AiFixSection + AI Fix 按钮 |
| 前端 `SouthPane/Results.tsx` | 传递 error + sql props |
| 前端 `SouthPane/index.tsx` | 传递 error props |
| 前端 `SqlEditor/index.tsx` | onApplySql 回调链 |
| 前端 `types.ts` | error_fixed 事件类型 |
| 前端 `featureFlags.ts` | AI_AGENT_DEBUG 枚举 |
| `superset_config_docker.py` | AI_AGENT_DEBUG feature flag |

---

## 3. DebugAgent 设计

### 3.1 工具链

```python
class DebugAgent(BaseAgent):
    tools = [
        GetSchemaTool(database_id),   # 查看表结构（确认列名/类型）
        ExecuteSqlTool(database_id),  # 执行修复后的 SQL 验证
    ]
```

### 3.2 工作流

```
用户 SQL 执行失败，看到错误信息
    ↓ 点击 "AI Fix"
前端发送 { agent_type: "debug", message: 原始SQL, error: 错误信息 }
    ↓
DebugAgent system prompt 注入：
  - 原始 SQL
  - 错误信息（结构化）
  - 常见错误类型对照表
    ↓ LLM 分析
get_schema() → 确认列名和类型
    ↓ LLM 生成修复
返回修复后的 SQL + 解释
    ↓
前端展示修复后的 SQL + "Apply" 按钮
    ↓ 点击 "Apply"
SQL 替换到编辑器中
```

### 3.3 System Prompt 要点

```
你是一个 SQL 调试专家。用户执行 SQL 时遇到错误，请诊断并修复。

## 常见错误类型
| 错误模式 | 可能原因 | 修复方法 |
|---|---|---|
| column does not exist | 列名拼写错误/大小写 | 查 schema 确认列名 |
| syntax error | SQL 语法错误 | 检查关键字、括号 |
| function does not exist | 函数名错误 | 查数据库文档 |
| division by zero | 除零 | 添加 NULLIF |
| relation does not exist | 表名错误 | 查 schema 确认表名 |

## 输出格式
1. 错误原因分析
2. 修复后的完整 SQL（用 ```sql 代码块包裹）
3. 修复说明
```

---

## 4. 前端改动

### AI Fix 按钮

在 ResultSet 组件的错误区域新增按钮：

```
┌─────────────────────────────────────┐
│  Error: column "gendar" does not    │
│  exist                              │
│                                     │
│  [AI Fix ✨]                        │
└─────────────────────────────────────┘
```

点击后：
1. 显示 loading 状态
2. 调用 `/api/v1/ai/chat` with `agent_type: "debug"`
3. 展示修复后的 SQL 和说明
4. "Apply" 按钮将 SQL 写回编辑器

### 权限检查

AI Fix 按钮同时检查 `AI_AGENT` 和 `AI_AGENT_DEBUG` 两个 feature flag。

---

## 5. execute_sql 错误增强

`ExecuteSqlTool` 的错误输出改为结构化格式：

```python
# 之前
{"error": "string error message"}

# 之后
{
    "error": {
        "message": "column \"gendar\" does not exist",
        "error_type": "COLUMN_NOT_FOUND",
        "line": 3,
        "hint": "Perhaps you meant to reference the column \"gender\""
    }
}
```

调用 `db_engine_spec.extract_errors()` 解析数据库原生错误信息。

---

## 6. 测试验证

通过 SQL Lab 测试：
1. 输入含错误的 SQL（如拼写错误的列名）→ 执行失败
2. 点击 "AI Fix" → Agent 诊断错误
3. 返回修复后的 SQL → 点击 "Apply" 应用到编辑器
