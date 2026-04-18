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

import { useState, useCallback, useEffect } from 'react';
import { styled, SupersetClient } from '@superset-ui/core';
import type { AiChatMessage } from '../types';
import useAiSessions from '../hooks/useAiSessions';
import { useAiChat } from '../hooks/useAiChat';
import { AiSessionSidebar } from './AiSessionSidebar';
import { AiWorkspaceContent } from './AiWorkspaceContent';
import { AiWorkspaceInput } from './AiWorkspaceInput';
import { AiNewSessionModal } from './AiNewSessionModal';

const WorkspaceRoot = styled.div`
  display: flex;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: ${({ theme }) => theme.colorBgLayout};
`;

const MainColumn = styled.div`
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
`;

interface DatabaseOption {
  id: number;
  database_name: string;
}

export function AiWorkspace() {
  const {
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
  } = useAiSessions();

  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('ai_assistant__sidebar_collapsed') === 'true',
  );
  const [showNewSessionModal, setShowNewSessionModal] = useState(false);
  const [databases, setDatabases] = useState<DatabaseOption[]>([]);

  // Fetch real database list from API
  useEffect(() => {
    SupersetClient.get({
      endpoint: '/api/v1/database/?q=(page_size:100)',
    })
      .then(({ json }) => {
        const result = (json?.result ?? []) as DatabaseOption[];
        setDatabases(result);
      })
      .catch(() => {
        setDatabases([]);
      });
  }, []);

  const databaseId = activeSession?.databaseId ?? null;
  const agentType = activeSession?.agentType ?? 'auto';

  return (
    <WorkspaceRoot>
      <AiSessionSidebar
        sessions={filteredSessions}
        activeSessionId={activeSessionId}
        collapsed={sidebarCollapsed}
        onSelectSession={setActiveSessionId}
        onNewSession={() => setShowNewSessionModal(true)}
        onDeleteSession={deleteSession}
        onToggleCollapse={() =>
          setSidebarCollapsed(prev => {
            const next = !prev;
            localStorage.setItem(
              'ai_assistant__sidebar_collapsed',
              String(next),
            );
            return next;
          })
        }
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />
      <MainColumn>
        {activeSessionId ? (
          <AiChatSession
            key={activeSessionId}
            sessionId={activeSessionId}
            databaseId={databaseId}
            agentType={agentType}
            onTitleUpdate={title => updateSession(activeSessionId, { title })}
            onAgentTypeChange={type =>
              updateSession(activeSessionId, { agentType: type })
            }
            initialMessages={getSessionMessages(activeSessionId)}
            onSaveMessages={msgs => saveSessionMessages(activeSessionId, msgs)}
          />
        ) : (
          <AiWorkspaceContent
            messages={[]}
            streamingText=""
            loading={false}
            steps={[]}
            chartResults={[]}
            dashboardResult={null}
            sqlPreview={null}
            agentType="auto"
            routedAgent={null}
            clarifyState={null}
          />
        )}
      </MainColumn>
      <AiNewSessionModal
        visible={showNewSessionModal}
        databases={databases}
        onCreate={(dbId, agentType) => {
          createSession(dbId, agentType);
          setShowNewSessionModal(false);
        }}
        onCancel={() => setShowNewSessionModal(false)}
      />
    </WorkspaceRoot>
  );
}

/** Inner component that owns a single chat session. Remounted on session switch via key. */
interface AiChatSessionProps {
  sessionId: string;
  databaseId: number | null;
  agentType: string;
  onTitleUpdate: (title: string) => void;
  onAgentTypeChange: (type: string) => void;
  initialMessages: AiChatMessage[];
  onSaveMessages: (msgs: AiChatMessage[]) => void;
}

function AiChatSession({
  sessionId,
  databaseId,
  agentType,
  onTitleUpdate,
  onAgentTypeChange,
  initialMessages,
  onSaveMessages,
}: AiChatSessionProps) {
  const {
    messages,
    loading,
    streamingText,
    sendMessage,
    steps,
    chartResults,
    dashboardResult,
    sqlPreview,
    routedAgent,
    clarifyState,
    answerClarify,
    dismissClarify,
    saveChart,
  } = useAiChat(databaseId, agentType, sessionId, initialMessages);

  const handleSend = useCallback(
    (message: string) => {
      sendMessage(message);
      // Update session title from first message
      if (initialMessages.length === 0) {
        onTitleUpdate(
          message.slice(0, 40) + (message.length > 40 ? '...' : ''),
        );
      }
    },
    [sendMessage, initialMessages.length, onTitleUpdate],
  );

  // Persist messages to localStorage whenever they change
  useEffect(() => {
    if (messages.length > 0) {
      onSaveMessages(messages);
    }
  }, [messages, onSaveMessages]);

  return (
    <>
      <AiWorkspaceContent
        messages={messages}
        streamingText={streamingText}
        loading={loading}
        steps={steps}
        chartResults={chartResults}
        dashboardResult={dashboardResult}
        sqlPreview={sqlPreview}
        agentType={agentType}
        routedAgent={routedAgent}
        clarifyState={clarifyState}
        onClarifyAnswer={answerClarify}
        onClarifyDismiss={dismissClarify}
        onSendMessage={sendMessage}
        onSaveChart={saveChart}
      />
      <AiWorkspaceInput
        onSend={handleSend}
        loading={loading}
        agentType={agentType}
        routedAgent={routedAgent}
        onAgentTypeChange={onAgentTypeChange}
      />
    </>
  );
}
