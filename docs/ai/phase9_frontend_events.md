# Phase 9：前端事件渲染增强

> 前置：Phase 8 StateGraph 后端已完成，E2E 测试通过
> 目标：让前端正确渲染后端 11 种事件，用户能实时看到 Agent 的每一步操作

---

## 一、问题分析

### 当前状态

后端通过 Redis Streams 发送 11 种事件：

| 事件类型 | 后端发出 | 前端处理 |
|---|---|---|
| `thinking` | ✅ "理解请求..." "搜索数据集..." 等 | ❌ 静默丢弃 |
| `text_chunk` | ✅ 文本流 | ✅ 累加显示 |
| `tool_call` | ✅ 工具调用开始 | ❌ 静默丢弃 |
| `tool_result` | ✅ 工具调用结果 | ❌ 静默丢弃 |
| `sql_generated` | ✅ 生成的 SQL | ❌ 静默丢弃 |
| `data_analyzed` | ✅ 数据分析摘要 | ❌ 前端 types.ts 未定义 |
| `chart_created` | ✅ 图表创建结果 | ❌ 静默丢弃 |
| `dashboard_created` | ✅ 仪表板创建结果 | ❌ 静默丢弃 |
| `error_fixed` | ✅ 修复提示 | ❌ 静默丢弃 |
| `done` | ✅ 完成信号 | ✅ 停止轮询 |
| `error` | ✅ 错误信息 | ✅ 显示错误 |

**核心问题**：用户在 chart/dashboard 模式下只看到 "Thinking..." + 闪烁光标，完全看不到 Agent 在做什么。

### 次要问题

1. **Chart/Dashboard 链接靠正则**：`extractChartUrl()` 和 `extractDashboardUrl()` 从文本中正则提取 URL，后端已发出结构化的 `chart_created` / `dashboard_created` 事件但未使用
2. **60 秒超时太短**：Dashboard 模式通常需要 30-90 秒，`MAX_POLL_ATTEMPTS = 120`（500ms × 120 = 60s）
3. **`useAiStream.ts` 死代码**：未被任何组件引用
4. **`data_analyzed` 类型缺失**：前端 types.ts 未定义此事件类型

---

## 二、改动概览

改 4 个文件，删 1 个文件，新增 1 个组件：

```
superset-frontend/src/features/ai/
├── types.ts                    [改] 添加 data_analyzed + 结构化接口
├── hooks/
│   ├── useAiChat.ts            [改] 核心改造：处理全部事件类型
│   └── useAiStream.ts          [删] 死代码
├── components/
│   ├── AiStepProgress.tsx      [新] 实时步骤进度组件
│   └── AiChatPanel.tsx         [改] 用结构化事件替代正则
```

### 后端零改动

所有事件类型后端已正确实现，只需前端消费。

---

## 三、详细设计

### 3.1 types.ts — 扩展类型定义

```typescript
// 新增事件类型
export type AgentEventType =
  | 'thinking'
  | 'text_chunk'
  | 'tool_call'
  | 'tool_result'
  | 'sql_generated'
  | 'data_analyzed'        // ← 新增
  | 'chart_created'
  | 'dashboard_created'
  | 'error_fixed'
  | 'done'
  | 'error';

// 新增：步骤进度
export interface AiStep {
  id: string;
  type: AgentEventType;
  label: string;              // 显示文本
  status: 'running' | 'done' | 'error';
  detail?: string;            // 附加信息（SQL、工具名等）
}

// 新增：图表创建结果
export interface ChartResult {
  chartId: number;
  sliceName: string;
  vizType: string;
  exploreUrl: string;
}

// 新增：仪表板创建结果
export interface DashboardResult {
  dashboardId: number;
  dashboardTitle: string;
  dashboardUrl: string;
  chartCount: number;
}
```

### 3.2 useAiChat.ts — 核心改造

**新增状态**：

```typescript
const [steps, setSteps] = useState<AiStep[]>([]);
const [chartResults, setChartResults] = useState<ChartResult[]>([]);
const [dashboardResult, setDashboardResult] = useState<DashboardResult | null>(null);
const [sqlPreview, setSqlPreview] = useState<string | null>(null);
```

**事件处理逻辑**：

```typescript
for (const event of response.events) {
  switch (event.type) {
    case 'thinking':
      // 添加或更新步骤
      addStep(event.data.content as string, 'running');
      break;

    case 'tool_call':
      addStep(`调用工具: ${event.data.tool}`, 'running');
      break;

    case 'tool_result':
      updateLastStep('done');
      break;

    case 'sql_generated':
      setSqlPreview(event.data.sql as string);
      break;

    case 'data_analyzed':
      addStep(`数据分析: ${event.data.row_count} 行`, 'done');
      break;

    case 'chart_created':
      setChartResults(prev => [...prev, {
        chartId: event.data.chart_id as number,
        sliceName: event.data.slice_name as string,
        vizType: event.data.viz_type as string,
        exploreUrl: event.data.explore_url as string,
      }]);
      break;

    case 'dashboard_created':
      setDashboardResult({
        dashboardId: event.data.dashboard_id as number,
        dashboardTitle: event.data.dashboard_title as string,
        dashboardUrl: event.data.dashboard_url as string,
        chartCount: event.data.chart_count as number,
      });
      break;

    case 'error_fixed':
      addStep(`修复: ${event.data.message}`, 'done');
      break;

    case 'text_chunk':
      newChunkText += event.data.content as string;
      break;

    case 'error':
      // 错误处理
      break;

    case 'done':
      // 完成处理
      break;
  }
}
```

**超时调整**：`MAX_POLL_ATTEMPTS = 360`（500ms × 360 = 180s）

**返回值扩展**：

```typescript
return {
  messages, loading, streamingText, sendMessage, clearMessages,
  steps, chartResults, dashboardResult, sqlPreview,
};
```

**步骤去重**：使用 label 文本去重（同一条 thinking 消息只显示一次），状态更新而非追加。

### 3.3 AiStepProgress.tsx — 新增步骤进度组件

```tsx
interface AiStepProgressProps {
  steps: AiStep[];
}

// 视觉设计：
// ┌──────────────────────────────────────┐
// │ ▼ 工作步骤 (5/7)                     │
// │  ✓ 理解请求... 请求解析完成          │
// │  ✓ 搜索数据集... 数据集搜索完成      │
// │  ✓ Schema 读取完成                   │
// │  ✓ 图表规划完成                      │
// │  ● 执行查询... (进行中)              │
// └──────────────────────────────────────┘
//
// ✓ 已完成 → 灰色文本
// ● 进行中 → 蓝色 + 脉冲动画
// ✗ 错误   → 红色
```

- 默认展开，超过 8 步时自动折叠（只显示最后 5 步 + 展开按钮）
- 使用 Ant Design tokens 保持与 Superset 主题一致
- 脉冲动画使用 CSS `@keyframes` + `opacity` 渐变

### 3.4 AiChatPanel.tsx — 改造

**删除**：
- `extractChartUrl()` 函数
- `extractDashboardUrl()` 函数
- 消息列表中的正则提取逻辑

**新增**：
- 从 `useAiChat` 解构 `steps`, `chartResults`, `dashboardResult`, `sqlPreview`
- 在 streaming 区域显示 `<AiStepProgress steps={steps} />`
- 用 `chartResults` 渲染图表链接卡片
- 用 `dashboardResult` 渲染仪表板链接卡片
- Chart 模式下显示 SQL 预览（如果有 `sqlPreview`）

**渲染结构**：

```
消息列表:
  用户消息
  AI 消息（text_chunk 累积的最终文本）
    ├── <AiStepProgress steps={...} />     // 思考步骤
    ├── <AiSqlPreview sql={...} />         // SQL 预览（chart/dashboard 模式）
    ├── <ChartLink> × N                    // 创建的图表链接
    └── <DashboardLink>                    // 创建的仪表板链接

Streaming 区域:
  <AiStepProgress steps={...} />           // 实时步骤更新
  <AiStreamingText text={streamingText} /> // 文本流
```

### 3.5 useAiStream.ts — 删除

确认无任何文件引用后删除。`useAiChat` 已包含完整的轮询逻辑。

---

## 四、实施步骤

| 步骤 | 内容 | 文件 |
|---|---|---|
| 1 | 更新 types.ts | `types.ts` |
| 2 | 重写 useAiChat.ts | `useAiChat.ts` |
| 3 | 新增 AiStepProgress.tsx | `AiStepProgress.tsx`（新） |
| 4 | 改造 AiChatPanel.tsx | `AiChatPanel.tsx` |
| 5 | 删除 useAiStream.ts | `useAiStream.ts`（删） |
| 6 | 部署前端 build 到 Docker | — |
| 7 | 浏览器测试 | — |

---

## 五、验证方案

### 1. Chart 模式

输入：`"查询birth_names表的出生人数趋势"`

期望：
- 步骤列表实时更新：理解请求 → 搜索数据集 → 选择数据集 → Schema → 规划图表 → SQL → 分析 → 图表类型 → 创建
- SQL 预览区域显示生成的 SQL
- "View Chart →" 链接正常跳转

### 2. Dashboard 模式

输入：`"创建birth_names的仪表板，包含趋势图和性别分布饼图"`

期望：
- 步骤列表持续更新，每张图创建完成时 chartResults 数组增长
- 最终显示 "View Dashboard →" 链接
- 不超时（< 180s）

### 3. 错误场景

输入：`"查询不存在表xyz"`

期望：
- 步骤列表显示搜索 → 选择步骤变为红色 ✗
- 显示错误消息

### 4. NL2SQL 模式（回归测试）

输入：`"查询有多少条记录"`

期望：
- 与当前行为一致：SQL 预览 + "Copy to SQL Lab" 按钮
