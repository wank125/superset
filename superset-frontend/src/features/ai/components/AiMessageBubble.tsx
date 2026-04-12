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

interface AiMessageBubbleProps {
  message: AiChatMessage;
}

const Bubble = styled.div<{ isUser: boolean }>`
  padding: 8px 12px;
  border-radius: 8px;
  max-width: 85%;
  margin-bottom: 8px;
  align-self: ${({ isUser }) => (isUser ? 'flex-end' : 'flex-start')};
  background: ${({ isUser, theme }) =>
    isUser ? theme.colorPrimary : theme.colorBgLayout};
  color: ${({ isUser, theme }) =>
    isUser ? theme.colorWhite : theme.colorText};
  word-break: break-word;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.5;
`;

export function AiMessageBubble({ message }: AiMessageBubbleProps) {
  const isUser = message.role === 'user';
  return <Bubble isUser={isUser}>{message.content}</Bubble>;
}
