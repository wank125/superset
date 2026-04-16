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

import { useState } from 'react';
import { styled, t } from '@superset-ui/core';
import { Input } from '@superset-ui/core/components';
import { Button } from '@superset-ui/core/components/Button';
import { Icons } from '@superset-ui/core/components/Icons';
import { Radio, RadioChangeEvent } from '@superset-ui/core/components/Radio';
import { AGENT_MODES_WITH_AUTO, ROUTED_LABELS } from '../types';

interface AiWorkspaceInputProps {
  onSend: (message: string) => void;
  loading: boolean;
  agentType: string;
  routedAgent: string | null;
  onAgentTypeChange: (type: string) => void;
}

const InputBar = styled.div`
  flex-shrink: 0;
  padding: 12px 24px 16px;
  width: 100%;
  box-sizing: border-box;
`;

const Composer = styled.div`
  width: 100%;
  max-width: 768px;
  margin: 0 auto;
  box-sizing: border-box;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: ${({ theme }) => theme.borderRadiusLG}px;
  background: ${({ theme }) => theme.colorBgContainer};
  padding: 10px 12px;
`;

const MessageInput = styled(Input)`
  border: 0;
  box-shadow: none;
  padding: 0;
  background: transparent;

  &:focus,
  &:hover {
    border: 0;
    box-shadow: none;
  }
`;

const ToolbarRow = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
`;

const ModeGroup = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
`;

const RoutedBadge = styled.span`
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: ${({ theme }) => theme.colorPrimaryBg};
  color: ${({ theme }) => theme.colorPrimary};
  white-space: nowrap;
`;

const SendIcon = styled.span`
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 0;
`;

const SendButton = styled(Button)`
  min-width: 30px;
  width: 30px;
  height: 30px;
  padding: 0;

  .anticon {
    margin: 0;
  }
`;

const AttachmentButton = styled(Button)`
  min-width: 24px;
  width: 24px;
  height: 24px;
  padding: 0;

  .anticon {
    margin: 0;
  }
`;

export function AiWorkspaceInput({
  onSend,
  loading,
  agentType,
  routedAgent,
  onAgentTypeChange,
}: AiWorkspaceInputProps) {
  const [inputValue, setInputValue] = useState('');

  const handleSend = () => {
    if (!inputValue.trim() || loading) {
      return;
    }
    onSend(inputValue.trim());
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
      ? t('描述你想创建的仪表板...')
      : agentType === 'chart'
        ? t('描述你想创建的图表...')
        : t('输入你的问题...');

  return (
    <InputBar>
      <Composer>
        <MessageInput
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={loading}
        />
        <ToolbarRow>
          <ModeGroup>
            <AttachmentButton
              buttonSize="small"
              buttonStyle="secondary"
              aria-label={t('添加附件')}
              disabled={loading}
              icon={<Icons.PlusOutlined iconSize="s" />}
            />
            <Radio.Group
              optionType="button"
              buttonStyle="solid"
              size="small"
              options={AGENT_MODES_WITH_AUTO}
              value={agentType}
              onChange={(e: RadioChangeEvent) =>
                onAgentTypeChange(e.target.value)
              }
            />
            {agentType === 'auto' && routedAgent && (
              <RoutedBadge>
                {t('已路由')}: {ROUTED_LABELS[routedAgent] || routedAgent}
              </RoutedBadge>
            )}
          </ModeGroup>
          <SendButton
            buttonSize="small"
            buttonStyle="primary"
            aria-label={t('发送')}
            icon={
              <SendIcon>
                <svg
                  aria-hidden="true"
                  focusable="false"
                  width="14"
                  height="14"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <path
                    d="M8 13V3M4 7l4-4 4 4"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </SendIcon>
            }
            onClick={handleSend}
            disabled={loading || !inputValue.trim()}
          />
        </ToolbarRow>
      </Composer>
    </InputBar>
  );
}
