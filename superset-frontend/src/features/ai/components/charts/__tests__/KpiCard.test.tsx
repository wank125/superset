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

import { render, screen } from 'spec/helpers/testing-library';
import { KpiCard } from '../KpiCard';
import type { InlineChartData } from '../../../utils/chatMsgAdapter';

const makeData = (
  overrides: Partial<InlineChartData> = {},
): InlineChartData => ({
  columns: [
    { name: 'total_sales', showType: 'NUMBER' },
  ],
  rows: [{ total_sales: 12345.67 }],
  categoryCols: [],
  metricCols: ['total_sales'],
  ...overrides,
});

test('renders metric value with K suffix', () => {
  render(<KpiCard data={makeData()} />);
  expect(screen.getByText('12.3K')).toBeInTheDocument();
});

test('renders M suffix for millions', () => {
  render(<KpiCard data={makeData({
    rows: [{ total_sales: 2500000 }],
  })} />);
  expect(screen.getByText('2.5M')).toBeInTheDocument();
});

test('renders insight text', () => {
  render(<KpiCard data={makeData()} insight="Sales are strong" />);
  expect(screen.getByText('Sales are strong')).toBeInTheDocument();
});

test('renders period comparison statistics', () => {
  render(
    <KpiCard
      data={makeData({
        statistics: { '环比': '+5.2%', '同比': '+12.3%' },
      })}
    />,
  );
  expect(screen.getByText('环比')).toBeInTheDocument();
  expect(screen.getByText('+5.2%')).toBeInTheDocument();
  expect(screen.getByText('同比')).toBeInTheDocument();
  expect(screen.getByText('+12.3%')).toBeInTheDocument();
});

test('renders nothing when no rows', () => {
  const { container } = render(
    <KpiCard data={makeData({ rows: [] })} />,
  );
  expect(container.innerHTML).toBe('');
});

test('renders multiple metrics', () => {
  render(
    <KpiCard
      data={makeData({
        columns: [
          { name: 'revenue', showType: 'NUMBER' },
          { name: 'orders', showType: 'NUMBER' },
        ],
        metricCols: ['revenue', 'orders'],
        rows: [{ revenue: 9999, orders: 42 }],
      })}
    />,
  );
  expect(screen.getByText('10.0K')).toBeInTheDocument();
  expect(screen.getByText('42')).toBeInTheDocument();
});
