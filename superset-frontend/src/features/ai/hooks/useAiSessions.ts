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

import { useState, useCallback, useMemo, useEffect } from 'react';
import type { AiChatMessage, AiSession } from 'src/features/ai/types';

const STORAGE_KEY = 'ai_assistant__sessions';
const ACTIVE_KEY = 'ai_assistant__active_session_id';
const MESSAGES_KEY_PREFIX = 'ai_assistant__messages_';
const MAX_SESSIONS = 50;

function loadSessions(): AiSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveSessions(sessions: AiSession[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function generateSessionId(): string {
  return `ai-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export default function useAiSessions() {
  const [sessions, setSessions] = useState<AiSession[]>(() =>
    loadSessions().sort((a, b) => b.updatedAt - a.updatedAt),
  );
  const [activeSessionId, setActiveSessionId] = useState<string | null>(
    () => localStorage.getItem(ACTIVE_KEY),
  );
  const [searchQuery, setSearchQuery] = useState('');

  // Persist sessions to localStorage
  useEffect(() => {
    saveSessions(sessions);
  }, [sessions]);

  // Persist active session id
  useEffect(() => {
    if (activeSessionId) {
      localStorage.setItem(ACTIVE_KEY, activeSessionId);
    } else {
      localStorage.removeItem(ACTIVE_KEY);
    }
  }, [activeSessionId]);

  const activeSession = useMemo(
    () => sessions.find(s => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter(s => s.title.toLowerCase().includes(q));
  }, [sessions, searchQuery]);

  const createSession = useCallback(
    (databaseId: number | null, agentType = 'data_assistant'): string => {
      const id = generateSessionId();
      const now = Date.now();
      const session: AiSession = {
        id,
        title: '新对话',
        databaseId,
        agentType,
        createdAt: now,
        updatedAt: now,
      };
      setSessions(prev => {
        const next = [session, ...prev];
        return next.slice(0, MAX_SESSIONS);
      });
      setActiveSessionId(id);
      return id;
    },
    [],
  );

  const deleteSession = useCallback(
    (id: string) => {
      setSessions(prev => prev.filter(s => s.id !== id));
      // Clean up stored messages
      localStorage.removeItem(`${MESSAGES_KEY_PREFIX}${id}`);
      if (activeSessionId === id) {
        setActiveSessionId(prev => {
          const remaining = sessions.filter(s => s.id !== id);
          return remaining.length > 0 ? remaining[0].id : null;
        });
      }
    },
    [activeSessionId, sessions],
  );

  const updateSession = useCallback(
    (id: string, patch: Partial<AiSession>) => {
      setSessions(prev =>
        prev.map(s =>
          s.id === id ? { ...s, ...patch, updatedAt: Date.now() } : s,
        ),
      );
    },
    [],
  );

  const getSessionMessages = useCallback((id: string): AiChatMessage[] => {
    try {
      const raw = localStorage.getItem(`${MESSAGES_KEY_PREFIX}${id}`);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  }, []);

  const saveSessionMessages = useCallback(
    (id: string, msgs: AiChatMessage[]) => {
      localStorage.setItem(`${MESSAGES_KEY_PREFIX}${id}`, JSON.stringify(msgs));
    },
    [],
  );

  return {
    sessions,
    filteredSessions,
    activeSession,
    activeSessionId,
    setActiveSessionId,
    createSession,
    deleteSession,
    updateSession,
    searchQuery,
    setSearchQuery,
    getSessionMessages,
    saveSessionMessages,
  };
}
