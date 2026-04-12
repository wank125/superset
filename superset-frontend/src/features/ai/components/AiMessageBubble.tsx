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
import type { AiChatMessage } from '../types';
import { AiMarkdown } from './AiMarkdown';

interface AiMessageBubbleProps {
  message: AiChatMessage;
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

export function AiMessageBubble({ message }: AiMessageBubbleProps) {
  const isUser = message.role === 'user';
  return (
    <Bubble isUser={isUser}>
      <AiMarkdown content={message.content} />
    </Bubble>
  );
}
