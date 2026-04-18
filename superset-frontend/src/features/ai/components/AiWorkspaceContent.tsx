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

import { useRef, useEffect, useCallback } from 'react';
import { styled, t } from '@superset-ui/core';
import type {
  AiChatMessage,
  AiStep,
  ChartPreviewData,
  ChartResult,
  DashboardResult,
  ClarifyState,
} from 'src/features/ai/types';
import { AiMessageBubble } from './AiMessageBubble';
import { AiStreamingText } from './AiStreamingText';
import { AiSqlPreview } from './AiSqlPreview';
import { AiStepProgress } from './AiStepProgress';
import { AiClarifyOptions } from './AiClarifyOptions';

interface AiWorkspaceContentProps {
  messages: AiChatMessage[];
  streamingText: string;
  loading: boolean;
  steps: AiStep[];
  chartResults: ChartResult[];
  dashboardResult: DashboardResult | null;
  sqlPreview: string | null;
  agentType: string;
  routedAgent: string | null;
  clarifyState: ClarifyState | null;
  onSqlCopy?: (sql: string) => void;
  onChartClick?: (chartId: number, url: string) => void;
  onDashboardClick?: (dashboardId: number, url: string) => void;
  onClarifyAnswer?: (value: string) => void;
  onClarifyDismiss?: () => void;
  onSendMessage?: (message: string) => void;
  onSaveChart?: (preview: ChartPreviewData) => Promise<void>;
}

const ContentArea = styled.div`
  flex: 1;
  min-height: 0;
  width: 100%;
  min-width: 0;
  overflow-y: auto;
  padding: 24px 24px 12px;
`;

const ContentInner = styled.div`
  width: 100%;
  max-width: 768px;
  margin: 0 auto;
`;

const WelcomeBlock = styled.div`
  text-align: center;
  padding: 80px 20px 40px;
  color: ${({ theme }) => theme.colorTextSecondary};
`;

const WelcomeTitle = styled.h2`
  font-size: 22px;
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  color: ${({ theme }) => theme.colorText};
  margin: 0 0 8px;
`;

const WelcomeSub = styled.p`
  font-size: 14px;
  margin: 0;
`;

const ResultCard = styled.a`
  display: block;
  margin-top: 6px;
  padding: 8px 12px;
  background: ${({ theme }) => theme.colorPrimaryBg};
  color: ${({ theme }) => theme.colorPrimary};
  border: 1px solid ${({ theme }) => theme.colorPrimaryBorder};
  border-radius: 4px;
  text-decoration: none;
  font-size: 12px;
  cursor: pointer;

  &:hover {
    background: ${({ theme }) => theme.colorPrimaryBgHover};
  }
`;

const ResultLabel = styled.span`
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  margin-right: 8px;
`;

const ChartListContainer = styled.div`
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
`;

export function AiWorkspaceContent({
  messages,
  streamingText,
  loading,
  steps,
  chartResults,
  dashboardResult,
  sqlPreview,
  agentType,
  routedAgent,
  clarifyState,
  onSqlCopy,
  onChartClick,
  onDashboardClick,
  onClarifyAnswer,
  onClarifyDismiss,
  onSendMessage,
  onSaveChart,
}: AiWorkspaceContentProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, steps, scrollToBottom]);

  const latestChart =
    chartResults.length > 0 ? chartResults[chartResults.length - 1] : null;
  const hasSqlBlock = (text: string) => /```sql\s*\n[\s\S]*?```/i.test(text);

  return (
    <ContentArea>
      <ContentInner>
        {messages.length === 0 && !loading && (
          <WelcomeBlock>
            <WelcomeTitle>{t('AI 助手')}</WelcomeTitle>
            <WelcomeSub>{t('选择数据库，输入你的问题开始对话')}</WelcomeSub>
          </WelcomeBlock>
        )}
        {messages.map((msg, idx) => (
          <div key={msg.timestamp}>
            <AiMessageBubble
              message={msg}
              onSuggestQuestion={
                msg.role === 'assistant' && onSendMessage
                  ? onSendMessage
                  : undefined
              }
              onSaveChart={onSaveChart}
            />
            {msg.role === 'assistant' && msg.steps && msg.steps.length > 0 && (
              <AiStepProgress steps={msg.steps} />
            )}
            {msg.role === 'assistant' && idx === messages.length - 1 && (
              <>
                {agentType === 'data_assistant' && !hasSqlBlock(msg.content) && (
                  <AiSqlPreview sql={msg.content} onCopyToEditor={onSqlCopy} />
                )}
                {(agentType === 'chart' || agentType === 'dashboard') &&
                  sqlPreview && (
                    <AiSqlPreview sql={sqlPreview} onCopyToEditor={onSqlCopy} />
                  )}
                {agentType === 'chart' && latestChart && onChartClick && (
                  <ResultCard
                    href={latestChart.exploreUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => {
                      e.preventDefault();
                      onChartClick(latestChart.chartId, latestChart.exploreUrl);
                    }}
                  >
                    <ResultLabel>{t('查看图表')}</ResultLabel>
                    {latestChart.sliceName} ({latestChart.vizType}) →
                  </ResultCard>
                )}
                {agentType === 'dashboard' &&
                  dashboardResult &&
                  onDashboardClick && (
                    <ResultCard
                      href={dashboardResult.dashboardUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={e => {
                        e.preventDefault();
                        onDashboardClick(
                          dashboardResult.dashboardId,
                          dashboardResult.dashboardUrl,
                        );
                      }}
                    >
                      <ResultLabel>{t('查看仪表板')}</ResultLabel>
                      {dashboardResult.dashboardTitle} (
                      {dashboardResult.chartCount} {t('张图表')}) →
                    </ResultCard>
                  )}
                {agentType === 'dashboard' &&
                  chartResults.length > 0 &&
                  !dashboardResult && (
                    <ChartListContainer>
                      {chartResults.map(cr => (
                        <ResultCard
                          key={cr.chartId}
                          href={cr.exploreUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={e => {
                            e.preventDefault();
                            onChartClick?.(cr.chartId, cr.exploreUrl);
                          }}
                        >
                          <ResultLabel>{t('图表')}</ResultLabel>
                          {cr.sliceName} ({cr.vizType}) →
                        </ResultCard>
                      ))}
                    </ChartListContainer>
                  )}
              </>
            )}
          </div>
        ))}
        {loading && steps.length > 0 && <AiStepProgress steps={steps} />}
        {loading && streamingText && <AiStreamingText text={streamingText} />}
        {loading && !streamingText && steps.length === 0 && (
          <AiStreamingText text={t('思考中...')} />
        )}
        {clarifyState && onClarifyAnswer && onClarifyDismiss && (
          <AiClarifyOptions
            clarifyState={clarifyState}
            onSelect={onClarifyAnswer}
            onDismiss={onClarifyDismiss}
          />
        )}
        <div ref={bottomRef} />
      </ContentInner>
    </ContentArea>
  );
}
