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

import { adaptQueryResult, inferChartType } from '../chatMsgAdapter';
import type { SqlQueryResult } from '../../types';

/**
 * Integration tests: verify end-to-end data flow
 * Backend SqlQueryResult → adaptQueryResult → inferChartType
 */

// Simulates a backend data_analyzed event payload for a KPI query
const kpiEventPayload: SqlQueryResult = {
  columns: [
    { name: 'gmv', type: 'FLOAT' },
  ],
  rows: [{ gmv: 158000.5 }],
  row_count: 1,
  insight: 'GMV 同比增长 12%',
  statistics: { '环比': '+5.2%', '同比': '+12.0%' },
};

// Simulates a trend query result
const trendEventPayload: SqlQueryResult = {
  columns: [
    { name: 'order_date', type: 'DATETIME', is_dttm: true },
    { name: 'daily_revenue', type: 'FLOAT' },
    { name: 'order_count', type: 'INTEGER' },
  ],
  rows: Array.from({ length: 30 }, (_, i) => ({
    order_date: `2025-01-${String(i + 1).padStart(2, '0')}`,
    daily_revenue: 1000 + i * 50,
    order_count: 20 + i,
  })),
  row_count: 30,
};

// Simulates a category breakdown result
const categoryEventPayload: SqlQueryResult = {
  columns: [
    { name: 'channel', type: 'STRING' },
    { name: 'sales', type: 'INTEGER' },
  ],
  rows: [
    { channel: '线上', sales: 5000 },
    { channel: '线下', sales: 3000 },
    { channel: '分销', sales: 2000 },
    { channel: '直营', sales: 4500 },
  ],
  row_count: 4,
};

test('KPI flow: payload → data → chart type = kpi', () => {
  const data = adaptQueryResult(kpiEventPayload);
  expect(inferChartType(data)).toBe('kpi');
  expect(data.metricCols).toEqual(['gmv']);
  expect(data.statistics).toEqual({ '环比': '+5.2%', '同比': '+12.0%' });
  expect(data.rows[0].gmv).toBe(158000.5);
});

test('Trend flow: payload → data → chart type = trend', () => {
  const data = adaptQueryResult(trendEventPayload);
  expect(inferChartType(data)).toBe('trend');
  expect(data.dateCol).toBe('order_date');
  expect(data.metricCols).toEqual(['daily_revenue', 'order_count']);
  expect(data.rows).toHaveLength(30);
});

test('Category flow: payload → data → chart type = pie', () => {
  const data = adaptQueryResult(categoryEventPayload);
  expect(inferChartType(data)).toBe('pie');
  expect(data.categoryCols).toEqual(['channel']);
  expect(data.metricCols).toEqual(['sales']);
});

test('Statistics pass through adapter', () => {
  const data = adaptQueryResult(kpiEventPayload);
  expect(data.statistics).toBeDefined();
  expect(data.statistics!['环比']).toBe('+5.2%');
});

test('No statistics when not provided', () => {
  const data = adaptQueryResult(trendEventPayload);
  expect(data.statistics).toBeUndefined();
});

test('Full pipeline: multiple chart types from different payloads', () => {
  const payloads = [kpiEventPayload, trendEventPayload, categoryEventPayload];
  const types = payloads.map(p => inferChartType(adaptQueryResult(p)));
  expect(types).toEqual(['kpi', 'trend', 'pie']);
});
