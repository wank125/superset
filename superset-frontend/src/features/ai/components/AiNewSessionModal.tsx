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

import { useState } from 'react';
import { styled, t } from '@superset-ui/core';
import { Modal, Select } from '@superset-ui/core/components';
import { Radio, RadioChangeEvent } from '@superset-ui/core/components/Radio';

interface AiNewSessionModalProps {
  visible: boolean;
  databases: { id: number; database_name: string }[];
  onCreate: (databaseId: number | null, agentType: string) => void;
  onCancel: () => void;
}

const AgentModeGroup = styled.div`
  margin-bottom: 16px;
`;

const AGENT_MODES = [
  { label: 'Auto', value: 'auto' },
  { label: 'SQL', value: 'nl2sql' },
  { label: 'Chart', value: 'chart' },
  { label: 'Dashboard', value: 'dashboard' },
  { label: 'Copilot', value: 'copilot' },
];

export function AiNewSessionModal({
  visible,
  databases,
  onCreate,
  onCancel,
}: AiNewSessionModalProps) {
  const [selectedDb, setSelectedDb] = useState<number | undefined>(undefined);
  const [agentType, setAgentType] = useState('auto');

  const isCopilot = agentType === 'copilot';
  const isAuto = agentType === 'auto';

  const handlePrimaryAction = () => {
    onCreate(selectedDb ?? null, agentType);
    setSelectedDb(undefined);
    setAgentType('auto');
  };

  const handleHide = () => {
    setSelectedDb(undefined);
    setAgentType('auto');
    onCancel();
  };

  const databaseOptions = databases.map(db => ({
    value: db.id,
    label: db.database_name,
  }));

  return (
    <Modal
      show={visible}
      name={t('新建对话')}
      title={t('新建对话')}
      onHide={handleHide}
      onHandledPrimaryAction={handlePrimaryAction}
      primaryButtonName={t('创建')}
      disablePrimaryButton={!isCopilot && !isAuto && selectedDb === undefined}
      destroyOnHidden
    >
      <AgentModeGroup>
        <div style={{ marginBottom: 8 }}>{t('辅助类型')}</div>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          size="small"
          options={AGENT_MODES}
          value={agentType}
          onChange={(e: RadioChangeEvent) => setAgentType(e.target.value)}
        />
      </AgentModeGroup>
      <div style={{ marginBottom: 8 }}>
        {t('选择数据库')}
        {!isCopilot && ' *'}
      </div>
      <Select
        style={{ width: '100%' }}
        placeholder={t('请选择数据库...')}
        options={databaseOptions}
        value={selectedDb}
        onChange={value => setSelectedDb(value as number)}
      />
    </Modal>
  );
}
