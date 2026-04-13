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
  AiChatRequest,
  AiChatMessage,
  AiStep,
  ChartResult,
  DashboardResult,
  ClarifyState,
} from '../types';
import { sendChat, fetchEvents } from '../api/aiClient';

const POLL_INTERVAL_MS = 500;
const MAX_POLL_ATTEMPTS = 360; // 180 seconds
const SQL_RESULT_HEADER = '查询结果';

/** Generate a stable session ID that persists for the lifetime of the hook. */
function createSessionId(): string {
  return `ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

let stepCounter = 0;

function appendSqlResult(content: string, sqlResult: string | null): string {
  const result = sqlResult?.trim();
  if (!result || content.includes(SQL_RESULT_HEADER)) {
    return content;
  }
  const prefix = content.trim();
  const resultBlock = `${SQL_RESULT_HEADER}：\n\n\`\`\`text\n${result}\n\`\`\``;
  return prefix ? `${prefix}\n\n${resultBlock}` : resultBlock;
}

export function useAiChat(
  databaseId: number | null,
  agentType: string = 'nl2sql',
  sessionId?: string,
  initialMessages: AiChatMessage[] = [],
) {
  // Use the provided sessionId (from AiWorkspace session) or generate one
  const sessionIdRef = useRef<string>(sessionId ?? createSessionId());
  // If sessionId prop changes, update the ref
  if (sessionId && sessionIdRef.current !== sessionId) {
    sessionIdRef.current = sessionId;
  }

  const [messages, setMessages] = useState<AiChatMessage[]>(initialMessages);
  const [loading, setLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [steps, setSteps] = useState<AiStep[]>([]);
  const [chartResults, setChartResults] = useState<ChartResult[]>([]);
  const [dashboardResult, setDashboardResult] =
    useState<DashboardResult | null>(null);
  const [sqlPreview, setSqlPreview] = useState<string | null>(null);
  const [routedAgent, setRoutedAgent] = useState<string | null>(null);
  const [clarifyState, setClarifyState] = useState<ClarifyState | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Ref to track accumulated streaming text without relying on setState updater
  const streamingTextRef = useRef('');
  // Track step labels to avoid duplicates
  const stepLabelsRef = useRef<Set<string>>(new Set());
  const stepsRef = useRef<AiStep[]>([]);
  // Track latest results via refs so finalize() can read them synchronously
  const chartResultsRef = useRef<ChartResult[]>([]);
  const dashboardResultRef = useRef<DashboardResult | null>(null);
  const latestSqlResultRef = useRef<string | null>(null);
  // Synchronous loading flag — avoids stale-closure race in answerClarify
  const loadingRef = useRef(false);
  // Queued clarify answer — sent once finalize() clears loading
  const pendingAnswerRef = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const addStep = useCallback(
    (
      label: string,
      status: AiStep['status'],
      type: AiStep['type'] = 'thinking',
      detail?: string,
    ) => {
      // Deduplicate by label — update existing step status instead of appending
      if (stepLabelsRef.current.has(label)) {
        setSteps(prev => {
          const next = prev.map(s =>
            s.label === label
              ? { ...s, status, detail: detail ?? s.detail }
              : s,
          );
          stepsRef.current = next;
          return next;
        });
        return;
      }
      stepLabelsRef.current.add(label);
      stepCounter += 1;
      const step: AiStep = {
        id: `step-${stepCounter}`,
        type,
        label,
        status,
        detail,
      };
      setSteps(prev => {
        const next = [...prev, step];
        stepsRef.current = next;
        return next;
      });
    },
    [],
  );

  const markAllRunningDone = useCallback((): AiStep[] => {
    const next = stepsRef.current.map(s =>
      s.status === 'running' ? { ...s, status: 'done' as const } : s,
    );
    stepsRef.current = next;
    setSteps(next);
    return next;
  }, []);

  const createAssistantMessage = useCallback(
    (content: string, finalSteps: AiStep[]): AiChatMessage => ({
      role: 'assistant' as const,
      content: appendSqlResult(content, latestSqlResultRef.current),
      timestamp: Date.now(),
      steps: finalSteps.length > 0 ? finalSteps : undefined,
    }),
    [],
  );

  const resetState = useCallback(() => {
    streamingTextRef.current = '';
    setStreamingText('');
    setSteps([]);
    stepsRef.current = [];
    setChartResults([]);
    chartResultsRef.current = [];
    setDashboardResult(null);
    dashboardResultRef.current = null;
    setSqlPreview(null);
    latestSqlResultRef.current = null;
    setRoutedAgent(null);
    setClarifyState(null);
    stepLabelsRef.current.clear();
  }, []);

  const finalize = useCallback(
    (accumulated: string) => {
      const finalSteps = markAllRunningDone();
      // If we have text chunks, use them as the assistant message
      if (accumulated) {
        setMessages(msgs => [
          ...msgs,
          createAssistantMessage(accumulated, finalSteps),
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
            lines.push(`[打开仪表板](${dash.dashboardUrl})`);
          } else if (charts.length === 1) {
            lines.push(`图表 "${charts[0].sliceName}" 创建成功！`);
            lines.push(`[查看图表](${charts[0].exploreUrl})`);
          } else {
            lines.push(`${charts.length} 张图表创建成功：`);
            charts.forEach(c => {
              lines.push(`- [${c.sliceName}](${c.exploreUrl}) (${c.vizType})`);
            });
          }
          setMessages(msgs => [
            ...msgs,
            createAssistantMessage(lines.join('\n'), finalSteps),
          ]);
        } else if (latestSqlResultRef.current) {
          setMessages(msgs => [
            ...msgs,
            createAssistantMessage('', finalSteps),
          ]);
        }
      }
      streamingTextRef.current = '';
      setStreamingText('');
      loadingRef.current = false;
      setLoading(false);
      // NOTE: queued clarify answer is handled in sendMessage below,
      // which checks pendingAnswerRef after loadingRef is cleared.
    },
    [createAssistantMessage, markAllRunningDone],
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
                const tool = (event.data.tool as string) || '';
                const result = (event.data.result as string) || '';
                if (tool === 'execute_sql' && result) {
                  latestSqlResultRef.current = result;
                }
                // Mark the last running tool_call step as done
                setSteps(prev => {
                  const idx = [...prev]
                    .reverse()
                    .findIndex(
                      s => s.type === 'tool_call' && s.status === 'running',
                    );
                  if (idx === -1) return prev;
                  const realIdx = prev.length - 1 - idx;
                  const next = prev.map((s, i) =>
                    i === realIdx ? { ...s, status: 'done' as const } : s,
                  );
                  stepsRef.current = next;
                  return next;
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
                  dashboardTitle: (event.data.dashboard_title as string) || '',
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
              case 'intent_routed': {
                const routed = (event.data.agent as string) || 'nl2sql';
                setRoutedAgent(routed);
                addStep(`自动路由: ${routed}`, 'done', 'intent_routed');
                break;
              }
              case 'insight_generated': {
                const insight = (event.data.insight as string) || '';
                if (insight) {
                  addStep(`💡 ${insight}`, 'done', 'insight_generated');
                }
                break;
              }
              case 'clarify': {
                setClarifyState({
                  question: (event.data.question as string) || '请补充信息：',
                  clarifyType: (event.data.clarify_type as string) || 'general',
                  options: (event.data.options as ClarifyState['options']) || [],
                  answerPrefix:
                    (event.data.context as Record<string, string>)
                      ?.answer_prefix || '',
                  originalRequest:
                    (event.data.context as Record<string, string>)
                      ?.original_request || '',
                });
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
      if (!message.trim() || loadingRef.current) return;

      loadingRef.current = true;
      setMessages(prev => [
        ...prev,
        { role: 'user', content: message, timestamp: Date.now() },
      ]);
      setLoading(true);
      resetState();

      try {
        const payload: AiChatRequest = {
          message,
          agent_type: agentType,
          session_id: sessionIdRef.current,
        };
        if (databaseId != null) {
          payload.database_id = databaseId;
        }
        const { channel_id: channelId } = await sendChat(payload);
        pollEvents(channelId, '0', 0);
      } catch (err) {
        loadingRef.current = false;
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

      // After finalize() clears loading, check for queued clarify answer
      const sendIfPending = () => {
        const pending = pendingAnswerRef.current;
        if (pending && !loadingRef.current) {
          pendingAnswerRef.current = null;
          sendMessage(pending);
        }
      };
      // Use microtask to ensure finalize() has completed
      Promise.resolve().then(sendIfPending);
    },
    [databaseId, agentType, pollEvents, resetState],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    resetState();
    stopPolling();
    loadingRef.current = false;
    pendingAnswerRef.current = null;
    setLoading(false);
    // Reset session for a fresh conversation
    sessionIdRef.current = createSessionId();
  }, [stopPolling, resetState]);

  const answerClarify = useCallback(
    (value: string) => {
      if (!clarifyState) return;
      const message = clarifyState.answerPrefix
        ? clarifyState.answerPrefix.replace('{value}', value)
        : value;
      setClarifyState(null);
      if (loadingRef.current) {
        // Graph still running — queue answer; finalize() will send it
        pendingAnswerRef.current = message;
      } else {
        sendMessage(message);
      }
    },
    [clarifyState, sendMessage],
  );

  const dismissClarify = useCallback(() => {
    setClarifyState(null);
  }, []);

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
    routedAgent,
    clarifyState,
    answerClarify,
    dismissClarify,
  };
}
