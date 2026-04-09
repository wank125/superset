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

import { SupersetClient } from '@superset-ui/core';
import type {
  AiChatRequest,
  AiChatResponse,
  AiEventsResponse,
} from '../types';

export function sendChat(payload: AiChatRequest): Promise<AiChatResponse> {
  return SupersetClient.post({
    endpoint: '/api/v1/ai/chat/',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(({ json }) => json as AiChatResponse);
}

export function fetchEvents(
  channelId: string,
  lastId: string,
): Promise<AiEventsResponse> {
  return SupersetClient.get({
    endpoint: `/api/v1/ai/events/?channel_id=${channelId}&last_id=${lastId}`,
  }).then(({ json }) => json as AiEventsResponse);
}
