/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

export type AgentEventType =
  | 'thinking'
  | 'retrying'
  | 'text_chunk'
  | 'tool_call'
  | 'tool_result'
  | 'tool_repair'
  | 'sql_generated'
  | 'data_analyzed'
  | 'insight_generated'
  | 'chart_created'
  | 'chart_updated'
  | 'chart_preview'
  | 'dashboard_created'
  | 'error_fixed'
  | 'intent_routed'
  | 'analysis_plan'
  | 'clarify'
  | 'done'
  | 'error';

export interface AiStreamEvent {
  id: string;
  type: AgentEventType;
  data: Record<string, unknown>;
}

export interface AiChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  steps?: AiStep[];
  /** Inline chart data from data_analyzed event. */
  queryResult?: SqlQueryResult;
  /** Agent-driven chart previews with viz_type + form_data. */
  chartPreviews?: ChartPreviewData[];
  /** Suggested follow-up questions. */
  suggestQuestions?: string[];
}

/** A single step in the agent's execution progress. */
export interface AiStep {
  id: string;
  type: AgentEventType;
  label: string;
  status: 'running' | 'done' | 'error';
  detail?: string;
}

/** Result of a successful chart creation event. */
export interface ChartResult {
  chartId: number;
  sliceName: string;
  vizType: string;
  exploreUrl: string;
}

/** Result of a successful dashboard creation event. */
export interface DashboardResult {
  dashboardId: number;
  dashboardTitle: string;
  dashboardUrl: string;
  chartCount: number;
}

export interface AiChatRequest {
  message: string;
  database_id?: number;
  agent_type?: string;
  session_id?: string;
}

export interface AiChatResponse {
  channel_id: string;
}

export interface AiEventsResponse {
  events: AiStreamEvent[];
  last_id: string;
}

/** A persistent conversation session stored in localStorage. */
export interface AiSession {
  id: string;
  title: string;
  databaseId: number | null;
  agentType: string;
  createdAt: number;
  updatedAt: number;
}

/** A single clarification option presented to the user. */
export interface ClarifyOption {
  label: string;
  value: string;
  description?: string;
}

/** State holding an active clarification request from the AI agent. */
export interface ClarifyState {
  question: string;
  clarifyType: string;
  options: ClarifyOption[];
  answerPrefix: string;
  originalRequest: string;
}

/** SQL query result for inline chart rendering. */
export interface SqlQueryResult {
  columns: Array<{
    name: string;
    type: 'STRING' | 'INTEGER' | 'FLOAT' | 'DATETIME' | 'BOOLEAN' | 'TEXT';
    is_dttm?: boolean;
  }>;
  rows: Record<string, unknown>[];
  row_count: number;
  insight?: string;
  /** Period-over-period statistics, e.g. { "环比": "+5.2%", "同比": "+12.3%" } */
  statistics?: Record<string, string>;
}

/** Chart preview data from chart_preview event (viz_type + form_data + query rows). */
export interface ChartPreviewData {
  vizType: string;
  sliceName: string;
  semanticParams: Record<string, unknown>;
  formData: Record<string, unknown>;
  datasourceId: number;
  insight?: string;
  suggestQuestions?: string[];
  chartIndex: number;
  /** Parsed query result columns + rows for inline chart rendering. */
  columns?: SqlQueryResult['columns'];
  rows?: SqlQueryResult['rows'];
  row_count?: number;
}

/** Phase 19a: structured analysis plan for user confirmation. */
export interface AnalysisPlanData {
  dataset: string;
  dataset_reason: string;
  metrics_dimensions: {
    metrics: string[];
    dimensions: string[];
  };
  time_range?: string;
  charts: Array<{
    index: number;
    title: string;
    intent: string;
    viz: string;
    target_table?: string;
  }>;
  assumptions_risks: string[];
  confidence: number;
}

/** Phase 21: AI-generated alert configuration. */
export interface AiAlertConfigResponse {
  name: string;
  sql: string;
  validator_type: 'not null' | 'operator' | 'AI';
  validator_config_json: Record<string, unknown>;
  crontab: string;
  description: string;
  database_id: number;
}

/** Shared agent mode definitions — single source of truth for UI components. */
export const AGENT_MODES = [
  { label: '数据助手', value: 'data_assistant' },
  { label: 'Chart', value: 'chart' },
  { label: 'Dashboard', value: 'dashboard' },
  { label: 'Alert', value: 'alert' },
] as const;

export const AGENT_MODES_WITH_AUTO = [
  { label: 'Auto', value: 'auto' },
  { label: '数据助手', value: 'data_assistant' },
  { label: 'Chart', value: 'chart' },
  { label: 'Dashboard', value: 'dashboard' },
] as const;

/** Map of routed agent type to display label. */
export const ROUTED_LABELS: Record<string, string> = {
  data_assistant: '数据助手',
  chart: 'Chart',
  dashboard: 'Dashboard',
};
