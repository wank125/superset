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

import { useMemo } from 'react';
import { styled } from '@superset-ui/core';
import type { SqlQueryResult } from '../types';
import {
  adaptQueryResult,
  inferChartType,
  type InlineChartData,
} from '../utils/chatMsgAdapter';
import { KpiCard } from './charts/KpiCard';
import { TrendChart } from './charts/TrendChart';
import { BarChart } from './charts/BarChart';
import { PieChart } from './charts/PieChart';
import { DataTable } from './charts/DataTable';
import { SuggestQuestions } from './SuggestQuestions';

interface AiInlineChartProps {
  /** The raw SQL result from the AI agent. */
  result: SqlQueryResult;
  /** Optional insight text from the LLM. */
  insight?: string;
  /** Suggested follow-up questions. */
  suggestQuestions?: string[];
  /** Callback when user clicks a suggested question. */
  onSuggestQuestion?: (q: string) => void;
}

const EmptyBox = styled.div`
  text-align: center;
  padding: 24px;
  color: ${({ theme }) => theme.colorTextSecondary};
  font-size: 13px;
`;

const InsightText = styled.div`
  font-size: 12px;
  color: ${({ theme }) => theme.colorTextSecondary};
  padding: 4px 0;
`;

/**
 * Auto-detects the best chart type for SQL query results and renders
 * an inline chart with optional insight and suggested follow-ups.
 */
export function AiInlineChart({
  result,
  insight,
  suggestQuestions,
  onSuggestQuestion,
}: AiInlineChartProps) {
  const data: InlineChartData = useMemo(
    () => adaptQueryResult(result),
    [result],
  );

  const chartType = useMemo(() => inferChartType(data), [data]);

  if (!data.rows.length) {
    return <EmptyBox>No data returned</EmptyBox>;
  }

  return (
    <div>
      {chartType === 'kpi' && <KpiCard data={data} insight={insight} />}
      {chartType === 'trend' && <TrendChart data={data} />}
      {chartType === 'bar' && <BarChart data={data} />}
      {chartType === 'pie' && <PieChart data={data} />}
      {chartType === 'table' && (
        <DataTable
          data={data}
          onDrillDown={dim => onSuggestQuestion?.(`按 ${dim} 拆分`)}
        />
      )}
      {chartType !== 'kpi' && insight && (
        <InsightText>{insight}</InsightText>
      )}
      {suggestQuestions && suggestQuestions.length > 0 && onSuggestQuestion && (
        <SuggestQuestions
          questions={suggestQuestions}
          onSelect={onSuggestQuestion}
        />
      )}
    </div>
  );
}
