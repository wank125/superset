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
import { Radio } from '@superset-ui/core/components';
import { useAiChat } from '../hooks/useAiChat';
import { AiMessageBubble } from './AiMessageBubble';
import { AiStreamingText } from './AiStreamingText';
import { AiSqlPreview } from './AiSqlPreview';

interface AiChatPanelProps {
  databaseId: number;
  onSqlGenerated?: (sql: string) => void;
  onChartCreated?: (chartId: number, exploreUrl: string) => void;
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
  padding: 12px 16px;
  border-bottom: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  display: flex;
  justify-content: space-between;
  align-items: center;
`;

const HeaderLeft = styled.div`
  display: flex;
  align-items: center;
  gap: 12px;
`;

const HeaderTitle = styled.span`
  font-weight: ${({ theme }) => theme.fontWeightStrong};
`;

const CloseButton = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  font-size: 18px;
  color: ${({ theme }) => theme.colorTextSecondary};
`;

const MessagesContainer = styled.div`
  flex: 1;
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

const ChartLink = styled.a`
  display: inline-block;
  margin-top: 4px;
  padding: 6px 12px;
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

function extractChartUrl(text: string): {
  chartId: number;
  exploreUrl: string;
} | null {
  // Match explore URL with slice_id from the text
  const match = text.match(
    /\/explore\/\?(?:form_data_key=[^&]*&)?slice_id=(\d+)/,
  );
  if (match) {
    return { chartId: parseInt(match[1], 10), exploreUrl: match[0] };
  }
  return null;
}

const AGENT_MODES = [
  { label: 'SQL', value: 'nl2sql' },
  { label: 'Chart', value: 'chart' },
];

export function AiChatPanel({
  databaseId,
  onSqlGenerated,
  onChartCreated,
  onClose,
}: AiChatPanelProps) {
  const [agentType, setAgentType] = useState('nl2sql');
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { messages, loading, streamingText, sendMessage, clearMessages } =
    useAiChat(databaseId, agentType);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

  const handleModeChange = (e: any) => {
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
    agentType === 'chart'
      ? t('Describe the chart you want to create...')
      : t('Ask a question about your data...');

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
            {msg.role === 'assistant' && agentType === 'nl2sql' && (
              <AiSqlPreview sql={msg.content} onCopyToEditor={onSqlGenerated} />
            )}
            {msg.role === 'assistant' && agentType === 'chart' && (() => {
              const chartInfo = extractChartUrl(msg.content);
              if (chartInfo && onChartCreated) {
                return (
                  <ChartLink
                    href={chartInfo.exploreUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={e => {
                      e.preventDefault();
                      onChartCreated(chartInfo.chartId, chartInfo.exploreUrl);
                    }}
                  >
                    {t('View Chart')} →
                  </ChartLink>
                );
              }
              return null;
            })()}
          </div>
        ))}
        {loading && streamingText && <AiStreamingText text={streamingText} />}
        {loading && !streamingText && (
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
        <SendButton onClick={handleSend} disabled={loading || !inputValue.trim()}>
          {t('Send')}
        </SendButton>
      </InputContainer>
    </PanelContainer>
  );
}
