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

import { useCallback, useRef, useState } from 'react';
import type {
  AiChatMessage,
  AiStep,
  ChartResult,
  DashboardResult,
} from '../types';
import { sendChat, fetchEvents } from '../api/aiClient';

const POLL_INTERVAL_MS = 500;
const MAX_POLL_ATTEMPTS = 360; // 180 seconds

/** Generate a stable session ID that persists for the lifetime of the hook. */
function createSessionId(): string {
  return `ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

let stepCounter = 0;

export function useAiChat(databaseId: number, agentType: string = 'nl2sql') {
  const [messages, setMessages] = useState<AiChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [steps, setSteps] = useState<AiStep[]>([]);
  const [chartResults, setChartResults] = useState<ChartResult[]>([]);
  const [dashboardResult, setDashboardResult] =
    useState<DashboardResult | null>(null);
  const [sqlPreview, setSqlPreview] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessionIdRef = useRef<string>(createSessionId());
  // Ref to track accumulated streaming text without relying on setState updater
  const streamingTextRef = useRef('');
  // Track step labels to avoid duplicates
  const stepLabelsRef = useRef<Set<string>>(new Set());
  // Track latest results via refs so finalize() can read them synchronously
  const chartResultsRef = useRef<ChartResult[]>([]);
  const dashboardResultRef = useRef<DashboardResult | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const addStep = useCallback(
    (label: string, status: AiStep['status'], type: AiStep['type'] = 'thinking', detail?: string) => {
      // Deduplicate by label — update existing step status instead of appending
      if (stepLabelsRef.current.has(label)) {
        setSteps(prev =>
          prev.map(s =>
            s.label === label ? { ...s, status, detail: detail ?? s.detail } : s,
          ),
        );
        return;
      }
      stepLabelsRef.current.add(label);
      const step: AiStep = {
        id: `step-${++stepCounter}`,
        type,
        label,
        status,
        detail,
      };
      setSteps(prev => [...prev, step]);
    },
    [],
  );

  const markAllRunningDone = useCallback(() => {
    setSteps(prev =>
      prev.map(s => (s.status === 'running' ? { ...s, status: 'done' as const } : s)),
    );
  }, []);

  const resetState = useCallback(() => {
    streamingTextRef.current = '';
    setStreamingText('');
    setSteps([]);
    setChartResults([]);
    chartResultsRef.current = [];
    setDashboardResult(null);
    dashboardResultRef.current = null;
    setSqlPreview(null);
    stepLabelsRef.current.clear();
  }, []);

  const finalize = useCallback(
    (accumulated: string) => {
      // If we have text chunks, use them as the assistant message
      if (accumulated) {
        setMessages(msgs => [
          ...msgs,
          { role: 'assistant' as const, content: accumulated, timestamp: Date.now() },
        ]);
      } else {
        // StateGraph path: no text_chunk events, generate summary from results
        const charts = chartResultsRef.current;
        const dash = dashboardResultRef.current;
        if (charts.length > 0 || dash) {
          const lines: string[] = [];
          if (dash) {
            lines.push(`仪表板 "${dash.dashboardTitle}" 创建成功！`);
            lines.push(`${dash.chartCount} 张图表已添加。`);
            lines.push(dash.dashboardUrl);
          } else if (charts.length === 1) {
            lines.push(`图表 "${charts[0].sliceName}" 创建成功！`);
            lines.push(charts[0].exploreUrl);
          } else {
            lines.push(`${charts.length} 张图表创建成功：`);
            charts.forEach(c => {
              lines.push(`- ${c.sliceName} (${c.vizType})`);
              lines.push(`  ${c.exploreUrl}`);
            });
          }
          setMessages(msgs => [
            ...msgs,
            { role: 'assistant' as const, content: lines.join('\n'), timestamp: Date.now() },
          ]);
        }
      }
      markAllRunningDone();
      streamingTextRef.current = '';
      setStreamingText('');
      setLoading(false);
    },
    [markAllRunningDone],
  );

  const pollEvents = useCallback(
    (channelId: string, lastId: string, attempt: number) => {
      if (attempt >= MAX_POLL_ATTEMPTS) {
        finalize(streamingTextRef.current);
        return;
      }

      fetchEvents(channelId, lastId)
        .then(response => {
          let newChunkText = '';
          for (const event of response.events) {
            switch (event.type) {
              case 'thinking': {
                const content = (event.data.content as string) || '';
                if (content) {
                  addStep(content, 'running', 'thinking');
                }
                break;
              }
              case 'tool_call': {
                const tool = (event.data.tool as string) || '';
                addStep(`调用工具: ${tool}`, 'running', 'tool_call');
                break;
              }
              case 'tool_result': {
                // Mark the last running tool_call step as done
                setSteps(prev => {
                  const idx = [...prev]
                    .reverse()
                    .findIndex(s => s.type === 'tool_call' && s.status === 'running');
                  if (idx === -1) return prev;
                  const realIdx = prev.length - 1 - idx;
                  return prev.map((s, i) =>
                    i === realIdx ? { ...s, status: 'done' as const } : s,
                  );
                });
                break;
              }
              case 'sql_generated': {
                const sql = (event.data.sql as string) || '';
                if (sql) {
                  setSqlPreview(sql);
                  addStep('SQL 生成完成', 'done', 'sql_generated', sql);
                }
                break;
              }
              case 'data_analyzed': {
                const rowCount = event.data.row_count as number;
                addStep(
                  `数据分析完成 (${rowCount} 行)`,
                  'done',
                  'data_analyzed',
                );
                break;
              }
              case 'chart_created': {
                const chart: ChartResult = {
                  chartId: event.data.chart_id as number,
                  sliceName: (event.data.slice_name as string) || '',
                  vizType: (event.data.viz_type as string) || '',
                  exploreUrl: (event.data.explore_url as string) || '',
                };
                chartResultsRef.current = [...chartResultsRef.current, chart];
                setChartResults(prev => [...prev, chart]);
                addStep(
                  `图表创建完成: ${chart.sliceName}`,
                  'done',
                  'chart_created',
                );
                break;
              }
              case 'dashboard_created': {
                const dash: DashboardResult = {
                  dashboardId: event.data.dashboard_id as number,
                  dashboardTitle:
                    (event.data.dashboard_title as string) || '',
                  dashboardUrl: (event.data.dashboard_url as string) || '',
                  chartCount: (event.data.chart_count as number) || 0,
                };
                dashboardResultRef.current = dash;
                setDashboardResult(dash);
                addStep(
                  `仪表板创建完成: ${dash.dashboardTitle}`,
                  'done',
                  'dashboard_created',
                );
                break;
              }
              case 'error_fixed': {
                const msg = (event.data.message as string) || '';
                addStep(msg, 'done', 'error_fixed');
                break;
              }
              case 'text_chunk': {
                const content = event.data.content as string;
                if (content) {
                  newChunkText += content;
                }
                break;
              }
              case 'error': {
                const errMsg =
                  (event.data.message as string) || 'Unknown error';
                addStep(`错误: ${errMsg}`, 'error', 'error');
                finalize(streamingTextRef.current);
                setMessages(prev => [
                  ...prev,
                  {
                    role: 'assistant' as const,
                    content: `Error: ${errMsg}`,
                    timestamp: Date.now(),
                  },
                ]);
                stopPolling();
                return;
              }
              case 'done': {
                const fullText = streamingTextRef.current + newChunkText;
                finalize(fullText);
                stopPolling();
                return;
              }
              default:
                break;
            }
          }

          // No done event yet — accumulate chunk text for next poll
          if (newChunkText) {
            streamingTextRef.current += newChunkText;
            setStreamingText(streamingTextRef.current);
          }

          // Continue polling
          pollTimerRef.current = setTimeout(
            () => pollEvents(channelId, response.last_id, attempt + 1),
            POLL_INTERVAL_MS,
          ) as unknown as ReturnType<typeof setInterval>;
        })
        .catch(() => {
          // On network/server error, retry with back-off instead of hanging
          const backoff = Math.min(POLL_INTERVAL_MS * (attempt + 1), 5000);
          pollTimerRef.current = setTimeout(
            () => pollEvents(channelId, lastId, attempt + 1),
            backoff,
          ) as unknown as ReturnType<typeof setInterval>;
        });
    },
    [stopPolling, addStep, finalize],
  );

  const sendMessage = useCallback(
    async (message: string) => {
      if (!message.trim() || loading) return;

      setMessages(prev => [
        ...prev,
        { role: 'user', content: message, timestamp: Date.now() },
      ]);
      setLoading(true);
      resetState();

      try {
        const { channel_id: channelId } = await sendChat({
          message,
          database_id: databaseId,
          agent_type: agentType,
          session_id: sessionIdRef.current,
        });
        pollEvents(channelId, '0', 0);
      } catch (err) {
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant' as const,
            content: `Failed to send message: ${err}`,
            timestamp: Date.now(),
          },
        ]);
        setLoading(false);
      }
    },
    [databaseId, agentType, loading, pollEvents, resetState],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    resetState();
    stopPolling();
    setLoading(false);
    // Reset session for a fresh conversation
    sessionIdRef.current = createSessionId();
  }, [stopPolling, resetState]);

  return {
    messages,
    loading,
    streamingText,
    sendMessage,
    clearMessages,
    steps,
    chartResults,
    dashboardResult,
    sqlPreview,
  };
}
