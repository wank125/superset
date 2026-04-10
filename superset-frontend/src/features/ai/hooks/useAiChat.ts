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

import { useCallback, useRef, useState } from 'react';
import type { AiChatMessage } from '../types';
import { sendChat, fetchEvents } from '../api/aiClient';

const POLL_INTERVAL_MS = 500;
const MAX_POLL_ATTEMPTS = 120; // 60 seconds

/** Generate a stable session ID that persists for the lifetime of the hook. */
function createSessionId(): string {
  return `ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function useAiChat(databaseId: number) {
  const [messages, setMessages] = useState<AiChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessionIdRef = useRef<string>(createSessionId());
  // Ref to track accumulated streaming text without relying on setState updater
  // to avoid React 18 Strict Mode double-invocation of updater functions
  // causing duplicate messages when setMessages is nested inside setStreamingText.
  const streamingTextRef = useRef('');

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const pollEvents = useCallback(
    (channelId: string, lastId: string, attempt: number) => {
      if (attempt >= MAX_POLL_ATTEMPTS) {
        const accumulated = streamingTextRef.current;
        if (accumulated) {
          setMessages(msgs => [
            ...msgs,
            { role: 'assistant' as const, content: accumulated, timestamp: Date.now() },
          ]);
        }
        streamingTextRef.current = '';
        setStreamingText('');
        setLoading(false);
        return;
      }

      fetchEvents(channelId, lastId)
        .then(response => {
          let newChunkText = '';
          for (const event of response.events) {
            if (event.type === 'text_chunk' && event.data.content) {
              newChunkText += event.data.content as string;
            }
            if (event.type === 'error') {
              const accumulated = streamingTextRef.current;
              if (accumulated) {
                setMessages(msgs => [
                  ...msgs,
                  { role: 'assistant' as const, content: accumulated, timestamp: Date.now() },
                ]);
              }
              streamingTextRef.current = '';
              setStreamingText('');
              setMessages(prev => [
                ...prev,
                {
                  role: 'assistant' as const,
                  content: `Error: ${event.data.message || 'Unknown error'}`,
                  timestamp: Date.now(),
                },
              ]);
              setLoading(false);
              stopPolling();
              return;
            }
            if (event.type === 'done') {
              const fullText = streamingTextRef.current + newChunkText;
              if (fullText) {
                setMessages(msgs => [
                  ...msgs,
                  { role: 'assistant' as const, content: fullText, timestamp: Date.now() },
                ]);
              }
              streamingTextRef.current = '';
              setStreamingText('');
              setLoading(false);
              stopPolling();
              return;
            }
          }

          // No done event yet — accumulate chunk text for next poll
          if (newChunkText) {
            streamingTextRef.current += newChunkText;
            setStreamingText(streamingTextRef.current);
          }

          // Continue polling
          pollTimerRef.current = setTimeout(
            () => pollEvents(channelId, response.last_id, attempt + 1),
            POLL_INTERVAL_MS,
          ) as unknown as ReturnType<typeof setInterval>;
        })
        .catch(() => {
          // On network/server error, retry with back-off instead of hanging
          const backoff = Math.min(POLL_INTERVAL_MS * (attempt + 1), 5000);
          pollTimerRef.current = setTimeout(
            () => pollEvents(channelId, lastId, attempt + 1),
            backoff,
          ) as unknown as ReturnType<typeof setInterval>;
        });
    },
    [stopPolling],
  );

  const sendMessage = useCallback(
    async (message: string) => {
      if (!message.trim() || loading) return;

      setMessages(prev => [
        ...prev,
        { role: 'user', content: message, timestamp: Date.now() },
      ]);
      setLoading(true);
      streamingTextRef.current = '';
      setStreamingText('');

      try {
        const { channel_id: channelId } = await sendChat({
          message,
          database_id: databaseId,
          session_id: sessionIdRef.current,
        });
        pollEvents(channelId, '0', 0);
      } catch (err) {
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant' as const,
            content: `Failed to send message: ${err}`,
            timestamp: Date.now(),
          },
        ]);
        setLoading(false);
      }
    },
    [databaseId, loading, pollEvents],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    streamingTextRef.current = '';
    setStreamingText('');
    stopPolling();
    // Reset session for a fresh conversation
    sessionIdRef.current = createSessionId();
  }, [stopPolling]);

  return { messages, loading, streamingText, sendMessage, clearMessages };
}
