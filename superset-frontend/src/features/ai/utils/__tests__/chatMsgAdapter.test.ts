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

const singleMetricResult: SqlQueryResult = {
  columns: [
    { name: 'total_sales', type: 'FLOAT' },
  ],
  rows: [{ total_sales: 12345.67 }],
  row_count: 1,
};

const trendResult: SqlQueryResult = {
  columns: [
    { name: 'order_date', type: 'DATETIME', is_dttm: true },
    { name: 'revenue', type: 'FLOAT' },
  ],
  rows: [
    { order_date: '2025-01-01', revenue: 100 },
    { order_date: '2025-01-02', revenue: 200 },
  ],
  row_count: 2,
};

const categoryResult: SqlQueryResult = {
  columns: [
    { name: 'region', type: 'STRING' },
    { name: 'sales', type: 'INTEGER' },
  ],
  rows: [
    { region: 'North', sales: 100 },
    { region: 'South', sales: 200 },
    { region: 'East', sales: 150 },
  ],
  row_count: 3,
};

const manyCategoryResult: SqlQueryResult = {
  columns: [
    { name: 'city', type: 'STRING' },
    { name: 'population', type: 'INTEGER' },
  ],
  rows: Array.from({ length: 25 }, (_, i) => ({
    city: `City_${i}`,
    population: i * 1000,
  })),
  row_count: 25,
};

test('adaptQueryResult classifies columns by showType', () => {
  const data = adaptQueryResult(trendResult);
  expect(data.dateCol).toBe('order_date');
  expect(data.categoryCols).toEqual([]);
  expect(data.metricCols).toEqual(['revenue']);
  expect(data.columns[0].showType).toBe('DATE');
  expect(data.columns[1].showType).toBe('NUMBER');
});

test('adaptQueryResult identifies category columns', () => {
  const data = adaptQueryResult(categoryResult);
  expect(data.dateCol).toBeUndefined();
  expect(data.categoryCols).toEqual(['region']);
  expect(data.metricCols).toEqual(['sales']);
});

test('inferChartType returns kpi for single row + single metric', () => {
  const data = adaptQueryResult(singleMetricResult);
  expect(inferChartType(data)).toBe('kpi');
});

test('inferChartType returns trend for date + metric', () => {
  const data = adaptQueryResult(trendResult);
  expect(inferChartType(data)).toBe('trend');
});

test('inferChartType returns pie for few category rows', () => {
  const data = adaptQueryResult(categoryResult);
  expect(inferChartType(data)).toBe('pie');
});

test('inferChartType returns bar for medium category rows', () => {
  const mediumResult: SqlQueryResult = {
    columns: categoryResult.columns,
    rows: Array.from({ length: 15 }, (_, i) => ({
      region: `R${i}`,
      sales: i * 10,
    })),
    row_count: 15,
  };
  const data = adaptQueryResult(mediumResult);
  expect(inferChartType(data)).toBe('bar');
});

test('inferChartType returns table for many rows', () => {
  const data = adaptQueryResult(manyCategoryResult);
  expect(inferChartType(data)).toBe('table');
});

test('adaptQueryResult preserves rows', () => {
  const data = adaptQueryResult(categoryResult);
  expect(data.rows).toHaveLength(3);
  expect(data.rows[0]).toEqual({ region: 'North', sales: 100 });
});

test('adaptQueryResult handles INTEGER type as NUMBER', () => {
  const data = adaptQueryResult(categoryResult);
  const salesCol = data.columns.find(c => c.name === 'sales');
  expect(salesCol?.showType).toBe('NUMBER');
});

test('adaptQueryResult handles is_dttm flag', () => {
  const result: SqlQueryResult = {
    columns: [
      { name: 'created_at', type: 'STRING', is_dttm: true },
      { name: 'count', type: 'INTEGER' },
    ],
    rows: [{ created_at: '2025-01', count: 5 }],
    row_count: 1,
  };
  const data = adaptQueryResult(result);
  expect(data.dateCol).toBe('created_at');
});
