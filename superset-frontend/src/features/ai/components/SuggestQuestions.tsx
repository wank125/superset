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

interface SuggestQuestionsProps {
  questions: string[];
  onSelect: (question: string) => void;
}

const Wrapper = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0 4px;
`;

const Chip = styled.button`
  background: ${({ theme }) => theme.colorBgTextHover};
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 14px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
  color: ${({ theme }) => theme.colorText};
  white-space: nowrap;

  &:hover {
    border-color: ${({ theme }) => theme.colorPrimary};
    color: ${({ theme }) => theme.colorPrimary};
  }
`;

export function SuggestQuestions({ questions, onSelect }: SuggestQuestionsProps) {
  if (!questions.length) return null;

  return (
    <Wrapper>
      {questions.map((q, i) => (
        <Chip key={i} type="button" onClick={() => onSelect(q)}>
          {q}
        </Chip>
      ))}
    </Wrapper>
  );
}
