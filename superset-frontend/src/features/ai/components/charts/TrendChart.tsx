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
import type { InlineChartData } from '../../utils/chatMsgAdapter';
import { useECharts } from '../../utils/useECharts';

interface TrendChartProps {
  data: InlineChartData;
}

const Container = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 8px;
  overflow: hidden;
`;

export function TrendChart({ data }: TrendChartProps) {
  const { dateCol, metricCols, rows } = data;

  const option = useMemo(() => {
    if (!dateCol || metricCols.length === 0) return null;

    const xData = rows.map(r => String(r[dateCol] ?? ''));
    const series = metricCols.map(col => ({
      name: col,
      type: 'line' as const,
      smooth: true,
      data: rows.map(r => {
        const v = Number(r[col]);
        return Number.isNaN(v) ? null : v;
      }),
    }));

    return {
      tooltip: { trigger: 'axis' },
      legend:
        metricCols.length > 1
          ? { bottom: 0, textStyle: { fontSize: 11 } }
          : undefined,
      grid: {
        left: 50,
        right: 20,
        top: 16,
        bottom: metricCols.length > 1 ? 40 : 30,
      },
      xAxis: { type: 'category', data: xData, axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
      series,
    };
  }, [dateCol, metricCols, rows]);

  const { chartRef, height } = useECharts(option);

  if (!option) return null;

  return (
    <Container>
      <div ref={chartRef} style={{ height }} />
    </Container>
  );
}
