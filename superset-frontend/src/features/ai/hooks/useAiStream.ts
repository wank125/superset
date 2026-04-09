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

import { useCallback, useEffect, useRef, useState } from 'react';
import type { AiStreamEvent } from '../types';
import { fetchEvents } from '../api/aiClient';

const POLL_INTERVAL_MS = 500;

interface UseAiStreamOptions {
  channelId: string | null;
  onComplete?: () => void;
  onError?: (message: string) => void;
}

export function useAiStream({ channelId, onComplete, onError }: UseAiStreamOptions) {
  const [events, setEvents] = useState<AiStreamEvent[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const lastIdRef = useRef('0');
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!channelId) {
      stopPolling();
      return;
    }

    lastIdRef.current = '0';
    setEvents([]);
    setStreamingText('');

    const poll = () => {
      fetchEvents(channelId!, lastIdRef.current).then(response => {
        if (response.events.length > 0) {
          setEvents(prev => [...prev, ...response.events]);
          lastIdRef.current = response.last_id;

          let textAccum = '';
          for (const event of response.events) {
            if (event.type === 'text_chunk' && event.data.content) {
              textAccum += event.data.content as string;
            }
            if (event.type === 'error') {
              onError?.(event.data.message as string || 'Unknown error');
              stopPolling();
              return;
            }
            if (event.type === 'done') {
              onComplete?.();
              stopPolling();
              return;
            }
          }
          if (textAccum) {
            setStreamingText(prev => prev + textAccum);
          }
        }
      });
    };

    timerRef.current = setInterval(poll, POLL_INTERVAL_MS);
    poll(); // Initial poll

    return () => stopPolling();
  }, [channelId, onComplete, onError, stopPolling]);

  return { events, streamingText };
}
