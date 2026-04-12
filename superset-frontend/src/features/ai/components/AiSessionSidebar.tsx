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

import { styled, t } from '@superset-ui/core';
import type { AiSession } from 'src/features/ai/types';

interface AiSessionSidebarProps {
  sessions: AiSession[];
  activeSessionId: string | null;
  collapsed: boolean;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onToggleCollapse: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
}

const SidebarRoot = styled.div<{ collapsed: boolean }>`
  width: ${({ collapsed }) => (collapsed ? '52px' : '260px')};
  flex-shrink: 0;
  background: ${({ theme }) => theme.colorBgContainer};
  border-right: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width ${({ theme }) => theme.motionDurationMid};
`;

const SidebarHeader = styled.div`
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid ${({ theme }) => theme.colorBorderSecondary};
`;

const NewChatBtn = styled.button`
  flex: 1;
  padding: 6px 12px;
  background: ${({ theme }) => theme.colorPrimary};
  color: ${({ theme }) => theme.colorWhite};
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  white-space: nowrap;

  &:hover {
    opacity: 0.9;
  }
`;

const CollapseBtn = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 16px;
  flex-shrink: 0;
  padding: 4px;

  &:hover {
    color: ${({ theme }) => theme.colorText};
  }
`;

const SearchInput = styled.input`
  margin: 8px 12px;
  padding: 6px 10px;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 4px;
  font-size: 12px;
  outline: none;
  width: calc(100% - 24px);
  box-sizing: border-box;

  &:focus {
    border-color: ${({ theme }) => theme.colorPrimary};
  }
`;

const SessionList = styled.div`
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px;
`;

const SessionItem = styled.div<{ active: boolean }>`
  padding: 8px 10px;
  border-radius: 4px;
  cursor: pointer;
  margin-bottom: 2px;
  background: ${({ active, theme }) =>
    active ? theme.colorPrimaryBg : 'transparent'};
  border-left: ${({ active, theme }) =>
    active ? `3px solid ${theme.colorPrimary}` : '3px solid transparent'};
  display: flex;
  align-items: center;
  gap: 8px;

  &:hover {
    background: ${({ active, theme }) =>
      active ? theme.colorPrimaryBg : theme.colorFillQuaternary};
  }
`;

const SessionTitle = styled.div`
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
`;

const SessionTime = styled.span`
  font-size: 11px;
  color: ${({ theme }) => theme.colorTextSecondary};
  flex-shrink: 0;
`;

const DeleteBtn = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 12px;
  opacity: 0.3;
  padding: 2px;
  transition: opacity 0.2s;

  &:hover {
    opacity: 1;
    color: ${({ theme }) => theme.colorError};
  }
`;

const EmptyState = styled.div`
  padding: 24px 12px;
  text-align: center;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 13px;
`;

const CollapsedIcon = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 18px;
  padding: 4px;
  width: 100%;
  text-align: center;

  &:hover {
    color: ${({ theme }) => theme.colorPrimary};
  }
`;

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return t('刚刚');
  if (minutes < 60) return `${minutes} ${t('分钟前')}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${t('小时前')}`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} ${t('天前')}`;
  return new Date(timestamp).toLocaleDateString();
}

export function AiSessionSidebar({
  sessions,
  activeSessionId,
  collapsed,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onToggleCollapse,
  searchQuery,
  onSearchChange,
}: AiSessionSidebarProps) {
  if (collapsed) {
    return (
      <SidebarRoot collapsed>
        <CollapsedIcon onClick={onNewSession} title={t('新建对话')}>
          +
        </CollapsedIcon>
        <CollapsedIcon onClick={onToggleCollapse} title={t('展开')}>
          ≡
        </CollapsedIcon>
      </SidebarRoot>
    );
  }

  return (
    <SidebarRoot collapsed={false}>
      <SidebarHeader>
        <NewChatBtn onClick={onNewSession}>+ {t('新对话')}</NewChatBtn>
        <CollapseBtn onClick={onToggleCollapse} title={t('收起')}>
          ◀
        </CollapseBtn>
      </SidebarHeader>
      <SearchInput
        placeholder={t('搜索对话...')}
        value={searchQuery}
        onChange={e => onSearchChange(e.target.value)}
      />
      <SessionList>
        {sessions.length === 0 ? (
          <EmptyState>{t('暂无对话')}</EmptyState>
        ) : (
          sessions.map(session => (
            <SessionItem
              key={session.id}
              active={session.id === activeSessionId}
              onClick={() => onSelectSession(session.id)}
            >
              <SessionTitle>{session.title}</SessionTitle>
              <SessionTime>{formatRelativeTime(session.updatedAt)}</SessionTime>
              <DeleteBtn
                onClick={e => {
                  e.stopPropagation();
                  onDeleteSession(session.id);
                }}
                title={t('删除')}
              >
                ✕
              </DeleteBtn>
            </SessionItem>
          ))
        )}
      </SessionList>
    </SidebarRoot>
  );
}
