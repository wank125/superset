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
import type { AiChatMessage, ChartPreviewData } from '../types';
import { AiMarkdown } from './AiMarkdown';
import { AiInlineChart } from './AiInlineChart';
import { AiChartPreview } from './AiChartPreview';

interface AiMessageBubbleProps {
  message: AiChatMessage;
  onSuggestQuestion?: (question: string) => void;
  onSaveChart?: (preview: ChartPreviewData) => Promise<void>;
}

const Bubble = styled.div<{ isUser: boolean }>`
  padding: ${({ isUser }) => (isUser ? '8px 12px' : 0)};
  border-radius: 8px;
  width: ${({ isUser }) => (isUser ? 'fit-content' : '100%')};
  max-width: ${({ isUser }) => (isUser ? '85%' : '100%')};
  margin-bottom: 8px;
  margin-left: ${({ isUser }) => (isUser ? 'auto' : 0)};
  box-sizing: border-box;
  background: ${({ isUser, theme }) =>
    isUser ? theme.colorPrimary : theme.colorBgLayout};
  color: ${({ isUser, theme }) =>
    isUser ? theme.colorWhite : theme.colorText};
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;

  ${({ isUser }) =>
    !isUser &&
    `
      > div > p,
      > div > ul,
      > div > ol,
      > div > h1,
      > div > h2,
      > div > h3,
      > div > h4 {
        padding-left: 12px;
        padding-right: 12px;
      }
    `}
`;

export function AiMessageBubble({
  message,
  onSuggestQuestion,
  onSaveChart,
}: AiMessageBubbleProps) {
  const isUser = message.role === 'user';
  return (
    <Bubble isUser={isUser}>
      <AiMarkdown content={message.content} />
      {/* Agent-driven chart previews (chart/dashboard mode) */}
      {message.role === 'assistant' &&
        message.chartPreviews &&
        message.chartPreviews.length > 0 && (
          <div style={{ padding: '0 12px 8px' }}>
            {message.chartPreviews.map((preview, idx) => (
              <AiChartPreview
                key={idx}
                preview={preview}
                onSuggestQuestion={onSuggestQuestion}
                onSave={onSaveChart}
              />
            ))}
          </div>
        )}
      {/* Data assistant inline chart (data_assistant mode) */}
      {message.role === 'assistant' &&
        message.queryResult &&
        !message.chartPreviews?.length && (
          <div style={{ padding: '0 12px 8px' }}>
            <AiInlineChart
              result={message.queryResult}
              insight={message.queryResult.insight}
              suggestQuestions={message.suggestQuestions}
              onSuggestQuestion={onSuggestQuestion}
            />
          </div>
        )}
    </Bubble>
  );
}
