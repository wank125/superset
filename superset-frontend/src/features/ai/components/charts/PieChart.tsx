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

interface PieChartProps {
  data: InlineChartData;
}

const Container = styled.div`
  margin: 8px 0;
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: 8px;
  overflow: hidden;
`;

export function PieChart({ data }: PieChartProps) {
  const { categoryCols, metricCols, rows } = data;

  const option = useMemo(() => {
    const catCol = categoryCols[0];
    const metricCol = metricCols[0];
    if (!catCol || !metricCol) return null;

    const pieData = rows.map(r => ({
      name: String(r[catCol] ?? ''),
      value: Number(r[metricCol]) || 0,
    }));

    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: {
        orient: 'vertical',
        right: 10,
        top: 'center',
        textStyle: { fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['40%', '50%'],
          avoidLabelOverlap: true,
          label: { show: false },
          emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } },
          data: pieData,
        },
      ],
    };
  }, [categoryCols, metricCols, rows]);

  const { chartRef, height } = useECharts(option);

  if (!option) return null;

  return (
    <Container>
      <div ref={chartRef} style={{ height }} />
    </Container>
  );
}
