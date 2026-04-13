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

import { styled } from '@superset-ui/core';
import { Button } from '@superset-ui/core/components/Button';
import type { ClarifyState } from '../types';

interface AiClarifyOptionsProps {
  clarifyState: ClarifyState;
  onSelect: (value: string) => void;
  onDismiss: () => void;
}

const Container = styled.div`
  margin: 12px 0;
  border: 1px solid ${({ theme }) => theme.colorPrimaryBorder};
  border-radius: ${({ theme }) => theme.borderRadiusLG}px;
  background: ${({ theme }) => theme.colorPrimaryBg};
  padding: 12px 16px;
`;

const Question = styled.div`
  font-size: 14px;
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  margin-bottom: 10px;
  color: ${({ theme }) => theme.colorText};
`;

const OptionsList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
`;

const OptionButton = styled.button`
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: ${({ theme }) => theme.borderRadius}px;
  background: ${({ theme }) => theme.colorBgContainer};
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: ${({ theme }) => theme.colorText};
  transition: border-color 0.2s, background 0.2s;

  &:hover {
    border-color: ${({ theme }) => theme.colorPrimary};
    background: ${({ theme }) => theme.colorPrimaryBgHover};
  }
`;

const OptionLabel = styled.span`
  font-weight: ${({ theme }) => theme.fontWeightStrong};
`;

const OptionDesc = styled.span`
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 12px;
`;

const DismissRow = styled.div`
  display: flex;
  justify-content: flex-end;
`;

export function AiClarifyOptions({
  clarifyState,
  onSelect,
  onDismiss,
}: AiClarifyOptionsProps) {
  const { question, options } = clarifyState;

  return (
    <Container>
      <Question>{question}</Question>
      {options.length > 0 && (
        <OptionsList>
          {options.map(opt => (
            <OptionButton
              key={opt.value}
              type="button"
              onClick={() => onSelect(opt.value)}
            >
              <OptionLabel>{opt.label}</OptionLabel>
              {opt.description && <OptionDesc>{opt.description}</OptionDesc>}
            </OptionButton>
          ))}
        </OptionsList>
      )}
      <DismissRow>
        <Button buttonSize="small" buttonStyle="link" onClick={onDismiss}>
          取消
        </Button>
      </DismissRow>
    </Container>
  );
}
