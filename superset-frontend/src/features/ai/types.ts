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

export type AgentEventType =
  | 'thinking'
  | 'text_chunk'
  | 'tool_call'
  | 'tool_result'
  | 'sql_generated'
  | 'done'
  | 'error';

export interface AiStreamEvent {
  id: string;
  type: AgentEventType;
  data: Record<string, unknown>;
}

export interface AiChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export interface AiChatRequest {
  message: string;
  database_id: number;
  agent_type?: string;
  session_id?: string;
}

export interface AiChatResponse {
  channel_id: string;
}

export interface AiEventsResponse {
  events: AiStreamEvent[];
  last_id: string;
}
