# 当前产品功能全景 vs. AgentBI 愿景差距分析

> 分析时间：2026-04-15  
> 代码库：`feature/agentbi-merge` 分支

---

## 一、当前已实现功能清单

### 1. 对话式数据分析 — 核心路径

系统采用 **StateGraph 双图架构**（图表图 + 仪表板图），由 7 种 Agent 模式覆盖不同场景：

| Agent 模式 | 触发方式 | 功能概述 |
|-----------|---------|---------|
| `nl2sql` | 默认 / fallback | 自然语言 → SQL 执行 → Markdown 输出 |
| `chart` | 关键词 / 手动 | SQL → 智能图表（含内嵌渲染） |
| `dashboard` | 关键词 / 手动 | 多图表自动组装仪表板 |
| `copilot` | 关键词 / 手动 | 平台管理：图表/仪表板/权限/日志查询 |
| `debug` | 关键词 / 手动 | SQL 错误自动修复 |
| `alert` | 手动 | 自然语言 → 告警规则 AI 生成（独立 API） |
| `auto` | 前端默认 | LLM + 关键词双层路由自动分发 |

---

### 2. 后端 AI 能力模块

#### 2.1 StateGraph 图表生成流水线

```
请求 → classify_intent
       ↓
      [修改已有图表?] → load_existing_chart → apply_chart_modification → update_chart
       ↓
parse_request → search_dataset（多库关键词匹配）
       ↓
select_dataset（RBAC 过滤 + LLM 确认）
       ↓
[需要澄清?] → clarify_user（结构化选项澄清）
       ↓
read_schema（带列描述/业务指标注入）
       ↓
[仪表板?] → plan_dashboard → review_analysis → [plan/direct] → single_chart_subgraph × N → create_dashboard
       ↓
sub: plan_query（SQL 生成）
       ↓
     validate_sql（静态检查 + 字段校验）→ [失败] → plan_query（最多 3 次）
       ↓
     execute_query → [失败] → plan_query
       ↓
     analyze_result（KPI 判断 + LLM 洞察 + statistics 生成 + 推荐问题）
       ↓
     select_chart（图表类型 + 参数规格）
       ↓
     normalize_chart_params → [失败] → repair_chart_params（LLM 修复）
       ↓
     create_chart（幂等校验 + 10 分钟去重）
```

#### 2.2 工具集（18 个 Tools）

| 工具名 | 功能 | Agent 归属 |
|--------|------|-----------|
| `list_databases` | 列出可用数据库 | Copilot |
| `get_schema` | 获取表/列结构（含列描述、业务指标） | NL2SQL / Chart |
| `search_datasets` | 语义匹配数据集 | Chart / Dashboard |
| `execute_sql` | 安全执行 SQL（RBAC + 注入防护） | NL2SQL / Chart |
| `analyze_data` | 列类型推断 + 适用性打分 | Chart |
| `create_chart` | 创建 Superset 图表（幂等） | Chart |
| `create_dashboard` | 动态宽度布局仪表板 | Dashboard |
| `get_chart_detail` | 查询图表详情 | Copilot |
| `get_dashboard_detail` | 查询仪表板详情 | Copilot |
| `get_dataset_detail` | 查询数据集详情 | Copilot |
| `list_charts` | 列出图表列表 | Copilot |
| `list_dashboards` | 列出仪表板列表 | Copilot |
| `query_history` | 查询历史记录 | Copilot |
| `saved_query` | 已保存查询管理 | Copilot |
| `report_status` | 告警/报告状态查询 | Copilot |
| `whoami` | 当前用户权限信息 | Copilot |
| `embed_dashboard` | 生成嵌入式仪表板链接 | Copilot |
| `(alert/generate)` | 告警规则 AI 生成（独立 REST API） | Alert |

#### 2.3 图表类库（`chart_types/catalog.py`）

支持 **15+ 图表类型**，含 `default_width` 自动布局权重：

| 分类 | 图表类型 | Dashboard 宽度 |
|------|---------|--------------|
| 时序类 | echarts_timeseries、timeseries_bar、smooth、area、step、scatter | 6 |
| 分类类 | pie、bar_basic | 4/6 |
| KPI 类 | big_number_total、big_number、gauge_chart | 3 |
| 表格类 | table、pivot_table_v2 | 12 |
| 分布类 | histogram_v2 | 6 |

#### 2.4 业务指标语义层（`metric_catalog.py`）

- YAML 驱动的业务指标词典（`metric_catalog.yaml`）
- 支持字段：SQL 表达式、表名通配符、别名/同义词、聚合类型、展示单位
- 在 `plan_query` prompt 中注入，提升 SQL 语义准确性
- 在 `read_schema` 阶段注入列描述和指标定义

#### 2.5 智能路由系统（`router/`）

- **关键词路由**（`rules.py`）：双层置信度（high=0.90、low=0.60），涵盖 chart/dashboard/copilot/debug 四类关键词
- **LLM 路由**（`llm_classifier.py`）：关键词不确定时 LLM 兜底
- **续话检测**：识别「这个」「继续」等续话词，复用上轮 Agent
- 前端实时展示路由决策（`intent_routed` 事件）

#### 2.6 多轮对话记忆（LangGraph Checkpointer）

- 基于 `session_id` 的 LangGraph Checkpointer
- 对话历史摘要注入 `parse_request` → LLM 理解上下文
- 会话独立（不同 session_id 隔离）

#### 2.7 分析计划确认流（Phase 19a）

- 置信度 ≥ 0.5 → 直接执行
- 置信度 < 0.5 → 先展示结构化分析计划（数据集、指标、维度、图表规格列表、风险假设）
- 用户确认后继续，支持自然语言修改计划

#### 2.8 告警规则 AI 生成（`alert/api.py`）

- `POST /api/v1/ai/alert/generate/`
- 自然语言 → SQL 查询 + 触发条件（operator/not_null/AI）+ CRON 计划
- SQL 安全校验（SELECT only）
- 前端 `AlertConfigCard` 一键创建 Superset Alert

#### 2.9 Supersonic 集成准备（`semantic/`）

- `SuperSonicClient`：完整 HTTP 客户端（GET/POST + HMAC 认证支持）
- `model_mapping.py`：语义模型字段映射
- ⚠️ 当前为**备用路径**，未激活（`SUPERSONIC_BASE_URL` 未配置）

---

### 3. 前端功能模块

#### 3.1 聊天工作区（双入口）

| 组件 | 描述 |
|------|------|
| `AiWorkspace.tsx` | 大屏工作区（左侧会话列表 + 右侧对话区） |
| `AiChatDrawer.tsx` | 侧边抽屉式入口（悬浮按钮触发） |
| `AiChatPanel.tsx` | 紧凑型聊天面板（含 Agent 模式切换） |

#### 3.2 多会话管理

- `useAiSessions.ts`：localStorage 持久化会话列表
- `AiSessionSidebar.tsx`：会话历史，支持新建/切换/删除
- `AiNewSessionModal.tsx`：新建会话（选数据库 + Agent 类型）

#### 3.3 实时流式响应

- Server-Sent Events 长轮询（500ms 间隔，最大 180s）
- `AiStreamingText.tsx`：逐字打字机效果
- `AiStepProgress.tsx`：实时步骤进度条（已完成 / 进行中 / 错误）
- `AiSqlPreview.tsx`：SQL 生成预览（语法高亮）

#### 3.4 内嵌图表渲染（Phase 1 Merge 成果）

- `AiInlineChart.tsx`：自动推断图表类型并渲染
  - `KpiCard.tsx`：KPI 卡，环比/同比涨红跌绿（`PeriodCompareItem.tsx`）
  - `TrendChart.tsx`：多指标折线图（平滑曲线）
  - `BarChart.tsx`：多维度柱状图（标签旋转）
  - `PieChart.tsx`：环形饼图（hover label）
  - `DataTable.tsx`：粘性表头、行 hover、分类列下钻点击
- `SuggestQuestions.tsx`：3 个推荐追问 Chip
- `useECharts.ts`：ResizeObserver + window resize 自动适配

#### 3.5 澄清交互

- `AiClarifyOptions.tsx`：结构化选项卡（单选/多选）
- 选择后自动拼接消息发送

#### 3.6 告警快速创建

- `AlertConfigCard.tsx`：展示 AI 生成的告警配置 + 一键创建按钮

---

## 二、AgentBI 愿景 vs 当前实现对照

### 核心能力矩阵

| AgentBI 愿景功能 | 当前状态 | 备注 |
|----------------|---------|------|
| 自然语言查询数据 | ✅ 完整 | nl2sql Agent，全流程贯通 |
| 内嵌图表渲染（KPI/趋势/饼/表） | ✅ 完整 | Phase 1 Merge 成果 |
| 环比/同比对比显示 | ✅ 完整 | KpiCard + PeriodCompareItem |
| 推荐追问 | ⚠️ 基础实现 | 固定模板，非 LLM 动态生成 |
| 一键创建图表 | ✅ 完整 | Chart Agent 全流程 |
| 多图表仪表板生成 | ✅ 完整 | Dashboard Agent，支持多数据集 |
| 图表修改（意图识别） | ✅ 完整 | classify_intent 区分修改/新建 |
| 澄清问答 | ✅ 完整 | clarify_user 节点 + 前端选项卡 |
| 分析计划确认 | ✅ 完整 | Phase 19a 双模式（plan/direct） |
| SQL 自动修复 | ✅ 完整 | debug Agent + repair_chart_params |
| 多轮对话 | ⚠️ 基础实现 | Checkpointer 近 N 轮摘要，无长期记忆 |
| 意图路由（自动分发） | ✅ 完整 | 关键词 + LLM 双层路由 |
| 指标语义层（口径统一） | ⚠️ 基础实现 | YAML 文件，无 UI 管理 |
| 告警规则生成 | ⚠️ 有 Bug | expose/protect 未导入，安全校验失效 |
| 仪表板嵌入链接 | ✅ 完整 | EmbedDashboardTool |
| 下钻交互 | ✅ 完整 | DataTable DrillLink → 续话 |
| 多数据库支持 | ✅ 完整 | 基于 database_id 隔离 |
| RBAC 权限控制 | ✅ 完整 | 所有 Tool 通过 security_manager |
| **主动异常检测** | ❌ 未实现 | AgentBI 核心差异化能力 |
| **主动预警推送** | ❌ 未实现 | Alert 仅生成，无推送渠道 |
| **自动报告生成** | ❌ 未实现 | 无任何实现 |
| **自主归因推理** | ❌ 未实现 | 靠用户手动追问，无 Plan-Execute |
| **RLS SQL 过滤注入** | ❌ 未实现 | 鉴权有，行过滤无 |
| **Supersonic 语义层激活** | ❌ 待接通 | Client 已有，双路径未激活 |

---

### 详细差距说明

#### 差距 1：异常检测引擎（AgentBI 重点能力）

- **愿景**：自动识别数据波动（Z-score、同比归因）、主动发现异常
- **现状**：仅有用户主动提问触发的 nl2sql，无主动监控能力
- **实现思路**：定时任务 + 统计基线 + 异常阈值 → 触发告警或主动推送分析
- **实现难度**：高

#### 差距 2：主动预警推送

- **愿景**：预警规则命中 → 自动推送通知（微信/钉钉/邮件）
- **现状**：`AlertConfigCard` 创建 Superset Alert 后靠原生调度执行，无 LLM 驱动的推送语义
- **实现思路**：扩展 Superset Notification Channel + 自然语言告警摘要生成
- **实现难度**：中

#### 差距 3：自动报告生成

- **愿景**：按周期自动生成 PDF/Word/PPT 格式分析报告
- **现状**：无，仅有对话式输出
- **实现思路**：Superset 已有 Report 基础设施（`ReportSchedule`），可扩展 AI 摘要生成
- **实现难度**：中

#### 差距 4：行级数据权限（RLS）感知

- **愿景**：查询结果根据用户角色过滤，业务人员只看到自己的数据
- **现状**：`execute_sql` 有 RBAC 鉴权，SQL 生成层不注入 RLS 过滤
- **实现思路**：在 `compile_sql` 或 `plan_query` 前，从 Superset RLS 表查询用户适用的过滤规则并注入 WHERE 条件
- **实现难度**：低

#### 差距 5：推荐问题质量

- **愿景**：根据数据内容智能生成个性化追问
- **现状**：固定 3 条模板（「按 X 拆分」「同比上周」「哪个维度贡献最大」），不依赖数据内容
- **实现思路**：在 `analyze_result` 中用 LLM 基于实际数据特征生成追问
- **实现难度**：低

#### 差距 6：指标口径管理 UI

- **愿景**：统一指标定义中心，支持业务人员维护
- **现状**：`metric_catalog.yaml` 本地文件，需开发人员手动维护
- **实现思路**：CRUD API + 前端管理界面 + 指标版本控制
- **实现难度**：高

#### 差距 7：自主推理（归因分析）

- **愿景**：数据下降时 Agent 自动规划「拆分 → 对比 → 定位」多步分析
- **现状**：每步需用户手动追问，无自主规划
- **实现思路**：Plan-and-Execute 模式 + Tool-Use 链式调用
- **实现难度**：极高

#### 差距 8：Supersonic 语义层集成

- **愿景**：接入 Supersonic Headless BI 管理维度/指标/时间粒度
- **现状**：`SuperSonicClient` 已实现，dual-path 切换逻辑未完成
- **实现思路**：设置 `SUPERSONIC_BASE_URL` 配置 → 在 `metric_catalog.py` 中优先调用 Supersonic API → fallback 到本地 YAML
- **实现难度**：中

---

## 三、功能完成度评估

```
对话式分析
  自然语言查询          ███████████ 100%
  内嵌图表渲染          ████████░░░  80%  (推荐问题模板化)
  多轮对话              ████████░░░  75%  (无长期记忆衰减)
  归因自主推理          ██░░░░░░░░░  20%  (仅靠用户追问)

图表与仪表板
  单图表生成            ███████████ 100%
  仪表板生成            █████████░░  90%  (多数据集支持完整)
  图表修改              ████████░░░  80%  (意图分类有误判风险)
  动态布局              █████████░░  85%  (bin-packing 完整)

预警与监控
  告警规则生成          ████░░░░░░░  40%  (有 Bug，缺推送渠道)
  主动异常检测          █░░░░░░░░░░  10%  (仅概念)
  报告自动生成          ░░░░░░░░░░░   0%

数据治理
  指标语义层            █████░░░░░░  45%  (YAML，无 UI)
  Supersonic 集成       ███░░░░░░░░  25%  (Client 已有，未激活)
  RLS 感知              ██░░░░░░░░░  20%  (鉴权有，注入无)
```

---

## 四、建议后续优先级

| 优先级 | 任务 | 预估工期 | 决策价值 |
|--------|------|---------|---------|
| 🔴 P0 | 修复 Alert API 的 2 个 CRITICAL Bug | 30 分钟 | 保证功能可用 |
| 🔴 P0 | 修复 DATETIME 误判负数（runner.py） | 1 小时 | 图表类型正确 |
| 🟠 P1 | 推荐问题改为 LLM 动态生成 | 1 天 | 提升交互质量 |
| 🟠 P1 | 激活 Supersonic 双路径（配置切换） | 3 天 | 语义层一致性 |
| 🟠 P1 | KPI statistics 限定单指标触发 | 2 小时 | 避免虚假统计 |
| 🟡 P2 | RLS SQL 过滤注入 | 3 天 | 多租户合规 |
| 🟡 P2 | 自动报告生成（Superset Report 扩展） | 1 周 | 运营/管理层需求 |
| 🟡 P2 | 主动异常检测引擎（Z-score/同比） | 2 周 | 核心差异化能力 |
| 🟢 P3 | 指标语义管理 UI | 2 周 | 指标口径统一 |
| 🟢 P3 | 预警推送渠道集成（钉钉/邮件） | 1 周 | 闭环预警流程 |
