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
}

const Container = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: ${({ theme }) => theme.borderRadiusLG}px;
  overflow: hidden;
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

const Detail = styled.span`
  color: ${({ theme }) => theme.colorTextQuaternary};
  font-family: ${({ theme }) => theme.fontFamilyCode};
  font-size: 11px;
  margin-left: 20px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
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

const COLLAPSE_THRESHOLD = 8;
const VISIBLE_TAIL = 5;

export function AiStepProgress({ steps }: AiStepProgressProps) {
  const [expanded, setExpanded] = useState(false);

  if (steps.length === 0) {
    return null;
  }

  const doneCount = steps.filter(s => s.status === 'done').length;
  const totalCount = steps.length;
  const shouldCollapse =
    !expanded && steps.length > COLLAPSE_THRESHOLD;
  const visibleSteps = shouldCollapse
    ? steps.slice(-VISIBLE_TAIL)
    : steps;

  return (
    <Container>
      <Header onClick={() => setExpanded(prev => !prev)}>
        <span>
          {t('Steps')} ({doneCount}/{totalCount})
        </span>
        <span>{expanded ? '▲' : '▼'}</span>
      </Header>
      <StepList>
        {shouldCollapse && (
          <StepRow $status="done">
            <Icon $status="done">…</Icon>
            <span>
              {t('%d earlier steps hidden', steps.length - VISIBLE_TAIL)}
            </span>
          </StepRow>
        )}
        {visibleSteps.map(step => (
          <div key={step.id}>
            <StepRow $status={step.status}>
              <Icon $status={step.status}>{getIcon(step.status)}</Icon>
              <span>{step.label}</span>
            </StepRow>
            {step.detail && (
              <Detail>{step.detail}</Detail>
            )}
          </div>
        ))}
      </StepList>
    </Container>
  );
}
