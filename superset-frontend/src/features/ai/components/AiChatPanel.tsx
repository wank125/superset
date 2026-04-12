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

import { useState, useRef, useEffect, useCallback } from 'react';
import { styled, t } from '@superset-ui/core';
import { Radio, RadioChangeEvent } from '@superset-ui/core/components/Radio';
import { useAiChat } from '../hooks/useAiChat';
import { AiMessageBubble } from './AiMessageBubble';
import { AiStreamingText } from './AiStreamingText';
import { AiSqlPreview } from './AiSqlPreview';
import { AiStepProgress } from './AiStepProgress';

interface AiChatPanelProps {
  databaseId: number;
  onSqlGenerated?: (sql: string) => void;
  onChartCreated?: (chartId: number, exploreUrl: string) => void;
  onDashboardCreated?: (dashboardId: number, dashboardUrl: string) => void;
  visible?: boolean;
  onClose: () => void;
}

const PanelContainer = styled.div`
  display: flex;
  flex-direction: column;
  height: 100%;
  background: ${({ theme }) => theme.colorBgContainer};
`;

const Header = styled.div`
  flex-shrink: 0;
  padding: 12px 16px;
  border-bottom: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  display: flex;
  justify-content: space-between;
  align-items: center;
`;

const HeaderLeft = styled.div`
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
`;

const HeaderTitle = styled.span`
  font-weight: ${({ theme }) => theme.fontWeightStrong};
`;

const CloseButton = styled.button`
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 18px;
  color: ${({ theme }) => theme.colorTextSecondary};
`;

const MessagesContainer = styled.div`
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
`;

const InputContainer = styled.div`
  padding: 12px;
  border-top: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  display: flex;
  gap: 8px;
`;

const Input = styled.input`
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 4px;
  font-size: 13px;
  outline: none;

  &:focus {
    border-color: ${({ theme }) => theme.colorPrimary};
  }
`;

const SendButton = styled.button<{ disabled: boolean }>`
  padding: 8px 16px;
  background: ${({ disabled, theme }) =>
    disabled ? theme.colorBgLayout : theme.colorPrimary};
  color: ${({ disabled, theme }) =>
    disabled ? theme.colorTextDisabled : theme.colorWhite};
  border: none;
  border-radius: 4px;
  cursor: ${({ disabled }) => (disabled ? 'not-allowed' : 'pointer')};
  font-size: 13px;
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

const AGENT_MODES = [
  { label: 'SQL', value: 'nl2sql' },
  { label: 'Chart', value: 'chart' },
  { label: 'Dashboard', value: 'dashboard' },
];

export function AiChatPanel({
  databaseId,
  onSqlGenerated,
  onChartCreated,
  onDashboardCreated,
  onClose,
}: AiChatPanelProps) {
  const [agentType, setAgentType] = useState('nl2sql');
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const {
    messages,
    loading,
    streamingText,
    sendMessage,
    clearMessages,
    steps,
    chartResults,
    dashboardResult,
    sqlPreview,
  } = useAiChat(databaseId, agentType);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, steps, scrollToBottom]);

  const handleModeChange = (e: RadioChangeEvent) => {
    const newMode = e.target.value;
    if (newMode !== agentType) {
      setAgentType(newMode);
      clearMessages();
    }
  };

  const handleSend = () => {
    if (!inputValue.trim() || loading) return;
    sendMessage(inputValue.trim());
    setInputValue('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const placeholder =
    agentType === 'dashboard'
      ? t('Describe the dashboard you want to create...')
      : agentType === 'chart'
        ? t('Describe the chart you want to create...')
        : t('Ask a question about your data...');

  // Latest results from the most recent agent run
  const latestChart =
    chartResults.length > 0 ? chartResults[chartResults.length - 1] : null;

  return (
    <PanelContainer>
      <Header>
        <HeaderLeft>
          <HeaderTitle>{t('AI Assistant')}</HeaderTitle>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            size="small"
            options={AGENT_MODES}
            value={agentType}
            onChange={handleModeChange}
          />
        </HeaderLeft>
        <CloseButton onClick={onClose}>✕</CloseButton>
      </Header>
      <MessagesContainer>
        {messages.map((msg, idx) => (
          <div key={idx}>
            <AiMessageBubble message={msg} />
            {msg.role === 'assistant' && idx === messages.length - 1 && (
              <>
                {agentType === 'nl2sql' && (
                  <AiSqlPreview
                    sql={msg.content}
                    onCopyToEditor={onSqlGenerated}
                  />
                )}
                {(agentType === 'chart' || agentType === 'dashboard') &&
                  sqlPreview && (
                    <AiSqlPreview
                      sql={sqlPreview}
                      onCopyToEditor={onSqlGenerated}
                    />
                  )}
                {agentType === 'chart' && latestChart && onChartCreated && (
                  <ResultCard
                    href={latestChart.exploreUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => {
                      e.preventDefault();
                      onChartCreated(
                        latestChart.chartId,
                        latestChart.exploreUrl,
                      );
                    }}
                  >
                    <ResultLabel>{t('View Chart')}</ResultLabel>
                    {latestChart.sliceName} ({latestChart.vizType}) →
                  </ResultCard>
                )}
                {agentType === 'dashboard' &&
                  dashboardResult &&
                  onDashboardCreated && (
                    <ResultCard
                      href={dashboardResult.dashboardUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={e => {
                        e.preventDefault();
                        onDashboardCreated(
                          dashboardResult.dashboardId,
                          dashboardResult.dashboardUrl,
                        );
                      }}
                    >
                      <ResultLabel>{t('View Dashboard')}</ResultLabel>
                      {dashboardResult.dashboardTitle} (
                      {dashboardResult.chartCount} {t('charts')}) →
                    </ResultCard>
                  )}
                {agentType === 'dashboard' &&
                  chartResults.length > 0 &&
                  !dashboardResult && (
                    <div style={{ marginTop: 4 }}>
                      {chartResults.map(cr => (
                        <ResultCard
                          key={cr.chartId}
                          href={cr.exploreUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={e => {
                            e.preventDefault();
                            if (onChartCreated) {
                              onChartCreated(cr.chartId, cr.exploreUrl);
                            }
                          }}
                          style={{ marginBottom: 4 }}
                        >
                          <ResultLabel>{t('Chart')}</ResultLabel>
                          {cr.sliceName} ({cr.vizType}) →
                        </ResultCard>
                      ))}
                    </div>
                  )}
              </>
            )}
          </div>
        ))}
        {loading && steps.length > 0 && <AiStepProgress steps={steps} />}
        {loading && streamingText && <AiStreamingText text={streamingText} />}
        {loading && !streamingText && steps.length === 0 && (
          <AiStreamingText text={t('Thinking...')} />
        )}
        <div ref={messagesEndRef} />
      </MessagesContainer>
      <InputContainer>
        <Input
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={loading}
        />
        <SendButton
          onClick={handleSend}
          disabled={loading || !inputValue.trim()}
        >
          {t('Send')}
        </SendButton>
      </InputContainer>
    </PanelContainer>
  );
}
