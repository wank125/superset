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
import { SupersetClient } from '@superset-ui/core';
import type { AiAlertConfigResponse } from '../types';

interface AlertConfigCardProps {
  config: AiAlertConfigResponse;
  databaseId: number;
  onCreated?: (alertId: number) => void;
}

const Card = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 8px;
  overflow: hidden;
`;

const CardHeader = styled.div`
  padding: 10px 14px;
  background: ${({ theme }) => theme.colorPrimaryBg};
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
`;

const CardBody = styled.div`
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.8;
`;

const Label = styled.span`
  color: ${({ theme }) => theme.colorTextSecondary};
  margin-right: 4px;
`;

const SqlBlock = styled.pre`
  margin: 6px 0;
  padding: 8px;
  background: ${({ theme }) => theme.colorBgLayout};
  border-radius: 4px;
  font-size: 11px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
`;

const CreateButton = styled.button<{ loading: boolean }>`
  margin-top: 10px;
  padding: 6px 16px;
  background: ${({ loading, theme }) =>
    loading ? theme.colorBgLayout : theme.colorPrimary};
  color: ${({ loading, theme }) =>
    loading ? theme.colorTextDisabled : theme.colorWhite};
  border: none;
  border-radius: 4px;
  cursor: ${({ loading }) => (loading ? 'not-allowed' : 'pointer')};
  font-size: 12px;
`;

const SuccessMsg = styled.div`
  margin-top: 8px;
  padding: 6px 10px;
  background: ${({ theme }) => theme.colorSuccessBg};
  color: ${({ theme }) => theme.colorSuccess};
  border-radius: 4px;
  font-size: 12px;
`;

const ErrorMsg = styled.div`
  margin-top: 8px;
  padding: 6px 10px;
  background: ${({ theme }) => theme.colorErrorBg};
  color: ${({ theme }) => theme.colorError};
  border-radius: 4px;
  font-size: 12px;
`;

/**
 * Displays AI-generated alert config with a "Create Alert" button.
 */
export function AlertConfigCard({
  config,
  databaseId,
  onCreated,
}: AlertConfigCardProps) {
  const [creating, setCreating] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const payload = {
        type: 'Alert',
        name: config.name,
        description: config.description,
        sql: config.sql,
        database: databaseId,
        validator_type: config.validator_type,
        validator_config_json: config.validator_config_json,
        crontab: config.crontab,
        active: true,
        recipients: [],
      };
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/report/',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const id = (json as Record<string, unknown>).id as number;
      setSuccess(true);
      onCreated?.(id);
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <span role="img" aria-label="alert">
          🔔
        </span>
        {t('Generated Alert Rule')}
      </CardHeader>
      <CardBody>
        <div>
          <Label>{t('Name')}:</Label>
          {config.name}
        </div>
        <div>
          <Label>{t('Description')}:</Label>
          {config.description}
        </div>
        <div>
          <Label>{t('Validator')}:</Label>
          {config.validator_type}
          {config.validator_type === 'operator' &&
            ` (${(config.validator_config_json as Record<string, unknown>)?.op} ${(config.validator_config_json as Record<string, unknown>)?.threshold})`}
          {config.validator_type === 'AI' &&
            ` — ${(config.validator_config_json as Record<string, unknown>)?.prompt}`}
        </div>
        <div>
          <Label>{t('Schedule')}:</Label>
          {config.crontab}
        </div>
        <div>
          <Label>SQL:</Label>
          <SqlBlock>{config.sql}</SqlBlock>
        </div>
        {!success && (
          <CreateButton onClick={handleCreate} loading={creating}>
            {creating ? t('Creating...') : t('Create Alert')}
          </CreateButton>
        )}
        {success && (
          <SuccessMsg>
            {t('Alert created successfully! View in ')}
            <a href="/alert/list/" target="_blank" rel="noopener noreferrer">
              {t('Alert Management')}
            </a>
          </SuccessMsg>
        )}
        {error && <ErrorMsg>{error}</ErrorMsg>}
      </CardBody>
    </Card>
  );
}
