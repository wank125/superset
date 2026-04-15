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

import { render, screen, fireEvent } from 'spec/helpers/testing-library';
import { AiInlineChart } from '../AiInlineChart';
import type { SqlQueryResult } from '../../types';

// Mock ECharts since it needs a real DOM with dimensions
jest.mock('../../utils/useECharts', () => ({
  useECharts: () => ({
    chartRef: { current: document.createElement('div') },
    height: 260,
  }),
}));

const kpiResult: SqlQueryResult = {
  columns: [{ name: 'total', type: 'FLOAT' }],
  rows: [{ total: 9999 }],
  row_count: 1,
};

const trendResult: SqlQueryResult = {
  columns: [
    { name: 'date', type: 'DATETIME', is_dttm: true },
    { name: 'value', type: 'FLOAT' },
  ],
  rows: [
    { date: '2025-01', value: 100 },
    { date: '2025-02', value: 200 },
  ],
  row_count: 2,
};

const tableResult: SqlQueryResult = {
  columns: [
    { name: 'region', type: 'STRING' },
    { name: 'sales', type: 'INTEGER' },
  ],
  rows: Array.from({ length: 60 }, (_, i) => ({
    region: `Region_${i}`,
    sales: i * 100,
  })),
  row_count: 60,
};

const emptyResult: SqlQueryResult = {
  columns: [],
  rows: [],
  row_count: 0,
};

test('renders empty state for empty rows', () => {
  render(<AiInlineChart result={emptyResult} />);
  expect(screen.getByText('No data returned')).toBeInTheDocument();
});

test('renders KPI card for single-row single-metric result', () => {
  render(<AiInlineChart result={kpiResult} insight="Sales are up" />);
  // KPI card renders insight internally
  expect(screen.getByText('Sales are up')).toBeInTheDocument();
});

test('renders trend chart for date + metric result', () => {
  const { container } = render(<AiInlineChart result={trendResult} />);
  // Trend chart renders a div with ref for ECharts
  expect(container.querySelector('div')).toBeTruthy();
});

test('renders table for large category result', () => {
  render(<AiInlineChart result={tableResult} />);
  // Should render table headers
  expect(screen.getByText('region')).toBeInTheDocument();
  expect(screen.getByText('sales')).toBeInTheDocument();
  // Should show truncation message
  expect(screen.getByText(/Showing 50 of 60/)).toBeInTheDocument();
});

test('renders insight text for non-kpi charts', () => {
  render(
    <AiInlineChart result={trendResult} insight="Revenue is growing" />,
  );
  expect(screen.getByText('Revenue is growing')).toBeInTheDocument();
});

test('renders suggested questions', () => {
  const onSelect = jest.fn();
  render(
    <AiInlineChart
      result={trendResult}
      suggestQuestions={['Show breakdown', 'Compare last year']}
      onSuggestQuestion={onSelect}
    />,
  );
  const btn = screen.getByText('Show breakdown');
  expect(btn).toBeInTheDocument();
  fireEvent.click(btn);
  expect(onSelect).toHaveBeenCalledWith('Show breakdown');
});

test('passes onDrillDown to DataTable', () => {
  const onSelect = jest.fn();
  render(
    <AiInlineChart
      result={tableResult}
      onSuggestQuestion={onSelect}
    />,
  );
  // Click a category cell to trigger drill-down
  const drillBtn = screen.getByText('Region_0');
  fireEvent.click(drillBtn);
  expect(onSelect).toHaveBeenCalledWith('按 region 拆分');
});
