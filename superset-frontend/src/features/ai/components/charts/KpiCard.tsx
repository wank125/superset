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
import type { InlineChartData } from '../../utils/chatMsgAdapter';
import { PeriodCompareItem } from './PeriodCompareItem';

interface KpiCardProps {
  data: InlineChartData;
  insight?: string;
}

const Card = styled.div`
  background: ${({ theme }) => theme.colorBgContainer};
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 8px;
  padding: 16px;
  margin: 8px 0;
`;

const MetricRow = styled.div`
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
`;

const MetricItem = styled.div`
  min-width: 120px;
`;

const MetricLabel = styled.div`
  font-size: 12px;
  color: ${({ theme }) => theme.colorTextSecondary};
  margin-bottom: 4px;
`;

const MetricValue = styled.div`
  font-size: 28px;
  font-weight: ${({ theme }) => theme.fontWeightStrong};
  color: ${({ theme }) => theme.colorText};
  line-height: 1.2;
`;

const InsightText = styled.div`
  margin-top: 12px;
  font-size: 12px;
  color: ${({ theme }) => theme.colorTextSecondary};
  line-height: 1.5;
  padding-top: 8px;
  border-top: 1px solid ${({ theme }) => theme.colorBorderSecondary};
`;

const CompareRow = styled.div`
  display: flex;
  gap: 24px;
  margin-top: 12px;
  overflow-x: auto;
`;

function formatValue(val: unknown): string {
  if (val == null) return '-';
  const num = Number(val);
  if (Number.isNaN(num)) return String(val);
  // Format large numbers with K/M suffix
  if (Math.abs(num) >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (Math.abs(num) >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num % 1 === 0 ? num.toFixed(0) : num.toFixed(2);
}

export function KpiCard({ data, insight }: KpiCardProps) {
  const row = data.rows[0];
  if (!row) return null;

  return (
    <Card>
      <MetricRow>
        {data.metricCols.map(col => (
          <MetricItem key={col}>
            <MetricLabel>{col}</MetricLabel>
            <MetricValue>{formatValue(row[col])}</MetricValue>
          </MetricItem>
        ))}
      </MetricRow>
      {data.categoryCols.map(col => (
        <MetricLabel key={col} style={{ marginTop: 8 }}>
          {col}: {String(row[col] ?? '-')}
        </MetricLabel>
      ))}
      {data.statistics && Object.keys(data.statistics).length > 0 && (
        <CompareRow>
          {Object.entries(data.statistics).map(([title, value]) => (
            <PeriodCompareItem key={title} title={title} value={value} />
          ))}
        </CompareRow>
      )}
      {insight && <InsightText>{insight}</InsightText>}
    </Card>
  );
}
