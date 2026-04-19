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
import type { AiStep } from '../types';

interface AiStepProgressProps {
  steps: AiStep[];
  /**
   * When true, keeps all steps and thinking detail expanded while the agent is
   * running. Historical steps start collapsed and can be expanded manually.
   */
  isLive?: boolean;
}

const Container = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: ${({ theme }) => theme.borderRadiusLG}px;
  overflow: hidden;
`;

/** Remove outer border/margin when rendered inside LiveResponseArea */
const LiveContainer = styled.div`
  padding: 4px 0;
`;

const Header = styled.div`
  padding: 8px 12px;
  background: ${({ theme }) => theme.colorFillQuaternary};
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: ${({ theme }) => theme.colorTextSecondary};
  user-select: none;
`;

const StepList = styled.div`
  padding: 4px 0;
`;

const StepRow = styled.div<{ $status: AiStep['status'] }>`
  padding: 4px 12px;
  font-size: 12px;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: ${({ $status, theme }) => {
    switch ($status) {
      case 'error':
        return theme.colorError;
      case 'running':
        return theme.colorPrimary;
      default:
        return theme.colorTextTertiary;
    }
  }};
`;

const Icon = styled.span<{ $status: AiStep['status'] }>`
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 11px;

  ${({ $status }) =>
    $status === 'running' &&
    `
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    animation: pulse 1.5s ease-in-out infinite;
  `}
`;

const Detail = styled.div`
  color: ${({ theme }) => theme.colorTextQuaternary};
  font-family: ${({ theme }) => theme.fontFamilyCode};
  font-size: 11px;
  margin-left: 20px;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 500px;
`;

const ThinkingToggle = styled.button`
  background: none;
  border: none;
  color: ${({ theme }) => theme.colorPrimary};
  font-size: 11px;
  cursor: pointer;
  padding: 0 12px 4px 32px;
  display: block;
  font-family: inherit;

  &:hover {
    text-decoration: underline;
  }
`;

const ThinkingBlock = styled.div`
  margin: 0 12px 4px 32px;
  padding: 6px 8px;
  background: ${({ theme }) => theme.colorFillQuaternary};
  border-radius: ${({ theme }) => theme.borderRadiusSM}px;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 11px;
  line-height: 1.5;
  max-height: 160px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
`;

function getIcon(status: AiStep['status']): string {
  switch (status) {
    case 'done':
      return '✓';
    case 'running':
      return '●';
    case 'error':
      return '✗';
    default:
      return '·';
  }
}

function isThinkingDetail(detail: string | undefined): detail is string {
  return !!detail && detail.length > 0;
}

function isLongThinking(detail: string | undefined): boolean {
  return !!detail && detail.length > 40;
}

export function AiStepProgress({ steps, isLive }: AiStepProgressProps) {
  const [expanded, setExpanded] = useState(false);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(
    new Set(),
  );

  if (steps.length === 0) {
    return null;
  }

  const doneCount = steps.filter(s => s.status === 'done').length;
  const totalCount = steps.length;
  const showStepList = isLive || expanded;

  const toggleThinking = (stepId: string) => {
    setExpandedThinking(prev => {
      const next = new Set(prev);
      if (next.has(stepId)) {
        next.delete(stepId);
      } else {
        next.add(stepId);
      }
      return next;
    });
  };

  const Wrapper = isLive ? LiveContainer : Container;

  return (
    <Wrapper>
      {!isLive && (
        <Header onClick={() => setExpanded(prev => !prev)}>
          <span>
            {t('Steps')} ({doneCount}/{totalCount})
          </span>
          <span>{expanded ? '▲' : '▼'}</span>
        </Header>
      )}
      {showStepList && (
        <StepList>
          {steps.map(step => {
            const hasThinkingDetail =
              step.type === 'thinking' && isThinkingDetail(step.detail);
            const isLong = isLongThinking(step.detail);
            const thinkingOpen = isLive || expandedThinking.has(step.id);

            return (
              <div key={step.id}>
                <StepRow $status={step.status}>
                  <Icon $status={step.status}>{getIcon(step.status)}</Icon>
                  <span>{step.label}</span>
                </StepRow>
                {step.type === 'tool_call' && step.detail && (
                  <Detail>{step.detail}</Detail>
                )}
                {hasThinkingDetail && !isLong && <Detail>{step.detail}</Detail>}
                {hasThinkingDetail && isLong && !isLive && (
                  <ThinkingToggle
                    type="button"
                    onClick={() => toggleThinking(step.id)}
                  >
                    {thinkingOpen ? '收起思考' : '展开思考'}
                  </ThinkingToggle>
                )}
                {hasThinkingDetail && isLong && thinkingOpen && (
                  <ThinkingBlock>{step.detail}</ThinkingBlock>
                )}
              </div>
            );
          })}
        </StepList>
      )}
    </Wrapper>
  );
}
