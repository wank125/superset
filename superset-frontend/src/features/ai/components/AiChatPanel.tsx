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
import { useAiChat } from '../hooks/useAiChat';
import { AiMessageBubble } from './AiMessageBubble';
import { AiStreamingText } from './AiStreamingText';
import { AiSqlPreview } from './AiSqlPreview';

interface AiChatPanelProps {
  databaseId: number;
  onSqlGenerated?: (sql: string) => void;
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

export function AiChatPanel({
  databaseId,
  onSqlGenerated,
  onClose,
}: AiChatPanelProps) {
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { messages, loading, streamingText, sendMessage, clearMessages } =
    useAiChat(databaseId);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

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

  return (
    <PanelContainer>
      <Header>
        <span>{t('AI Assistant')}</span>
        <CloseButton onClick={onClose}>✕</CloseButton>
      </Header>
      <MessagesContainer>
        {messages.map((msg, idx) => (
          <div key={idx}>
            <AiMessageBubble message={msg} />
            {msg.role === 'assistant' && onSqlGenerated && (
              <AiSqlPreview sql={msg.content} onCopyToEditor={onSqlGenerated} />
            )}
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
          placeholder={t('Ask a question about your data...')}
          disabled={loading}
        />
        <SendButton onClick={handleSend} disabled={loading || !inputValue.trim()}>
          {t('Send')}
        </SendButton>
      </InputContainer>
    </PanelContainer>
  );
}
